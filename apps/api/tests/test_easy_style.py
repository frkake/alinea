"""M3-S11: やさしい訳スタイルのオンデマンド生成。

- 初回 `POST /api/revisions/{revision_id}/translations {style:"easy"}` は 202 を返し、
  TranslationSet(style=easy)+ セクション単位の `translation` ジョブ群(`reason='easy'`)
  を作成する。
- セットが既に `complete` なら 2 回目以降は 200 `{job_id: null}` で即時応答する。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.services.session_service import COOKIE_NAME, create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import DocumentRevision, Job, Paper, TranslationSet
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.jobs.store import JobStore
from alinea_core.search.rebuild import rebuild_block_search_index
from factories import (
    make_paper,
    make_translation_set,
    make_translation_unit,
    make_user,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _p(block_id: str, text: str) -> Block:
    return Block(id=block_id, type="paragraph", inlines=[Inline(t="text", v=text)])


def _make_document() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    _p("blk-a", "Rectified flow straightens the transport map."),
                    _p("blk-b", "The model learns a velocity field over time."),
                ],
            ),
            Section(
                id="sec-2",
                heading=SectionHeading(number="2", title="Method"),
                blocks=[
                    _p("blk-c", "We evaluate on standard image generation benchmarks."),
                ],
            ),
        ],
    )


async def _make_revision(
    db: AsyncSession, *, paper: Paper, content: DocumentContent
) -> DocumentRevision:
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=str(paper.id),
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(),
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    await rebuild_block_search_index(db, str(revision.id), content)
    return revision


@pytest_asyncio.fixture
async def ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[SimpleNamespace]:
    user = await make_user(db_session, email=f"s11-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, owner=user, visibility="private")
    content = _make_document()
    revision = await _make_revision(db_session, paper=paper, content=content)
    await db_session.commit()
    user_id = str(user.id)

    token = await create_session(redis_client, user_id)
    client.cookies.set(COOKIE_NAME, token)
    try:
        yield SimpleNamespace(user=user, user_id=user_id, paper=paper, revision=revision)
    finally:
        await db_session.commit()
        await purge_user(db_session, user_id)
        await db_session.commit()


async def _jobs_for_set(db: AsyncSession, set_id: str) -> list[Job]:
    rows = (
        (
            await db.execute(
                select(Job)
                .where(Job.kind == "translation", Job.payload["set_id"].astext == set_id)
                .order_by(Job.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def test_easy_translation_creates_set_and_prioritizes_open_section(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    resp = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "easy", "priority_section_id": "sec-2"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    set_id = body["set_id"]
    assert body["job_id"] is not None

    tset = await db_session.get(TranslationSet, set_id)
    assert tset is not None
    assert tset.style == "easy"
    assert tset.scope == "personal"  # private 論文
    assert str(tset.user_id) == ctx.user_id

    jobs = await _jobs_for_set(db_session, set_id)
    assert len(jobs) == 2  # sec-1 / sec-2
    by_section = {j.payload["section_id"]: j for j in jobs}
    assert set(by_section) == {"sec-1", "sec-2"}
    assert by_section["sec-1"].payload["reason"] == "easy"
    assert by_section["sec-1"].payload["block_ids"] == ["blk-a", "blk-b"]
    assert by_section["sec-2"].payload["block_ids"] == ["blk-c"]
    assert by_section["sec-1"].payload["generation"] == 0
    assert by_section["sec-2"].payload["generation"] == 0
    assert by_section["sec-1"].payload["request_key"]
    assert by_section["sec-2"].payload["request_key"]

    assert by_section["sec-2"].priority == 100
    assert by_section["sec-1"].priority == 0
    assert str(by_section["sec-2"].id) == body["job_id"]


async def test_easy_translation_resend_is_idempotent(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    r1 = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "easy", "priority_section_id": "sec-1"},
    )
    assert r1.status_code == 202, r1.text
    set_id = r1.json()["set_id"]

    r2 = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "easy", "priority_section_id": "sec-1"},
    )
    assert r2.status_code == 202, r2.text
    assert r2.json()["set_id"] == set_id
    assert r2.json()["job_id"] == r1.json()["job_id"]

    jobs = await _jobs_for_set(db_session, set_id)
    assert len(jobs) == 2


async def test_easy_translation_already_complete_is_immediate(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    complete_set = await make_translation_set(
        db_session,
        revision=ctx.revision,
        style="easy",
        scope="personal",
        user=ctx.user,
        status="complete",
    )
    for block_id in ("blk-a", "blk-b", "blk-c"):
        await make_translation_unit(
            db_session,
            translation_set=complete_set,
            block_id=block_id,
            text_ja=f"translated {block_id}",
        )
    await db_session.commit()

    resp = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "easy"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["set_id"] == str(complete_set.id)
    assert body["job_id"] is None

    jobs = await _jobs_for_set(db_session, str(complete_set.id))
    assert jobs == []


async def test_easy_translation_shared_for_public_paper(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> None:
    user = await make_user(db_session, email=f"s11-pub-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, visibility="public")
    content = _make_document()
    revision = await _make_revision(db_session, paper=paper, content=content)
    await db_session.commit()
    user_id = str(user.id)

    token = await create_session(redis_client, user_id)
    client.cookies.set(COOKIE_NAME, token)
    try:
        resp = await client.post(
            f"/api/revisions/{revision.id}/translations",
            json={"style": "easy"},
        )
        assert resp.status_code == 202, resp.text
        set_id = resp.json()["set_id"]
        tset = await db_session.get(TranslationSet, set_id)
        assert tset is not None
        assert tset.scope == "shared"
        assert tset.user_id is None
    finally:
        await db_session.commit()
        await purge_user(db_session, user_id)
        await db_session.commit()


async def test_easy_new_set_rolls_back_when_enqueue_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    ctx: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_enqueue(_store: JobStore, **_kwargs: Any) -> str:
        raise RuntimeError("initial easy enqueue failed")

    monkeypatch.setattr(JobStore, "enqueue_uncommitted", fail_enqueue)
    with pytest.raises(RuntimeError, match="initial easy enqueue failed"):
        await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "easy"},
        )

    tset = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == ctx.revision.id,
            TranslationSet.style == "easy",
        )
    )
    assert tset is None
    jobs = list(
        (
            await db_session.execute(
                select(Job).where(Job.paper_id == ctx.paper.id, Job.kind == "translation")
            )
        )
        .scalars()
        .all()
    )
    assert jobs == []
