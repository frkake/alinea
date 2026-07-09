"""M2-15: 直訳スタイルのオンデマンド生成(PY-TR-08。plans/03 §7.3、plans/06 §5.2・§10.2)。

- 初回 `POST /api/revisions/{revision_id}/translations {style:"literal"}` は 202 を返し、
  TranslationSet(style=literal)+ セクション単位の `translation` ジョブ群(`reason='literal'`)
  を作成する。`priority_section_id` のセクションだけ優先度(§3.1 のオンデマンド既定
  `priority=100`)が高い。
- 同一 `priority_section_id` での再送は冪等(同じ job_id を返す。§3.1 の冪等キー
  `xlate:{set_id}:{section_id}`)。
- セットが既に `complete` なら 2 回目以降は 200 `{job_id: null}` で即時応答する(§10.2 手順1)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from alinea_api.services.session_service import COOKIE_NAME, create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import DocumentRevision, Job, Paper, TranslationSet
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.search.rebuild import rebuild_block_search_index
from factories import (
    make_paper,
    make_translation_set,
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
    user = await make_user(db_session, email=f"tr8-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, owner=user, visibility="private")
    content = _make_document()
    revision = await _make_revision(db_session, paper=paper, content=content)
    await db_session.commit()
    user_id = str(user.id)  # rollback 後の属性アクセス(greenlet 事故)を避けるため先に確定

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


async def test_literal_translation_creates_set_and_prioritizes_open_section(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    resp = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal", "priority_section_id": "sec-2"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    set_id = body["set_id"]
    assert body["job_id"] is not None

    tset = await db_session.get(TranslationSet, set_id)
    assert tset is not None
    assert tset.style == "literal"
    assert tset.scope == "personal"  # private 論文(§9.2)
    assert str(tset.user_id) == ctx.user_id

    jobs = await _jobs_for_set(db_session, set_id)
    assert len(jobs) == 2  # sec-1 / sec-2
    by_section = {j.payload["section_id"]: j for j in jobs}
    assert set(by_section) == {"sec-1", "sec-2"}
    assert by_section["sec-1"].payload["reason"] == "literal"
    assert by_section["sec-1"].payload["block_ids"] == ["blk-a", "blk-b"]
    assert by_section["sec-2"].payload["block_ids"] == ["blk-c"]

    # 表示中セクション(sec-2)だけ優先度が高い(plans/06 §3.1 のオンデマンド既定 100)。
    assert by_section["sec-2"].priority == 100
    assert by_section["sec-1"].priority == 0
    assert str(by_section["sec-2"].id) == body["job_id"]


async def test_literal_translation_resend_is_idempotent(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    r1 = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal", "priority_section_id": "sec-1"},
    )
    assert r1.status_code == 202, r1.text
    set_id = r1.json()["set_id"]

    r2 = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal", "priority_section_id": "sec-1"},
    )
    assert r2.status_code == 202, r2.text
    assert r2.json()["set_id"] == set_id
    assert r2.json()["job_id"] == r1.json()["job_id"]

    jobs = await _jobs_for_set(db_session, set_id)
    assert len(jobs) == 2  # 冪等キーにより重複作成されない


async def test_literal_translation_already_complete_is_immediate(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    complete_set = await make_translation_set(
        db_session,
        revision=ctx.revision,
        style="literal",
        scope="personal",
        user=ctx.user,
        status="complete",
    )
    await db_session.commit()

    resp = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["set_id"] == str(complete_set.id)
    assert body["job_id"] is None

    jobs = await _jobs_for_set(db_session, str(complete_set.id))
    assert jobs == []  # complete セットへの再要求はジョブを作らない


async def test_literal_translation_shared_for_public_paper(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> None:
    user = await make_user(db_session, email=f"tr8-pub-{uuid.uuid4().hex}@example.com")
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
            json={"style": "literal"},
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
