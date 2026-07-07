"""``kind='vocab'`` ジョブ(generate_vocab_ai)のテスト(M2-11。plans/07 §7、docs/11 §4)。

- PY-VOC-02: AI 生成(FakeLLM 決定的)で 8+1(kind)フィールドが保存される。
- PY-VOC-03: ``edited_fields`` に入ったフィールドは regenerate で上書きされない。
- PY-VOC-04: チェーン全滅時は語彙本体・文脈・出典を残し ``generation_status='failed'``
  + ``generation_error`` を保存し、ジョブも ``status='failed'`` で確定する(黙って消えない)。

タスク関数(:func:`run_generate_vocab_ai`)を直接呼ぶ(HANDLERS への登録は followups)。
DB は実 PostgreSQL(worker 既存 conftest の ``db_session``)。クリーンアップは既存の worker
テスト方針(ユニーク UUID で衝突回避・明示 purge なし)に従う。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import Job, LibraryItem, Paper, User, VocabEntry
from yakudoku_core.jobs.store import JobStore
from yakudoku_llm.router import LLMRouter
from yakudoku_llm.testing.fake_provider import FakeLLMProvider
from yakudoku_worker.tasks.generate_vocab_ai import ALL_FIELDS, run_generate_vocab_ai

_STRUCTURED_RESPONSE: dict[str, Any] = {
    "kind": "idiom",
    "pos_label": "句動詞",
    "ipa": "/ˌbɔɪl ˈdaʊn tə/",  # noqa: RUF001
    "meaning_short": "要するに〜に帰着する",
    "meaning_long": "この文では、学習目的は結局単純な回帰問題になる、という意味。",
    "interpretation": "boil+down+to を物理イメージで分解して読む。",
    "etymology": "boil ← ラテン語 bullire(泡立つ)。",
    "mnemonic": "鍋を煮詰めるイメージ。",
    "related_forms": "come down to / amount to",
}


async def _make_entry(
    db: AsyncSession,
    *,
    edited_fields: list[str] | None = None,
    generation_status: str = "pending",
) -> tuple[User, LibraryItem, VocabEntry]:
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
    item = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(item)
    await db.flush()
    entry = VocabEntry(
        id=str(uuid.uuid4()),
        user_id=user.id,
        library_item_id=item.id,
        term="boil down to",
        context_anchor={
            "revision_id": str(uuid.uuid4()),
            "block_id": "blk-does-not-exist",
            "start": None,
            "end": None,
            "quote": None,
            "side": "source",
        },
        context_sentence="The training objective boils down to a simple regression.",
        context_hl_start=24,
        context_hl_end=36,
        edited_fields=edited_fields or [],
        generation_status=generation_status,
    )
    db.add(entry)
    await db.flush()
    await db.commit()
    return user, item, entry


async def _enqueue_and_claim(
    db: AsyncSession, *, user: User, item: LibraryItem, entry: VocabEntry, fields: list[str] | None
) -> Job:
    store = JobStore(db)
    job_id = await store.enqueue(
        kind="vocab",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(item.id),
        payload={"vocab_id": str(entry.id), "fields": fields},
    )
    job = await store.claim(job_id)
    assert job is not None
    return job


def _fake_router(*, fail: bool = False) -> LLMRouter:
    provider = FakeLLMProvider(fail=fail, structured={"vocab_content_v1": _STRUCTURED_RESPONSE})
    return LLMRouter([("fake", "fake-model", provider)])


# ============================================================================
# PY-VOC-02: 全フィールド生成・保存
# ============================================================================
async def test_generate_vocab_ai_writes_all_fields_and_completes(
    db_session: AsyncSession,
) -> None:
    user, item, entry = await _make_entry(db_session)
    job = await _enqueue_and_claim(db_session, user=user, item=item, entry=entry, fields=None)
    store = JobStore(db_session)
    ctx = {"router": _fake_router()}

    await run_generate_vocab_ai(ctx, store, job)

    updated = await db_session.get(VocabEntry, entry.id, populate_existing=True)
    assert updated is not None
    for field in ALL_FIELDS:
        assert getattr(updated, field) == _STRUCTURED_RESPONSE[field]
    assert updated.generation_status == "complete"
    assert updated.generation_error is None
    # 語彙・文脈・出典は変わらない。
    assert updated.term == "boil down to"
    assert updated.library_item_id == item.id

    finished = await store.get(str(job.id))
    assert finished is not None
    assert finished.status == "succeeded"
    assert set(finished.result["fields"]) == set(ALL_FIELDS)


# ============================================================================
# PY-VOC-03: edited_fields は上書きされない(二重防御)
# ============================================================================
async def test_generate_vocab_ai_skips_edited_fields(db_session: AsyncSession) -> None:
    user, item, entry = await _make_entry(
        db_session, edited_fields=["kind", "meaning_short", "meaning_long"]
    )
    entry.kind = "word"
    entry.meaning_short = "ユーザー編集済み短形"
    entry.meaning_long = "ユーザー編集済み長形"
    await db_session.commit()

    job = await _enqueue_and_claim(db_session, user=user, item=item, entry=entry, fields=None)
    store = JobStore(db_session)
    ctx = {"router": _fake_router()}

    await run_generate_vocab_ai(ctx, store, job)

    updated = await db_session.get(VocabEntry, entry.id, populate_existing=True)
    assert updated is not None
    # 編集済みフィールドは不変。
    assert updated.kind == "word"
    assert updated.meaning_short == "ユーザー編集済み短形"
    assert updated.meaning_long == "ユーザー編集済み長形"
    # 未編集フィールドは AI 生成で上書きされる。
    for field in ("pos_label", "ipa", "interpretation", "etymology", "mnemonic", "related_forms"):
        assert getattr(updated, field) == _STRUCTURED_RESPONSE[field]
    assert updated.generation_status == "complete"

    finished = await store.get(str(job.id))
    assert finished is not None
    assert set(finished.result["fields"]) == {
        "pos_label",
        "ipa",
        "interpretation",
        "etymology",
        "mnemonic",
        "related_forms",
    }


# ============================================================================
# regenerate の fields パラメータ(対象をさらに絞る。§11.6)
# ============================================================================
async def test_generate_vocab_ai_regenerate_limits_to_requested_fields(
    db_session: AsyncSession,
) -> None:
    user, item, entry = await _make_entry(db_session)
    entry.kind = "word"
    entry.pos_label = "PRESET_POS"
    entry.ipa = "PRESET_IPA"
    entry.meaning_short = "PRESET_SHORT"
    entry.meaning_long = "PRESET_LONG"
    entry.interpretation = "PRESET_INTERP"
    entry.related_forms = "PRESET_RELATED"
    await db_session.commit()

    job = await _enqueue_and_claim(
        db_session, user=user, item=item, entry=entry, fields=["mnemonic", "etymology"]
    )
    store = JobStore(db_session)
    ctx = {"router": _fake_router()}

    await run_generate_vocab_ai(ctx, store, job)

    updated = await db_session.get(VocabEntry, entry.id, populate_existing=True)
    assert updated is not None
    assert updated.mnemonic == _STRUCTURED_RESPONSE["mnemonic"]
    assert updated.etymology == _STRUCTURED_RESPONSE["etymology"]
    # fields で指定していないものは PRESET のまま(生成対象を絞れている)。
    assert updated.kind == "word"
    assert updated.pos_label == "PRESET_POS"
    assert updated.ipa == "PRESET_IPA"
    assert updated.meaning_short == "PRESET_SHORT"
    assert updated.meaning_long == "PRESET_LONG"
    assert updated.interpretation == "PRESET_INTERP"
    assert updated.related_forms == "PRESET_RELATED"

    finished = await store.get(str(job.id))
    assert finished is not None
    assert set(finished.result["fields"]) == {"mnemonic", "etymology"}


# ============================================================================
# PY-VOC-04: チェーン全滅 → 語彙は残り、failed + generation_error
# ============================================================================
async def test_generate_vocab_ai_failure_preserves_entry_and_fails_job(
    db_session: AsyncSession,
) -> None:
    user, item, entry = await _make_entry(db_session)
    job = await _enqueue_and_claim(db_session, user=user, item=item, entry=entry, fields=None)
    store = JobStore(db_session)
    ctx = {"router": _fake_router(fail=True)}

    await run_generate_vocab_ai(ctx, store, job)

    updated = await db_session.get(VocabEntry, entry.id, populate_existing=True)
    assert updated is not None
    assert updated.generation_status == "failed"
    assert updated.generation_error
    # 語彙本体・文脈・出典は消えない(P3。docs/11 §2)。
    assert updated.term == "boil down to"
    assert updated.context_sentence == "The training objective boils down to a simple regression."
    assert updated.library_item_id == item.id

    finished = await store.get(str(job.id))
    assert finished is not None
    assert finished.status == "failed"
    error = json.loads(finished.error or "{}")
    assert error["code"] == "provider_chain_exhausted"
