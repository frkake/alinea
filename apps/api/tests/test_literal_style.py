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

import asyncio
import datetime as dt
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.main import app
from alinea_api.routers.translations import get_translations_job_wakeup
from alinea_api.services.session_service import COOKIE_NAME, create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import DocumentRevision, Job, Paper, TranslationSet
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.jobs.store import JobStore
from alinea_core.search.rebuild import rebuild_block_search_index
from alinea_core.translation.pipeline import TranslationPlan, resolve_translation_plan
from factories import (
    make_paper,
    make_translation_set,
    make_translation_unit,
    make_user,
)
from httpx import ASGITransport, AsyncClient
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


def _make_document_with_appendix() -> DocumentContent:
    content = _make_document()
    content.sections.append(
        Section(
            id="sec-A",
            heading=SectionHeading(number="A", title="Details"),
            blocks=[_p("blk-app", "Additional derivations.")],
        )
    )
    return content


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
    assert by_section["sec-1"].payload["generation"] == 0
    assert by_section["sec-2"].payload["generation"] == 0
    assert by_section["sec-1"].payload["request_key"]
    assert by_section["sec-2"].payload["request_key"]

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


async def test_literal_queued_reuse_raises_priority_and_wakes_job(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    wakeups: list[tuple[str, str]] = []

    async def wakeup(job_id: str, queue_name: str) -> None:
        wakeups.append((job_id, queue_name))

    app.dependency_overrides[get_translations_job_wakeup] = lambda: wakeup
    try:
        initial = await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "literal"},
        )
        assert initial.status_code == 202, initial.text
        jobs = await _jobs_for_set(db_session, initial.json()["set_id"])
        sec_2 = next(job for job in jobs if job.payload["section_id"] == "sec-2")
        assert sec_2.priority == 0
        wakeups.clear()

        prioritized = await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "literal", "priority_section_id": "sec-2"},
        )

        assert prioritized.status_code == 202, prioritized.text
        assert prioritized.json()["job_id"] == str(sec_2.id)
        await db_session.refresh(sec_2)
        assert sec_2.priority == 100
        assert str(sec_2.id) in {job_id for job_id, _queue in wakeups}
    finally:
        app.dependency_overrides.pop(get_translations_job_wakeup, None)


async def test_literal_reuse_keeps_effective_interactive_queue(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    wakeups: list[tuple[str, str]] = []

    async def wakeup(job_id: str, queue_name: str) -> None:
        wakeups.append((job_id, queue_name))

    app.dependency_overrides[get_translations_job_wakeup] = lambda: wakeup
    try:
        initial = await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "literal", "priority_section_id": "sec-1"},
        )
        assert initial.status_code == 202, initial.text
        interactive_job_id = initial.json()["job_id"]
        interactive_job = await db_session.get(Job, interactive_job_id)
        assert interactive_job is not None
        assert interactive_job.priority == 100
        wakeups.clear()

        reused = await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "literal"},
        )

        assert reused.status_code == 202, reused.text
        assert (interactive_job_id, "alinea:interactive") in wakeups
        assert (interactive_job_id, "alinea:bulk") not in wakeups
        await db_session.refresh(interactive_job)
        assert interactive_job.priority == 100
    finally:
        app.dependency_overrides.pop(get_translations_job_wakeup, None)


@pytest.mark.parametrize("active_status", ["running", "waiting_quota"])
async def test_literal_nonqueued_active_reuse_does_not_duplicate_wakeup(
    active_status: str,
    client: AsyncClient,
    db_session: AsyncSession,
    ctx: SimpleNamespace,
) -> None:
    wakeups: list[tuple[str, str]] = []

    async def wakeup(job_id: str, queue_name: str) -> None:
        wakeups.append((job_id, queue_name))

    app.dependency_overrides[get_translations_job_wakeup] = lambda: wakeup
    try:
        initial = await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "literal"},
        )
        assert initial.status_code == 202, initial.text
        jobs = await _jobs_for_set(db_session, initial.json()["set_id"])
        for job in jobs:
            job.status = active_status
        await db_session.commit()
        wakeups.clear()

        reused = await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "literal", "priority_section_id": "sec-1"},
        )

        assert reused.status_code == 202, reused.text
        assert len(await _jobs_for_set(db_session, initial.json()["set_id"])) == 2
        assert wakeups == []
    finally:
        app.dependency_overrides.pop(get_translations_job_wakeup, None)


async def test_literal_terminal_work_advances_generation_repeatedly(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    first_response = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal", "priority_section_id": "sec-1"},
    )
    assert first_response.status_code == 202, first_response.text
    set_id = first_response.json()["set_id"]
    first_id = first_response.json()["job_id"]
    first = await db_session.get(Job, first_id)
    assert first is not None
    assert first.payload["generation"] == 0
    request_key = first.payload["request_key"]

    generated_ids = [first_id]
    for generation in (1, 2):
        current = await db_session.get(Job, generated_ids[-1])
        assert current is not None
        current.status = "failed"
        current.finished_at = dt.datetime.now(dt.UTC)
        await db_session.commit()

        response = await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "literal", "priority_section_id": "sec-1"},
        )
        assert response.status_code == 202, response.text
        generated_ids.append(response.json()["job_id"])
        job = await db_session.get(Job, generated_ids[-1])
        assert job is not None
        assert job.payload["request_key"] == request_key
        assert job.payload["generation"] == generation

    assert len(set(generated_ids)) == 3
    jobs = await _jobs_for_set(db_session, set_id)
    sec_1_jobs = [job for job in jobs if job.payload["section_id"] == "sec-1"]
    assert sorted(job.payload["generation"] for job in sec_1_jobs) == [0, 1, 2]


async def test_literal_new_set_rolls_back_when_enqueue_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    ctx: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_enqueue(_store: JobStore, **_kwargs: Any) -> str:
        raise RuntimeError("initial literal enqueue failed")

    monkeypatch.setattr(JobStore, "enqueue_uncommitted", fail_enqueue)
    with pytest.raises(RuntimeError, match="initial literal enqueue failed"):
        await client.post(
            f"/api/revisions/{ctx.revision.id}/translations",
            json={"style": "literal"},
        )

    tset = await db_session.scalar(
        select(TranslationSet).where(
            TranslationSet.revision_id == ctx.revision.id,
            TranslationSet.style == "literal",
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
        json={"style": "literal"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["set_id"] == str(complete_set.id)
    assert body["job_id"] is None

    jobs = await _jobs_for_set(db_session, str(complete_set.id))
    assert jobs == []  # complete セットへの再要求はジョブを作らない


@pytest.mark.parametrize(
    ("translate_table_cells", "raw", "expected_status"),
    [
        (
            True,
            "<table><tr><td>We improve image generation quality.</td><td>99.1</td></tr></table>",
            202,
        ),
        (False, "<table><tr><td>We improve image generation quality.</td></tr></table>", 200),
        (True, None, 200),
        (True, "<table><tr><td>99.1</td><td>$x^2$</td></tr></table>", 200),
    ],
)
async def test_literal_complete_set_requires_table_cells_only_for_supported_targets_when_enabled(
    translate_table_cells: bool,
    raw: str | None,
    expected_status: int,
    client: AsyncClient,
    db_session: AsyncSession,
    ctx: SimpleNamespace,
) -> None:
    content = _make_document()
    content.sections[1].blocks.append(
        Block(
            id="blk-table",
            type="table",
            raw=raw,
            caption=[Inline(t="text", v="Evaluation results")],
        )
    )
    ctx.revision.content = content.model_dump(mode="json")
    ctx.user.settings = {
        "translation": {"translate_table_cells": translate_table_cells},
    }
    complete_set = await make_translation_set(
        db_session,
        revision=ctx.revision,
        style="literal",
        scope="personal",
        user=ctx.user,
        status="complete",
    )
    complete_set.plan = TranslationPlan(
        include_appendix=True,
        translate_table_cells=translate_table_cells,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=["sec-1", "sec-2"],
        target_block_ids=["blk-a", "blk-b", "blk-c", "blk-table"],
        pages=None,
    ).model_dump(mode="json")
    for block_id in ("blk-a", "blk-b", "blk-c", "blk-table"):
        await make_translation_unit(
            db_session,
            translation_set=complete_set,
            block_id=block_id,
            text_ja=f"translated {block_id}",
        )
    await db_session.commit()

    response = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal"},
    )

    assert response.status_code == expected_status, response.text
    jobs = await _jobs_for_set(db_session, str(complete_set.id))
    if expected_status == 200:
        assert response.json()["job_id"] is None
        assert jobs == []
    else:
        assert response.json()["job_id"] is not None
        assert {job.payload["section_id"] for job in jobs} == {"sec-1", "sec-2"}


@pytest.mark.parametrize("incomplete_kind", ["missing", "blocking"])
async def test_literal_complete_with_incomplete_primary_reschedules(
    incomplete_kind: str,
    client: AsyncClient,
    db_session: AsyncSession,
    ctx: SimpleNamespace,
) -> None:
    complete_set = await make_translation_set(
        db_session,
        revision=ctx.revision,
        style="literal",
        scope="personal",
        user=ctx.user,
        status="complete",
    )
    for block_id in ("blk-a", "blk-b", "blk-c"):
        if incomplete_kind == "missing" and block_id == "blk-c":
            continue
        await make_translation_unit(
            db_session,
            translation_set=complete_set,
            block_id=block_id,
            text_ja=f"translated {block_id}",
            quality_flags=["provider_refusal"]
            if incomplete_kind == "blocking" and block_id == "blk-c"
            else [],
        )
    await db_session.commit()

    response = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal", "priority_section_id": "sec-2"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] is not None
    await db_session.refresh(complete_set)
    assert complete_set.status == "partial"
    jobs = await _jobs_for_set(db_session, str(complete_set.id))
    assert {job.payload["section_id"] for job in jobs} == {"sec-1", "sec-2"}


async def test_literal_empty_scope_completes_without_jobs_and_repairs_pending_set(
    client: AsyncClient,
    db_session: AsyncSession,
    ctx: SimpleNamespace,
) -> None:
    empty_content = DocumentContent(quality_level="A", sections=[])
    ctx.revision.content = empty_content.model_dump(mode="json")
    ctx.revision.stats = {"pages": 1}
    await db_session.commit()

    created = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal"},
    )

    assert created.status_code == 200, created.text
    assert created.json()["job_id"] is None
    tset = await db_session.get(TranslationSet, created.json()["set_id"])
    assert tset is not None
    assert tset.status == "complete"
    plan = resolve_translation_plan(empty_content, tset.plan, pages=1)
    assert plan.target_section_ids == []
    assert plan.target_block_ids == []
    assert await _jobs_for_set(db_session, str(tset.id)) == []

    tset.status = "pending"
    await db_session.commit()
    repaired = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal"},
    )

    assert repaired.status_code == 200, repaired.text
    assert repaired.json() == {"set_id": str(tset.id), "job_id": None}
    await db_session.refresh(tset)
    assert tset.status == "complete"
    assert await _jobs_for_set(db_session, str(tset.id)) == []


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


async def test_public_literal_active_job_is_reused_across_users(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> None:
    first_user = await make_user(
        db_session, email=f"tr8-shared-first-{uuid.uuid4().hex}@example.com"
    )
    second_user = await make_user(
        db_session, email=f"tr8-shared-second-{uuid.uuid4().hex}@example.com"
    )
    paper = await make_paper(db_session, visibility="public")
    revision = await _make_revision(db_session, paper=paper, content=_make_document())
    await db_session.commit()
    paper_id = str(paper.id)
    revision_id = str(revision.id)
    first_user_id = str(first_user.id)
    second_user_id = str(second_user.id)
    try:
        first_token = await create_session(redis_client, first_user_id)
        client.cookies.set(COOKIE_NAME, first_token)
        first = await client.post(
            f"/api/revisions/{revision_id}/translations",
            json={"style": "literal", "priority_section_id": "sec-1"},
        )
        assert first.status_code == 202, first.text

        second_token = await create_session(redis_client, second_user_id)
        client.cookies.set(COOKIE_NAME, second_token)
        second = await client.post(
            f"/api/revisions/{revision_id}/translations",
            json={"style": "literal", "priority_section_id": "sec-1"},
        )

        assert second.status_code == 202, second.text
        assert second.json()["set_id"] == first.json()["set_id"]
        assert second.json()["job_id"] == first.json()["job_id"]
        jobs = await _jobs_for_set(db_session, first.json()["set_id"])
        assert len(jobs) == 2
    finally:
        await db_session.rollback()
        persisted_paper = await db_session.get(Paper, paper_id)
        if persisted_paper is not None:
            await db_session.delete(persisted_paper)
            await db_session.commit()
        await purge_user(db_session, first_user_id)
        await purge_user(db_session, second_user_id)
        await db_session.commit()


async def test_concurrent_public_literal_creation_converges_on_one_set_and_jobs(
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    first_user = await make_user(db_session, email=f"tr8-race-first-{uuid.uuid4().hex}@example.com")
    second_user = await make_user(
        db_session, email=f"tr8-race-second-{uuid.uuid4().hex}@example.com"
    )
    paper = await make_paper(db_session, visibility="public")
    revision = await _make_revision(db_session, paper=paper, content=_make_document())
    await db_session.commit()
    paper_id = str(paper.id)
    revision_id = str(revision.id)
    first_user_id = str(first_user.id)
    second_user_id = str(second_user.id)
    first_token = await create_session(redis_client, first_user_id)
    second_token = await create_session(redis_client, second_user_id)
    first_client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    )
    second_client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    )
    first_client.cookies.set(COOKIE_NAME, first_token)
    second_client.cookies.set(COOKIE_NAME, second_token)
    try:
        first, second = await asyncio.gather(
            first_client.post(
                f"/api/revisions/{revision_id}/translations",
                json={"style": "literal", "priority_section_id": "sec-1"},
            ),
            second_client.post(
                f"/api/revisions/{revision_id}/translations",
                json={"style": "literal", "priority_section_id": "sec-1"},
            ),
        )

        assert first.status_code == second.status_code == 202
        assert first.json()["set_id"] == second.json()["set_id"]
        assert first.json()["job_id"] == second.json()["job_id"]
        jobs = await _jobs_for_set(db_session, first.json()["set_id"])
        assert len(jobs) == 2
    finally:
        await first_client.aclose()
        await second_client.aclose()
        await db_session.rollback()
        persisted_paper = await db_session.get(Paper, paper_id)
        if persisted_paper is not None:
            await db_session.delete(persisted_paper)
            await db_session.commit()
        await purge_user(db_session, first_user_id)
        await purge_user(db_session, second_user_id)
        await db_session.commit()


async def test_literal_translation_persists_user_plan_and_enqueues_only_its_targets(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    content = _make_document_with_appendix()
    ctx.revision.content = content.model_dump(mode="json")
    ctx.revision.stats = {"pages": 42}
    ctx.user.settings = {
        "translation": {
            "auto_translate_appendix": False,
            "translate_table_cells": False,
            "suggest_section_selection_over_30_pages": True,
        }
    }
    await db_session.commit()

    response = await client.post(
        f"/api/revisions/{ctx.revision.id}/translations",
        json={"style": "literal"},
    )

    assert response.status_code == 202, response.text
    tset = await db_session.get(TranslationSet, response.json()["set_id"])
    assert tset is not None and tset.plan is not None
    plan = resolve_translation_plan(content, tset.plan, pages=42)
    assert plan.include_appendix is False
    assert plan.translate_table_cells is False
    assert plan.suggest_section_selection_over_30_pages is True
    assert plan.pages == 42
    assert plan.target_section_ids == ["sec-1", "sec-2"]
    assert plan.target_block_ids == ["blk-a", "blk-b", "blk-c"]
    jobs = await _jobs_for_set(db_session, str(tset.id))
    assert {job.payload["section_id"] for job in jobs} == {"sec-1", "sec-2"}


async def test_shared_literal_plan_expands_then_never_shrinks(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> None:
    user = await make_user(db_session, email=f"tr8-merge-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, visibility="public")
    content = _make_document_with_appendix()
    revision = await _make_revision(db_session, paper=paper, content=content)
    revision.stats = {"pages": 42}
    user.settings = {"translation": {"auto_translate_appendix": False}}
    await db_session.commit()
    user_id = str(user.id)
    token = await create_session(redis_client, user_id)
    client.cookies.set(COOKIE_NAME, token)
    try:
        subset_response = await client.post(
            f"/api/revisions/{revision.id}/translations",
            json={"style": "literal"},
        )
        assert subset_response.status_code == 202, subset_response.text
        tset = await db_session.get(TranslationSet, subset_response.json()["set_id"])
        assert tset is not None and tset.plan is not None
        subset = resolve_translation_plan(content, tset.plan, pages=42)
        assert subset.target_section_ids == ["sec-1", "sec-2"]

        tset.status = "complete"
        user.settings = {}
        await db_session.commit()
        expanded_response = await client.post(
            f"/api/revisions/{revision.id}/translations",
            json={"style": "literal"},
        )
        assert expanded_response.status_code == 202, expanded_response.text
        await db_session.refresh(tset)
        expanded = resolve_translation_plan(content, tset.plan, pages=42)
        assert expanded.target_section_ids == ["sec-1", "sec-2", "sec-A"]
        assert expanded.include_appendix is True
        assert tset.status == "partial"
        jobs = await _jobs_for_set(db_session, str(tset.id))
        assert {job.payload["section_id"] for job in jobs} == {"sec-1", "sec-2", "sec-A"}

        for block_id in ("blk-a", "blk-b", "blk-c", "blk-app"):
            await make_translation_unit(
                db_session,
                translation_set=tset,
                block_id=block_id,
                text_ja=f"translated {block_id}",
            )
        tset.status = "complete"
        user.settings = {"translation": {"auto_translate_appendix": False}}
        await db_session.commit()
        not_shrunk_response = await client.post(
            f"/api/revisions/{revision.id}/translations",
            json={"style": "literal"},
        )
        assert not_shrunk_response.status_code == 200, not_shrunk_response.text
        await db_session.refresh(tset)
        not_shrunk = resolve_translation_plan(content, tset.plan, pages=42)
        assert not_shrunk.target_section_ids == ["sec-1", "sec-2", "sec-A"]
        assert not_shrunk.include_appendix is True
    finally:
        await db_session.commit()
        await purge_user(db_session, user_id)
        await db_session.commit()


async def test_shared_literal_plan_expansion_rolls_back_when_enqueue_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await make_user(db_session, email=f"tr8-atomic-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, visibility="public")
    content = _make_document_with_appendix()
    revision = await _make_revision(db_session, paper=paper, content=content)
    revision.stats = {"pages": 42}
    user.settings = {"translation": {"auto_translate_appendix": False}}
    await db_session.commit()
    user_id = str(user.id)
    token = await create_session(redis_client, user_id)
    client.cookies.set(COOKIE_NAME, token)
    try:
        initial_response = await client.post(
            f"/api/revisions/{revision.id}/translations",
            json={"style": "literal"},
        )
        assert initial_response.status_code == 202, initial_response.text
        tset = await db_session.get(TranslationSet, initial_response.json()["set_id"])
        assert tset is not None
        tset.status = "complete"
        user.settings = {}
        await db_session.commit()

        async def fail_enqueue(_store: JobStore, **_kwargs: Any) -> str:
            raise RuntimeError("literal enqueue failed")

        monkeypatch.setattr(JobStore, "enqueue_uncommitted", fail_enqueue)
        with pytest.raises(RuntimeError, match="literal enqueue failed"):
            await client.post(
                f"/api/revisions/{revision.id}/translations",
                json={"style": "literal"},
            )

        await db_session.refresh(tset)
        persisted = resolve_translation_plan(content, tset.plan, pages=42)
        assert persisted.target_section_ids == ["sec-1", "sec-2"]
        assert persisted.target_block_ids == ["blk-a", "blk-b", "blk-c"]
        assert tset.status == "complete"
        jobs = await _jobs_for_set(db_session, str(tset.id))
        assert {job.payload["section_id"] for job in jobs} == {"sec-1", "sec-2"}
    finally:
        await db_session.rollback()
        await purge_user(db_session, user_id)
        await db_session.commit()
