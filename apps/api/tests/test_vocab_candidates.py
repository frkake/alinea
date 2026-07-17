"""AI 単語抽出 API テスト(S7)。

docs/superpowers/specs/2026-07-16-ai-word-extraction-design.md。実 PostgreSQL。
本タスク所有ルータ(vocab_candidates)のみをマウントした専用アプリで検証する
(test_vocab.py と同方針)。LLM 呼び出しはワーカー側なので API テストは実 LLM に依存しない
(候補行は factories/直接 add で用意する)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import factories
import pytest_asyncio
from alinea_core.db.models import Job, VocabCandidate, VocabEntry
from alinea_core.document.blocks import DocumentContent
from alinea_core.search.rebuild import rebuild_block_search_index
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import vocab_candidates
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(vocab_candidates.router)
    return app


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession, redis_client: Any) -> AsyncIterator[SimpleNamespace]:
    from alinea_api.routers.vocab_candidates import get_vocab_job_wakeup
    from alinea_api.services.session_service import create_session
    from alinea_api.services.user_service import purge_user, upsert_user_by_email

    email = f"vc-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
    token = await create_session(redis_client, user.id)

    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    revision = await factories.make_revision(db_session, paper=paper)
    content = DocumentContent.model_validate(revision.content)
    await rebuild_block_search_index(db_session, str(revision.id), content)
    item = await factories.make_library_item(
        db_session, user=user, paper=paper, status="reading"
    )
    await db_session.commit()

    app = _build_app()
    wakeups: list[str] = []

    async def _noop_wakeup(job_id: str) -> None:
        wakeups.append(job_id)

    app.dependency_overrides[get_vocab_job_wakeup] = lambda: _noop_wakeup

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield SimpleNamespace(
                client=ac,
                user_id=uid,
                user=user,
                paper=paper,
                revision=revision,
                item_id=str(item.id),
                wakeups=wakeups,
                db=db_session,
            )
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


async def _add_candidate(
    ctx: SimpleNamespace,
    *,
    term: str = "reflow",
    kind: str = "word",
    block_id: str = "blk-p3",
    status: str = "pending",
) -> VocabCandidate:
    cand = VocabCandidate(
        id=str(uuid.uuid4()),
        user_id=ctx.user_id,
        library_item_id=ctx.item_id,
        term=term,
        kind=kind,
        context_anchor={
            "revision_id": str(ctx.revision.id),
            "block_id": block_id,
            "start": None,
            "end": None,
            "quote": term,
            "side": "source",
        },
        context_sentence="The reflow procedure straightens paths.",
        context_hl_start=4,
        context_hl_end=4 + len(term),
        status=status,
    )
    ctx.db.add(cand)
    await ctx.db.commit()
    return cand


# ============================================================================
# extract: ジョブを enqueue し、進行中は再利用する
# ============================================================================
async def test_extract_enqueues_job(ctx: SimpleNamespace) -> None:
    resp = await ctx.client.post(f"/api/library-items/{ctx.item_id}/vocab-candidates/extract")
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert job_id in ctx.wakeups

    job = await ctx.db.get(Job, job_id)
    assert job is not None
    assert job.kind == "vocab_extract"
    assert job.payload["library_item_id"] == ctx.item_id
    assert job.status == "queued"

    # 進行中ジョブがあれば再利用する(重複抽出しない)。
    again = await ctx.client.post(f"/api/library-items/{ctx.item_id}/vocab-candidates/extract")
    assert again.status_code == 202
    assert again.json()["job_id"] == job_id
    total = (
        await ctx.db.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == "vocab_extract", Job.library_item_id == ctx.item_id)
        )
    ).scalar_one()
    assert total == 1


async def test_extract_unknown_item_is_404(ctx: SimpleNamespace) -> None:
    resp = await ctx.client.post(
        f"/api/library-items/{uuid.uuid4()}/vocab-candidates/extract"
    )
    assert resp.status_code == 404


# ============================================================================
# list: pending のみ・出典 display 付き
# ============================================================================
async def test_list_returns_pending_with_source(ctx: SimpleNamespace) -> None:
    await _add_candidate(ctx, term="reflow", block_id="blk-p3")
    await _add_candidate(ctx, term="dismissed-word", block_id="blk-p3", status="dismissed")

    resp = await ctx.client.get(f"/api/library-items/{ctx.item_id}/vocab-candidates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["term"] == "reflow"
    assert item["kind"] == "word"
    assert item["anchor"]["block_id"] == "blk-p3"
    assert item["anchor"]["display"] == "§2 ¶1"
    assert item["source"]["paper_title"] == ctx.paper.title
    assert item["source"]["display"] == f"{ctx.paper.title} · §2 ¶1"
    assert item["highlight"] == {"start": 4, "end": 10}


# ============================================================================
# accept: 本物の VocabEntry を作り、生成ジョブを enqueue、候補を accepted に
# ============================================================================
async def test_accept_creates_entry_and_is_idempotent(ctx: SimpleNamespace) -> None:
    cand = await _add_candidate(ctx, term="reflow", kind="collocation", block_id="blk-p3")

    resp = await ctx.client.post(f"/api/vocab-candidates/{cand.id}/accept")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    vocab_id = body["vocab_id"]
    assert body["already_existed"] is False
    assert body["generation_job_id"] in ctx.wakeups

    entry = await ctx.db.get(VocabEntry, vocab_id)
    assert entry is not None
    assert entry.term == "reflow"
    assert entry.library_item_id == ctx.item_id
    # 生成ジョブ(kind='vocab')が積まれている。
    gen_job = await ctx.db.get(Job, body["generation_job_id"])
    assert gen_job is not None
    assert gen_job.kind == "vocab"
    assert gen_job.payload["vocab_id"] == vocab_id

    # 候補は accepted になり、entry がリンクされ、一覧から消える。
    refreshed = await ctx.db.get(VocabCandidate, cand.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "accepted"
    assert refreshed.vocab_entry_id == vocab_id
    listed = (
        await ctx.client.get(f"/api/library-items/{ctx.item_id}/vocab-candidates")
    ).json()
    assert listed["count"] == 0

    # 冪等: 二度目の accept は同じ entry を返し、重複を作らない。
    again = await ctx.client.post(f"/api/vocab-candidates/{cand.id}/accept")
    assert again.status_code == 201, again.text
    assert again.json()["vocab_id"] == vocab_id
    assert again.json()["already_existed"] is True
    count = (
        await ctx.db.execute(
            select(func.count()).select_from(VocabEntry).where(VocabEntry.term == "reflow")
        )
    ).scalar_one()
    assert count == 1


async def test_accept_when_term_already_saved_returns_existing(ctx: SimpleNamespace) -> None:
    # 先に手動で語彙帳に保存済みの語。
    existing = await factories.make_vocab_entry(
        ctx.db,
        user=ctx.user,
        library_item=await factories.make_library_item(ctx.db, user=ctx.user),
        term="Reflow",
        revision=ctx.revision,
    )
    await ctx.db.commit()
    cand = await _add_candidate(ctx, term="reflow", block_id="blk-p3")

    resp = await ctx.client.post(f"/api/vocab-candidates/{cand.id}/accept")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["vocab_id"] == str(existing.id)
    assert body["already_existed"] is True
    # 新しい entry は作られない。
    count = (
        await ctx.db.execute(
            select(func.count())
            .select_from(VocabEntry)
            .where(func.lower(VocabEntry.term) == "reflow")
        )
    ).scalar_one()
    assert count == 1


async def test_accept_unknown_candidate_is_404(ctx: SimpleNamespace) -> None:
    resp = await ctx.client.post(f"/api/vocab-candidates/{uuid.uuid4()}/accept")
    assert resp.status_code == 404


# ============================================================================
# dismiss: dismissed にして一覧から外す(冪等)
# ============================================================================
async def test_dismiss_marks_and_hides(ctx: SimpleNamespace) -> None:
    cand = await _add_candidate(ctx, term="reflow", block_id="blk-p3")

    resp = await ctx.client.post(f"/api/vocab-candidates/{cand.id}/dismiss")
    assert resp.status_code == 204, resp.text

    refreshed = await ctx.db.get(VocabCandidate, cand.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "dismissed"

    listed = (
        await ctx.client.get(f"/api/library-items/{ctx.item_id}/vocab-candidates")
    ).json()
    assert listed["count"] == 0

    # 冪等。
    again = await ctx.client.post(f"/api/vocab-candidates/{cand.id}/dismiss")
    assert again.status_code == 204


# ============================================================================
# 所有権: 他ユーザーには見えない
# ============================================================================
async def test_candidate_scoped_to_owner(
    ctx: SimpleNamespace, db_session: AsyncSession, redis_client: Any
) -> None:
    from alinea_api.services.session_service import create_session

    cand = await _add_candidate(ctx, term="reflow", block_id="blk-p3")

    # 別ユーザーのクライアント。
    other = await factories.make_user(db_session)
    await db_session.commit()
    token = await create_session(redis_client, other.id)

    # accept / list を他人が叩くと 404。
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as oc:
        oc.cookies.set("yk_session", token)
        assert (await oc.post(f"/api/vocab-candidates/{cand.id}/accept")).status_code == 404
        assert (
            await oc.get(f"/api/library-items/{ctx.item_id}/vocab-candidates")
        ).status_code == 404
