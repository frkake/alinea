"""``kind='vocab_extract'`` ジョブ(AI 単語抽出。S7)のテスト。

docs/superpowers/specs/2026-07-16-ai-word-extraction-design.md。fake router のみを使い、
実 LLM には依存しない。DB は実 PostgreSQL(worker 既存 conftest の ``db_session``)。

- happy path: 妥当な候補 → vocab_candidates 行が作られ、ジョブは succeeded(件数付き)。
- fail-closed: 実在しない block_id / block に無い term / 不正な kind は捨てる。
- dedup: 既に vocab_entries にある語・既に候補にある語(dismissed 含む)は再作成しない。
- 上限 MAX_CANDIDATES で切り詰める。
- ProviderChainExhausted → ジョブ failed、行は作らない。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from alinea_core.db.models import (
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    User,
    VocabCandidate,
    VocabEntry,
)
from alinea_core.jobs.store import JobStore
from alinea_llm.router import LLMRouter
from alinea_llm.testing.fake_provider import FakeLLMProvider
from alinea_worker.tasks.extract_vocab_candidates import (
    MAX_CANDIDATES,
    run_extract_vocab_candidates,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_CONTENT: dict[str, Any] = {
    "quality_level": "A",
    "sections": [
        {
            "id": "sec-1",
            "heading": {"number": "1", "title": "Introduction"},
            "blocks": [
                {
                    "id": "blk-p1",
                    "type": "paragraph",
                    "inlines": [
                        {
                            "t": "text",
                            "v": "The training objective boils down to a simple regression, "
                            "albeit with a rectified transport map.",
                        }
                    ],
                },
                {
                    "id": "blk-p2",
                    "type": "paragraph",
                    "inlines": [
                        {"t": "text", "v": "We hinge on an EMA teacher for distillation."}
                    ],
                },
            ],
        }
    ],
}


def _candidates_response(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"candidates": items}


async def _make_item(db: AsyncSession) -> tuple[User, LibraryItem, DocumentRevision]:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=str(uuid.uuid4()),
        title="Mock Rectified Flow",
        visibility="private",
        owner_user_id=user.id,
    )
    db.add(paper)
    await db.flush()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="arxiv_html",
        content=_CONTENT,
        stats={},
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    item = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(item)
    await db.flush()
    await db.commit()
    return user, item, revision


async def _enqueue_and_claim(db: AsyncSession, *, user: User, item: LibraryItem) -> Job:
    store = JobStore(db)
    job_id = await store.enqueue(
        kind="vocab_extract",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(item.id),
        payload={"library_item_id": str(item.id)},
    )
    job = await store.claim(job_id)
    assert job is not None
    return job


def _fake_router(
    items: list[dict[str, Any]] | None = None, *, fail: bool = False
) -> LLMRouter:
    provider = FakeLLMProvider(
        fail=fail,
        structured={"vocab_candidates_v1": _candidates_response(items or [])},
    )
    return LLMRouter([("fake", "fake-model", provider)])


async def _count(db: AsyncSession, item: LibraryItem) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(VocabCandidate)
                .where(VocabCandidate.library_item_id == item.id)
            )
        ).scalar_one()
    )


# ============================================================================
# happy path
# ============================================================================
async def test_extract_creates_pending_candidates(db_session: AsyncSession) -> None:
    user, item, revision = await _make_item(db_session)
    job = await _enqueue_and_claim(db_session, user=user, item=item)
    store = JobStore(db_session)
    ctx = {
        "router": _fake_router(
            [
                {"term": "boils down to", "kind": "idiom", "block_id": "blk-p1"},
                {"term": "albeit", "kind": "word", "block_id": "blk-p1", "reason": "難語"},
                {"term": "hinge on", "kind": "collocation", "block_id": "blk-p2"},
            ]
        )
    }

    await run_extract_vocab_candidates(ctx, store, job)

    rows = (
        (
            await db_session.execute(
                select(VocabCandidate)
                .where(VocabCandidate.library_item_id == item.id)
                .order_by(VocabCandidate.term)
            )
        )
        .scalars()
        .all()
    )
    terms = {r.term for r in rows}
    assert terms == {"boils down to", "albeit", "hinge on"}
    for r in rows:
        assert r.status == "pending"
        assert r.user_id == user.id
        # 文脈センテンスがサーバー側で導出され、対象語を含む。
        assert r.term.lower() in r.context_sentence.lower()
        assert r.context_anchor["block_id"] in ("blk-p1", "blk-p2")
        assert r.context_anchor["revision_id"] == str(revision.id)
        # ハイライトが文脈センテンス内の実位置を指す。
        assert (
            r.context_sentence[r.context_hl_start : r.context_hl_end].lower() == r.term.lower()
        )

    finished = await store.get(str(job.id))
    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.result["candidates_created"] == 3


# ============================================================================
# fail-closed: 不正な候補は捨てる
# ============================================================================
async def test_extract_drops_invalid_candidates(db_session: AsyncSession) -> None:
    user, item, _revision = await _make_item(db_session)
    job = await _enqueue_and_claim(db_session, user=user, item=item)
    store = JobStore(db_session)
    # 注: kind の値域(word/collocation/idiom)は structured 出力スキーマが保証するため、ここでは
    # スキーマは通るが意味的に不正なケース(存在しない block / ブロックに無い語)を検証する。
    ctx = {
        "router": _fake_router(
            [
                {"term": "albeit", "kind": "word", "block_id": "blk-does-not-exist"},  # 実在せず
                {"term": "quantum", "kind": "word", "block_id": "blk-p1"},  # blk-p1 に無い語
                {"term": "hinge on", "kind": "collocation", "block_id": "blk-p1"},  # 別ブロックの語
                {"term": "albeit", "kind": "word", "block_id": "blk-p1"},  # これだけ妥当
            ]
        )
    }

    await run_extract_vocab_candidates(ctx, store, job)

    rows = (
        (
            await db_session.execute(
                select(VocabCandidate).where(VocabCandidate.library_item_id == item.id)
            )
        )
        .scalars()
        .all()
    )
    assert [r.term for r in rows] == ["albeit"]

    finished = await store.get(str(job.id))
    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.result["candidates_created"] == 1


# ============================================================================
# dedup: 既存 vocab_entries と既存候補(dismissed 含む)を除く・冪等再実行
# ============================================================================
async def test_extract_skips_existing_and_is_idempotent(db_session: AsyncSession) -> None:
    user, item, revision = await _make_item(db_session)

    # 既に語彙帳にある語(ユーザー横断で重複扱い。docs/11 §1)。
    existing = VocabEntry(
        id=str(uuid.uuid4()),
        user_id=user.id,
        library_item_id=item.id,
        term="Albeit",  # 大文字違い → 正規化一致
        context_anchor={"revision_id": str(revision.id), "block_id": "blk-p1", "side": "source"},
        context_sentence="…",
    )
    db_session.add(existing)
    # 既に dismiss 済みの候補(再提案しない)。
    dismissed = VocabCandidate(
        id=str(uuid.uuid4()),
        user_id=user.id,
        library_item_id=item.id,
        term="hinge on",
        kind="collocation",
        context_anchor={"revision_id": str(revision.id), "block_id": "blk-p2", "side": "source"},
        context_sentence="We hinge on an EMA teacher for distillation.",
        status="dismissed",
    )
    db_session.add(dismissed)
    await db_session.commit()

    proposed = [
        {"term": "albeit", "kind": "word", "block_id": "blk-p1"},  # 既存 entry → skip
        {"term": "hinge on", "kind": "collocation", "block_id": "blk-p2"},  # dismissed → skip
        {"term": "boils down to", "kind": "idiom", "block_id": "blk-p1"},  # 新規
    ]

    job = await _enqueue_and_claim(db_session, user=user, item=item)
    store = JobStore(db_session)
    await run_extract_vocab_candidates(
        {"router": _fake_router(proposed)}, store, job
    )

    pending = (
        (
            await db_session.execute(
                select(VocabCandidate).where(
                    VocabCandidate.library_item_id == item.id,
                    VocabCandidate.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
    assert [r.term for r in pending] == ["boils down to"]

    finished = await store.get(str(job.id))
    assert finished is not None
    assert finished.result["candidates_created"] == 1

    # 冪等: もう一度同じ提案で走らせても新規は 0。
    total_before = await _count(db_session, item)
    job2 = await _enqueue_and_claim(db_session, user=user, item=item)
    await run_extract_vocab_candidates({"router": _fake_router(proposed)}, store, job2)
    assert await _count(db_session, item) == total_before
    finished2 = await store.get(str(job2.id))
    assert finished2 is not None
    assert finished2.result["candidates_created"] == 0


# ============================================================================
# 上限 MAX_CANDIDATES
# ============================================================================
async def _make_item_with_words(
    db: AsyncSession, words: list[str]
) -> tuple[User, LibraryItem]:
    """指定した全語を本文に含む 1 ブロックの論文を作る(truncation 検証用)。"""
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=str(uuid.uuid4()), title="Big", visibility="private", owner_user_id=user.id
    )
    db.add(paper)
    await db.flush()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="arxiv_html",
        content={
            "quality_level": "A",
            "sections": [
                {
                    "id": "sec-1",
                    "heading": {"number": "1", "title": "Words"},
                    "blocks": [
                        {
                            "id": "blk-w",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": " ".join(words) + "."}],
                        }
                    ],
                }
            ],
        },
        stats={},
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    item = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(item)
    await db.flush()
    await db.commit()
    return user, item


async def test_extract_truncates_to_max(db_session: AsyncSession) -> None:
    # MAX_CANDIDATES を超える件数を提案する(全て実在・妥当)。
    words = [f"lexeme{i:02d}" for i in range(MAX_CANDIDATES + 8)]
    user, item = await _make_item_with_words(db_session, words)
    proposed = [{"term": w, "kind": "word", "block_id": "blk-w"} for w in words]
    job = await _enqueue_and_claim(db_session, user=user, item=item)
    store = JobStore(db_session)

    await run_extract_vocab_candidates({"router": _fake_router(proposed)}, store, job)

    created = await _count(db_session, item)
    assert created == MAX_CANDIDATES  # 上限で切り詰められる
    finished = await store.get(str(job.id))
    assert finished is not None
    assert finished.result["candidates_created"] == MAX_CANDIDATES


# ============================================================================
# ProviderChainExhausted → ジョブ failed・行は作らない
# ============================================================================
async def test_extract_failure_marks_job_failed(db_session: AsyncSession) -> None:
    user, item, _revision = await _make_item(db_session)
    job = await _enqueue_and_claim(db_session, user=user, item=item)
    store = JobStore(db_session)

    await run_extract_vocab_candidates({"router": _fake_router(fail=True)}, store, job)

    assert await _count(db_session, item) == 0
    finished = await store.get(str(job.id))
    assert finished is not None
    assert finished.status == "failed"
    error = json.loads(finished.error or "{}")
    assert error["code"] == "provider_chain_exhausted"
