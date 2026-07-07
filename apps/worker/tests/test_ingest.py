"""取り込みステートマシンのテスト(M0-18・plans/05 §2・§7・§11)。

- PY-ING-02: readable 到達で先頭セクションが翻訳済み(部分読書)。
- PY-ING-05: 重複検知(arXiv ID 完全一致 + タイトルファジー一致)。
- PY-JOB-02: 段階再開(abstract 段で失敗 → 再実行で fetching/structuring を再処理しない)。
- 完全経路: translating_body をその場駆動して complete へ到達し、タイムライン 3 段が積まれる。

arXiv は ASGI スタブ、LLM は ScriptProvider、DB/S3/Redis は実サービス(ユニーク UUID)。
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import DocumentRevision, Job, LibraryItem, Paper, TranslationUnit
from yakudoku_core.document.blocks import DocumentContent
from yakudoku_core.ingest import (
    build_timeline,
    detect_duplicate,
    find_fuzzy_duplicate,
    is_fuzzy_duplicate,
)
from yakudoku_core.ingest.dedupe import PaperBibView
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.translation.pipeline import compute_translation_scope, find_shared_set
from yakudoku_llm.errors import ProviderChainExhausted
from yakudoku_llm.router import LLMRouter
from yakudoku_llm.testing.fake_provider import FakeLLMProvider
from yakudoku_worker.tasks.ingest import ingest_paper


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _arxiv_id() -> str:
    """`YYMM.NNNNN` 形式の一意な arXiv ID(実 DB の一意制約と衝突しない)。"""
    n = (int(time.time() * 1000) + random.randint(0, 9999)) % 100000
    return f"{random.randint(1001, 2912)}.{n:05d}"


async def _revision(db: AsyncSession, paper_id: str) -> DocumentRevision:
    return (
        (await db.execute(select(DocumentRevision).where(DocumentRevision.paper_id == paper_id)))
        .scalars()
        .one()
    )


async def _units_for_set(db: AsyncSession, set_id: str) -> dict[str, TranslationUnit]:
    rows = (
        (await db.execute(select(TranslationUnit).where(TranslationUnit.set_id == set_id)))
        .scalars()
        .all()
    )
    return {u.block_id: u for u in rows}


# ===========================================================================
# PY-ING-02: readable 到達で先頭セクションは訳出済み(本文は未着手)
# ===========================================================================


async def test_ingest_reaches_readable_with_first_section_translated(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
    arq_pool: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    ctx = {**worker_ctx, "arq_pool": arq_pool}  # 本文は張り出しのみ(その場駆動しない)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    # readable を通過し、本文は張り出し中(translating_body)。
    assert "readable" in JobStore.get_checkpoint(job)
    assert job.stage == "translating_body"

    rev = await _revision(db_session, ids["paper_id"])
    content = DocumentContent.model_validate(rev.content)
    scope = compute_translation_scope(content)
    first_blocks = set(scope.sections[0]["block_ids"])
    second_blocks = set(scope.sections[1]["block_ids"])

    tset = await find_shared_set(db_session, str(rev.id), "natural")
    assert tset is not None
    units = await _units_for_set(db_session, str(tset.id))

    # 先頭セクションは全訳済み(text_ja あり)= ビューアが開ける。
    assert first_blocks <= set(units)
    assert all(units[b].text_ja for b in first_blocks)
    # 本文の次セクションはまだ未訳(readable の時点では張り出しのみ)。
    assert not (second_blocks & set(units))

    # 残セクションが yk:bulk に張り出されている(§11.2)。
    body_jobs = (
        (await db_session.execute(select(Job).where(Job.kind == "translation"))).scalars().all()
    )
    body_for_set = [j for j in body_jobs if (j.payload or {}).get("set_id") == str(tset.id)]
    assert len(body_for_set) == len(scope.sections) - 1
    assert len(arq_pool.calls) == len(body_for_set)


# ===========================================================================
# 完全経路: translating_body → complete(その場駆動)。タイムライン 3 段。
# ===========================================================================


async def test_ingest_full_pipeline_reaches_complete(
    db_session: AsyncSession, worker_ctx: dict[str, Any], seed_ingest_job: Any
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)  # arq プール無し → 本文をその場駆動

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.stage == "complete"
    assert job.status == "succeeded"
    assert job.progress == 100

    rev = await _revision(db_session, ids["paper_id"])
    content = DocumentContent.model_validate(rev.content)
    scope = compute_translation_scope(content)
    tset = await find_shared_set(db_session, str(rev.id), "natural")
    assert tset is not None and tset.status == "complete"

    units = await _units_for_set(db_session, str(tset.id))
    # 自動翻訳対象は全て訳出済み。付録ブロックは対象外(未訳)。
    assert set(scope.in_scope_block_ids) <= set(units)
    appendix_block_ids = _appendix_block_ids(content, scope.appendix_section_ids)
    assert not (appendix_block_ids & set(units))

    # メタデータ・要約・タグが反映されている。
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert paper.title == "Mock Rectified Flow"
    assert paper.license == "cc-by-4.0"
    assert paper.abstract_ja
    assert paper.summary_lines and len(paper.summary_lines) == 3
    assert paper.thumbnail_key  # Figure 1 からサムネイル生成
    li = await db_session.get(LibraryItem, ids["library_item_id"])
    assert li is not None
    assert "cs.LG" in li.suggested_tags
    assert "distillation" in li.suggested_tags

    # タイムライン 3 段(fetching / structuring / translating_body)。
    timeline = build_timeline(job.log)
    assert len(timeline) == 3
    assert "HTML 取得" in timeline[0]["label"]
    assert "構造化" in timeline[1]["label"]
    assert "全文翻訳 完了" in timeline[2]["label"]


def _appendix_block_ids(content: DocumentContent, appendix_ids: list[str]) -> set[str]:
    out: set[str] = set()
    for sec, blk in content.iter_blocks():
        if sec.id in appendix_ids:
            out.add(blk.id)
    return out


# ===========================================================================
# PY-JOB-02: 段階再開(abstract 段で失敗 → 再実行で構造化を再処理しない)
# ===========================================================================


async def test_ingest_resumes_without_reprocessing(
    db_session: AsyncSession, worker_ctx: dict[str, Any], seed_ingest_job: Any
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)

    # フェーズ 1: abstract 段の LLM 呼び出しで必ず失敗する(fetching/structuring は成功済み)。
    failing_ctx = {**worker_ctx, "router": LLMRouter([("fake", "m", FakeLLMProvider(fail=True))])}
    job = await store.claim(ids["job_id"])
    assert job is not None
    with pytest.raises(ProviderChainExhausted):
        await ingest_paper(failing_ctx, store, job)

    rev_ids_after_fail = (
        (
            await db_session.execute(
                select(DocumentRevision.id).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert len(rev_ids_after_fail) == 1  # structuring は 1 度だけ実行された

    # リトライ余地を残して queued に戻す(run_job 相当)。
    await store.fail_with_retry(ids["job_id"], {"stage": "translating_abstract", "message": "boom"})

    # フェーズ 2: 正常な LLM で再実行 → 前段を再処理せず complete まで進む。
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    rev_ids_after_resume = (
        (
            await db_session.execute(
                select(DocumentRevision.id).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert rev_ids_after_resume == rev_ids_after_fail  # 同一リビジョン(二重処理なし)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.stage == "complete"
    assert job.status == "succeeded"


# ===========================================================================
# PY-ING-05: 重複検知(arXiv ID 完全一致 + タイトルファジー一致)
# ===========================================================================


async def test_detect_duplicate_by_arxiv_id(db_session: AsyncSession, seed_ingest_job: Any) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)

    dup = await detect_duplicate(db_session, arxiv_id, user_id=ids["user_id"])
    assert dup is not None
    assert dup.id == ids["library_item_id"]

    # 別 arXiv ID は重複なし。
    assert await detect_duplicate(db_session, _arxiv_id()) is None
    # 同 arXiv でも別ユーザーは未所持 → None。
    assert await detect_duplicate(db_session, arxiv_id, user_id=str(uuid.uuid4())) is None


def test_is_fuzzy_duplicate_matches_near_identical_titles() -> None:
    base = "Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow"
    a = PaperBibView(title=base, first_author_family="Liu", year=2022)
    # 句読点・大文字小文字違いのみ → 同一とみなす。
    b = PaperBibView(title=base.replace(":", " —").upper(), first_author_family="liu", year=2023)
    assert is_fuzzy_duplicate(a, b) is True

    # 第一著者姓が違う → 非重複。
    c = PaperBibView(title=base, first_author_family="Smith", year=2022)
    assert is_fuzzy_duplicate(a, c) is False
    # 年が 2 年以上離れる → 非重複。
    d = PaperBibView(title=base, first_author_family="Liu", year=2018)
    assert is_fuzzy_duplicate(a, d) is False
    # 全く別のタイトル → 非重複。
    e = PaperBibView(
        title="A Study of Feline Behavior in Urban Areas", first_author_family="Liu", year=2022
    )
    assert is_fuzzy_duplicate(a, e) is False


async def test_find_fuzzy_duplicate_in_db(db_session: AsyncSession) -> None:
    sfx = _uid()
    title = f"Rectified Flow Straight and Fast {sfx}"
    user_id = str(uuid.uuid4())
    from yakudoku_core.db.models import User

    db_session.add(User(id=user_id, email=f"{sfx}@t.test"))
    await db_session.flush()
    # private + 一意ユーザー所有にして、過去実行の public 論文と候補集合が混ざらないようにする。
    existing = Paper(
        id=str(uuid.uuid4()),
        title=title,
        authors=[{"name": "Xingchao Liu"}],
        arxiv_id=_arxiv_id(),
        visibility="private",
        owner_user_id=user_id,
    )
    db_session.add(existing)
    await db_session.flush()
    li = LibraryItem(id=str(uuid.uuid4()), user_id=user_id, paper_id=existing.id, status="planned")
    db_session.add(li)
    await db_session.commit()

    view = PaperBibView(
        title=title.replace(" ", "  ").lower(), first_author_family="Liu", year=None
    )
    match = await find_fuzzy_duplicate(
        db_session, view, user_id=user_id, exclude_paper_id=str(uuid.uuid4())
    )
    assert match is not None
    assert match.id == existing.id


# ===========================================================================
# waiting_quota: 翻訳段だけ停止(取り込み自体は失敗にしない。§2.6)
# ===========================================================================


async def test_translating_body_waits_on_quota(
    db_session: AsyncSession, worker_ctx: dict[str, Any], seed_ingest_job: Any
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    ctx = {**worker_ctx, "translation_quota_limit": 0}  # クォータ 0 で必ず超過

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    # 翻訳段は保留、取り込み(書誌・構造化・readable)は失敗にしない。
    assert job.status == "waiting_quota"
    assert job.stage == "translating_body"

    rev = await _revision(db_session, ids["paper_id"])
    tset = await find_shared_set(db_session, str(rev.id), "natural")
    assert tset is not None
    units = await _units_for_set(db_session, str(tset.id))
    assert units  # readable の先頭セクションは訳出済み
