"""取り込みステートマシンのテスト(M0-18・plans/05 §2・§7・§11)。

- PY-ING-02: readable 到達で先頭セクションが翻訳済み(部分読書)。
- PY-ING-05: 重複検知(arXiv ID 完全一致 + タイトルファジー一致)。
- PY-JOB-02: 段階再開(abstract 段で失敗 → 再実行で fetching/structuring を再処理しない)。
- 完全経路: translating_body をその場駆動して complete へ到達し、タイムライン 3 段が積まれる。

arXiv は ASGI スタブ、LLM は ScriptProvider、DB/S3/Redis は実サービス(ユニーク UUID)。
"""

from __future__ import annotations

import json
import random
import time
import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, call

import alinea_worker.pipeline as worker_pipeline
import pytest
from _summary_contract import assert_summary_lines_contract
from alinea_core.db.models import (
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest import (
    build_timeline,
    detect_duplicate,
    find_fuzzy_duplicate,
    is_fuzzy_duplicate,
)
from alinea_core.ingest.dedupe import PaperBibView
from alinea_core.jobs.store import JobStore
from alinea_core.translation.pipeline import (
    TranslationPlan,
    TranslationSettings,
    build_translation_plan,
    compute_translation_scope,
    find_shared_set,
    resolve_translation_plan,
    select_translation_plan_sections,
)
from alinea_core.translation.pipeline import (
    translate_section as core_translate_section,
)
from alinea_core.translation.placeholder import encode_block
from alinea_llm.errors import ProviderChainExhausted
from alinea_llm.router import LLMRouter
from alinea_llm.testing.fake_provider import FakeLLMProvider
from alinea_worker.pipeline import IngestRun, deps_from_ctx
from alinea_worker.source_candidates import CandidateUnavailable
from alinea_worker.tasks.ingest import ingest_paper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


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


async def test_latex_eprint_rate_limit_uses_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = object.__new__(IngestRun)
    attempts = 0

    async def fetch_once(_http: object) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise CandidateUnavailable("latex", "rate_limited", "rate limited")
        return b"latex archive"

    sleep = AsyncMock()
    monkeypatch.setattr(run, "_fetch_latex_candidate_bytes_once", fetch_once)
    monkeypatch.setattr(cast(Any, worker_pipeline).asyncio, "sleep", sleep)

    assert await run._fetch_latex_candidate_bytes(cast(Any, object())) == b"latex archive"
    assert sleep.await_args_list == [call(20), call(40)]


async def test_latex_eprint_rate_limit_honors_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = object.__new__(IngestRun)
    attempts = 0

    async def fetch_once(_http: object) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise CandidateUnavailable(
                "latex", "rate_limited", "rate limited", retry_after_s=120
            )
        return b"latex archive"

    sleep = AsyncMock()
    monkeypatch.setattr(run, "_fetch_latex_candidate_bytes_once", fetch_once)
    monkeypatch.setattr(cast(Any, worker_pipeline).asyncio, "sleep", sleep)

    assert await run._fetch_latex_candidate_bytes(cast(Any, object())) == b"latex archive"
    assert sleep.await_args_list == [call(120), call(120)]


async def test_latex_eprint_rate_limit_retries_beyond_network_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # arXiv 429s recover after several minutes; the LaTeX candidate must not be
    # abandoned after the (shorter) transient-network retry budget.
    run = object.__new__(IngestRun)
    attempts = 0

    async def fetch_once(_http: object) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts < 6:
            raise CandidateUnavailable("latex", "rate_limited", "rate limited")
        return b"latex archive"

    sleep = AsyncMock()
    monkeypatch.setattr(run, "_fetch_latex_candidate_bytes_once", fetch_once)
    monkeypatch.setattr(cast(Any, worker_pipeline).asyncio, "sleep", sleep)

    assert await run._fetch_latex_candidate_bytes(cast(Any, object())) == b"latex archive"
    # 5 backoffs before the 6th success, each capped at 60s.
    assert sleep.await_count == 5
    assert all(delay <= 60 for ((delay,), _kwargs) in sleep.await_args_list)


async def test_latex_eprint_network_error_keeps_short_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = object.__new__(IngestRun)
    attempts = 0

    async def fetch_once(_http: object) -> bytes:
        nonlocal attempts
        attempts += 1
        raise CandidateUnavailable("latex", "network_error", "boom")

    sleep = AsyncMock()
    monkeypatch.setattr(run, "_fetch_latex_candidate_bytes_once", fetch_once)
    monkeypatch.setattr(cast(Any, worker_pipeline).asyncio, "sleep", sleep)

    with pytest.raises(CandidateUnavailable):
        await run._fetch_latex_candidate_bytes(cast(Any, object()))
    assert attempts == worker_pipeline._LATEX_FETCH_MAX_ATTEMPTS


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

    # 残セクションが alinea:bulk に張り出されている(§11.2)。
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
    assert tset.plan is not None
    stored_plan = resolve_translation_plan(
        content,
        tset.plan,
        pages=(rev.stats or {}).get("pages"),
    )
    assert stored_plan.target_block_ids == scope.in_scope_block_ids
    assert stored_plan.include_appendix is True

    units = await _units_for_set(db_session, str(tset.id))
    # 自動翻訳対象は付録を含めて全て訳出済み。
    assert set(scope.in_scope_block_ids) <= set(units)
    appendix_block_ids = _appendix_block_ids(content, scope.appendix_section_ids)
    assert appendix_block_ids <= set(units)
    assert all(units[block_id].text_ja for block_id in appendix_block_ids)

    # メタデータ・要約・タグが反映されている。
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert paper.title == "Mock Rectified Flow"
    assert paper.license == "cc-by-4.0"
    assert paper.abstract_ja
    assert_summary_lines_contract(paper.summary_lines)
    assert paper.thumbnail_key  # Figure 1 からサムネイル生成
    li = await db_session.get(LibraryItem, ids["library_item_id"])
    assert li is not None
    assert "cs.LG" in li.suggested_tags
    assert "distillation" in li.suggested_tags

    # タイムライン 4 段(fetching / structuring / translating_body / 日本語PDF)。
    timeline = build_timeline(job.log)
    assert len(timeline) == 4
    assert "HTML 取得" in timeline[0]["label"]
    assert "構造化" in timeline[1]["label"]
    assert "全文翻訳 完了" in timeline[2]["label"]
    assert "日本語PDFをビルド" in timeline[3]["label"]


async def test_ingest_sanitizes_unsafe_controls_from_all_summary_outputs(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
    script_provider: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = script_provider.generate_structured

    async def generate_with_controls(request: Any) -> Any:
        response = await original_generate(request)
        data = dict(response.parsed or {})
        if request.json_schema is not None and request.json_schema.name == "summary_3line_v1":
            data["summary_lines"] = [f"{line}\x00\x11" for line in data.get("summary_lines", [])]
            data["suggested_tags"] = [f"{tag}\x02" for tag in data.get("suggested_tags", [])]
        else:
            data["translations"] = [
                {**item, "ja": f"{item['ja']}\x00\x11"} for item in data.get("translations", [])
            ]
        return response.model_copy(
            update={"parsed": data, "text": json.dumps(data, ensure_ascii=False)}
        )

    monkeypatch.setattr(script_provider, "generate_structured", generate_with_controls)
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None

    await ingest_paper(worker_ctx, store, job)

    paper = await db_session.get(Paper, ids["paper_id"])
    library_item = await db_session.get(LibraryItem, ids["library_item_id"])
    assert paper is not None and library_item is not None
    assert paper.abstract_ja and "\x00" not in paper.abstract_ja and "\x11" not in paper.abstract_ja
    assert paper.summary_lines is not None
    assert all("\x00" not in str(line) and "\x11" not in str(line) for line in paper.summary_lines)
    assert all("\x02" not in tag for tag in library_item.suggested_tags)


async def test_ingest_preserves_explicit_appendix_translation_opt_out(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    user = await db_session.get(User, ids["user_id"])
    assert user is not None
    user.settings = {"translation": {"auto_translate_appendix": False}}
    await db_session.commit()

    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    revision = await _revision(db_session, ids["paper_id"])
    content = DocumentContent.model_validate(revision.content)
    full_scope = compute_translation_scope(content)
    opted_out_scope = compute_translation_scope(content, include_appendix=False)
    translation_set = await find_shared_set(db_session, str(revision.id), "natural")
    assert translation_set is not None
    assert translation_set.plan is not None
    stored_plan = resolve_translation_plan(
        content,
        translation_set.plan,
        pages=(revision.stats or {}).get("pages"),
    )
    units = await _units_for_set(db_session, str(translation_set.id))
    appendix_block_ids = _appendix_block_ids(content, full_scope.appendix_section_ids)

    assert set(opted_out_scope.in_scope_block_ids) <= set(units)
    assert not (appendix_block_ids & set(units))
    assert stored_plan.target_block_ids == opted_out_scope.in_scope_block_ids
    assert stored_plan.include_appendix is False
    assert translation_set.status == "complete"


async def test_long_public_paper_waits_for_section_selection_before_body_translation(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    user = await db_session.get(User, ids["user_id"])
    assert user is not None
    user.settings = {"translation": {"suggest_section_selection_over_30_pages": True}}
    await db_session.commit()

    shared_id: str | None = None
    original_abstract_stage = IngestRun._stage_translating_abstract

    async def finish_abstract_with_long_page_count(run: IngestRun) -> None:
        nonlocal shared_id
        await original_abstract_stage(run)
        assert run.revision_id is not None and run.content is not None
        revision = await run.session.get(DocumentRevision, run.revision_id)
        assert revision is not None
        revision.stats = {**(revision.stats or {}), "pages": 31}
        shared = TranslationSet(
            revision_id=run.revision_id,
            style="natural",
            scope="shared",
            plan=build_translation_plan(
                run.content,
                TranslationSettings(),
                pages=31,
            ).model_dump(mode="json"),
            status="pending",
        )
        run.session.add(shared)
        await run.session.commit()
        shared_id = str(shared.id)

    body_calls: list[str] = []

    async def reject_body_translation(
        _session: AsyncSession,
        _set_id: str,
        section_id: str,
        *_args: Any,
        **_kwargs: Any,
    ) -> Any:
        body_calls.append(section_id)
        raise AssertionError("body translation ran before section selection")

    monkeypatch.setattr(
        IngestRun,
        "_stage_translating_abstract",
        finish_abstract_with_long_page_count,
    )
    monkeypatch.setattr("alinea_worker.pipeline.translate_section", reject_body_translation)

    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    checkpoint = JobStore.get_checkpoint(job)
    selection = checkpoint["section_selection"]
    assert job.status == "waiting_input"
    assert job.stage == "selecting_sections"
    assert job.progress == 55
    assert selection == {
        "status": "pending",
        "set_id": selection["set_id"],
        "revision_id": str((await _revision(db_session, ids["paper_id"])).id),
    }
    assert "readable" not in checkpoint
    assert body_calls == []

    translation_set = await db_session.get(TranslationSet, selection["set_id"])
    assert translation_set is not None
    assert translation_set.scope == "personal"
    assert translation_set.user_id == ids["user_id"]
    assert translation_set.base_set_id == shared_id
    pending = TranslationPlan.model_validate(translation_set.plan)
    assert pending.target_section_ids == []
    assert pending.target_block_ids == []

    translation_jobs = (
        (await db_session.execute(select(Job).where(Job.kind == "translation"))).scalars().all()
    )
    assert [
        queued
        for queued in translation_jobs
        if (queued.payload or {}).get("set_id") == str(translation_set.id)
    ] == []


async def test_long_paper_resume_uses_exact_accepted_selection_and_finalizes(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await seed_ingest_job(
        db_session,
        arxiv_id=_arxiv_id(),
        visibility="private",
    )
    user = await db_session.get(User, ids["user_id"])
    assert user is not None
    user.settings = {"translation": {"suggest_section_selection_over_30_pages": True}}
    await db_session.commit()

    original_abstract_stage = IngestRun._stage_translating_abstract
    page_count_injected = False

    async def finish_abstract_with_long_page_count(run: IngestRun) -> None:
        nonlocal page_count_injected
        await original_abstract_stage(run)
        if page_count_injected:
            return
        assert run.revision_id is not None
        revision = await run.session.get(DocumentRevision, run.revision_id)
        assert revision is not None
        revision.stats = {**(revision.stats or {}), "pages": 42}
        await run.session.commit()
        page_count_injected = True

    section_calls: list[tuple[str, tuple[str, ...]]] = []

    async def record_section_translation(
        session: AsyncSession,
        set_id: str,
        section_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        section_calls.append((section_id, tuple(kwargs.get("block_ids") or ())))
        return await core_translate_section(
            session,
            set_id,
            section_id,
            *args,
            **kwargs,
        )

    pdf_build_set_ids: list[str] = []

    async def record_pdf_build(run: IngestRun) -> None:
        assert run.set_id is not None
        pdf_build_set_ids.append(run.set_id)

    monkeypatch.setattr(
        IngestRun,
        "_stage_translating_abstract",
        finish_abstract_with_long_page_count,
    )
    monkeypatch.setattr("alinea_worker.pipeline.translate_section", record_section_translation)
    monkeypatch.setattr(IngestRun, "_build_latex_translation_pdf", record_pdf_build)

    store = JobStore(db_session)
    first = await store.claim(ids["job_id"])
    assert first is not None
    await ingest_paper(worker_ctx, store, first)

    waiting = await store.get(ids["job_id"])
    assert waiting is not None and waiting.status == "waiting_input"
    selection_checkpoint = JobStore.get_checkpoint(waiting)["section_selection"]
    translation_set = await db_session.get(TranslationSet, selection_checkpoint["set_id"])
    assert translation_set is not None
    pending = TranslationPlan.model_validate(translation_set.plan)
    revision = await _revision(db_session, ids["paper_id"])
    content = DocumentContent.model_validate(revision.content)
    selectable_scope = compute_translation_scope(content, include_appendix=pending.include_appendix)
    selected_scope = selectable_scope.sections[1:3]
    assert len(selected_scope) == 2
    selected_section_ids = [str(section["section_id"]) for section in selected_scope]
    selected = select_translation_plan_sections(content, pending, selected_section_ids)
    selected_json = selected.model_dump(mode="json")

    translation_set.plan = selected_json
    checkpoint = JobStore.get_checkpoint(waiting)
    checkpoint["section_selection"] = {
        "status": "accepted",
        "set_id": str(translation_set.id),
        "revision_id": str(revision.id),
        "plan": selected_json,
    }
    waiting.payload = {**waiting.payload, "_checkpoint": checkpoint}
    waiting.status = "queued"
    waiting.stage = "selecting_sections"
    user.settings = {
        "translation": {
            "auto_translate_appendix": False,
            "default_style": "literal",
            "suggest_section_selection_over_30_pages": True,
        }
    }
    await db_session.commit()

    resumed = await store.claim(ids["job_id"])
    assert resumed is not None
    await ingest_paper(worker_ctx, store, resumed)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.stage == "complete"
    assert completed.progress == 100
    assert section_calls == [
        (str(section["section_id"]), tuple(section["block_ids"])) for section in selected_scope
    ]
    assert pdf_build_set_ids == [str(translation_set.id)]

    await db_session.refresh(translation_set)
    assert translation_set.style == "natural"
    assert translation_set.plan == selected_json
    assert translation_set.status == "complete"
    units = await _units_for_set(db_session, str(translation_set.id))
    assert set(units) == set(selected.target_block_ids)


@pytest.mark.parametrize("corruption", ["pending_identity", "accepted_order"])
async def test_long_paper_resume_rejects_corrupt_selection_checkpoint(
    corruption: str,
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    user = await db_session.get(User, ids["user_id"])
    assert user is not None
    user.settings = {"translation": {"suggest_section_selection_over_30_pages": True}}
    await db_session.commit()

    original_abstract_stage = IngestRun._stage_translating_abstract

    async def finish_abstract_with_long_page_count(run: IngestRun) -> None:
        await original_abstract_stage(run)
        assert run.revision_id is not None
        revision = await run.session.get(DocumentRevision, run.revision_id)
        assert revision is not None
        revision.stats = {**(revision.stats or {}), "pages": 31}
        await run.session.commit()

    monkeypatch.setattr(
        IngestRun,
        "_stage_translating_abstract",
        finish_abstract_with_long_page_count,
    )
    store = JobStore(db_session)
    first = await store.claim(ids["job_id"])
    assert first is not None
    await ingest_paper(worker_ctx, store, first)

    waiting = await store.get(ids["job_id"])
    assert waiting is not None and waiting.status == "waiting_input"
    checkpoint = JobStore.get_checkpoint(waiting)
    if corruption == "pending_identity":
        checkpoint["section_selection"] = {
            **checkpoint["section_selection"],
            "set_id": str(uuid.uuid4()),
        }
    else:
        selection = checkpoint["section_selection"]
        translation_set = await db_session.get(TranslationSet, selection["set_id"])
        assert translation_set is not None
        pending = TranslationPlan.model_validate(translation_set.plan)
        revision = await _revision(db_session, ids["paper_id"])
        content = DocumentContent.model_validate(revision.content)
        selectable = compute_translation_scope(
            content,
            include_appendix=pending.include_appendix,
        )
        selected_ids = [str(section["section_id"]) for section in selectable.sections[:2]]
        corrupt = select_translation_plan_sections(
            content,
            pending,
            selected_ids,
        ).model_dump(mode="json")
        corrupt["target_section_ids"] = list(reversed(corrupt["target_section_ids"]))
        translation_set.plan = corrupt
        checkpoint["section_selection"] = {
            "status": "accepted",
            "set_id": str(translation_set.id),
            "revision_id": str(revision.id),
            "plan": corrupt,
        }
    waiting.payload = {**waiting.payload, "_checkpoint": checkpoint}
    waiting.status = "queued"
    await db_session.commit()

    resumed = await store.claim(ids["job_id"])
    assert resumed is not None
    with pytest.raises(ValueError, match="section selection"):
        await ingest_paper(worker_ctx, store, resumed)


@pytest.mark.parametrize(
    ("enabled", "pages"),
    [
        (False, 31),
        (True, 30),
        (True, None),
    ],
)
async def test_section_selection_gate_keeps_existing_automatic_path(
    enabled: bool,
    pages: int | None,
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    user = await db_session.get(User, ids["user_id"])
    assert user is not None
    user.settings = {"translation": {"suggest_section_selection_over_30_pages": enabled}}
    await db_session.commit()

    original_abstract_stage = IngestRun._stage_translating_abstract

    async def finish_abstract_with_page_count(run: IngestRun) -> None:
        await original_abstract_stage(run)
        assert run.revision_id is not None
        revision = await run.session.get(DocumentRevision, run.revision_id)
        assert revision is not None
        stats = dict(revision.stats or {})
        if pages is None:
            stats.pop("pages", None)
        else:
            stats["pages"] = pages
        revision.stats = stats
        await run.session.commit()

    monkeypatch.setattr(
        IngestRun,
        "_stage_translating_abstract",
        finish_abstract_with_page_count,
    )

    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.stage == "complete"
    assert "section_selection" not in JobStore.get_checkpoint(completed)

    revision = await _revision(db_session, ids["paper_id"])
    shared = await find_shared_set(db_session, str(revision.id), "natural")
    assert shared is not None
    assert shared.scope == "shared"


async def _enqueue_ingest_for_existing_public_paper(
    db: AsyncSession,
    *,
    paper_id: str,
    arxiv_id: str,
    settings: dict[str, Any],
) -> str:
    user = User(email=f"{uuid.uuid4().hex}@reuse.test", settings=settings)
    db.add(user)
    await db.flush()
    item = LibraryItem(user_id=str(user.id), paper_id=paper_id, status="planned")
    db.add(item)
    await db.commit()
    return await JobStore(db).enqueue(
        kind="ingest",
        payload={
            "mode": "initial",
            "source": "arxiv",
            "arxiv_id": arxiv_id,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "library_item_id": str(item.id),
        },
        priority="bulk",
        user_id=str(user.id),
        paper_id=paper_id,
        library_item_id=str(item.id),
    )


async def test_public_shared_plan_expands_but_never_shrinks_on_reuse(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    first_user = await db_session.get(User, ids["user_id"])
    assert first_user is not None
    first_user.settings = {"translation": {"auto_translate_appendix": False}}
    await db_session.commit()
    store = JobStore(db_session)
    first_job = await store.claim(ids["job_id"])
    assert first_job is not None
    await ingest_paper(worker_ctx, store, first_job)

    revision = await _revision(db_session, ids["paper_id"])
    content = DocumentContent.model_validate(revision.content)
    full_scope = compute_translation_scope(content)
    main_scope = compute_translation_scope(content, include_appendix=False)
    shared = await find_shared_set(db_session, str(revision.id), "natural")
    assert shared is not None and shared.plan is not None
    first_plan = resolve_translation_plan(
        content,
        shared.plan,
        pages=(revision.stats or {}).get("pages"),
    )
    assert first_plan.target_block_ids == main_scope.in_scope_block_ids

    full_job_id = await _enqueue_ingest_for_existing_public_paper(
        db_session,
        paper_id=ids["paper_id"],
        arxiv_id=arxiv_id,
        settings={},
    )
    full_job = await store.claim(full_job_id)
    assert full_job is not None
    await ingest_paper(worker_ctx, store, full_job)
    await db_session.refresh(shared)
    expanded = resolve_translation_plan(
        content,
        shared.plan,
        pages=(revision.stats or {}).get("pages"),
    )
    assert expanded.target_block_ids == full_scope.in_scope_block_ids
    assert expanded.include_appendix is True

    opt_out_job_id = await _enqueue_ingest_for_existing_public_paper(
        db_session,
        paper_id=ids["paper_id"],
        arxiv_id=arxiv_id,
        settings={"translation": {"auto_translate_appendix": False}},
    )
    opt_out_job = await store.claim(opt_out_job_id)
    assert opt_out_job is not None
    await ingest_paper(worker_ctx, store, opt_out_job)
    await db_session.refresh(shared)
    not_shrunk = resolve_translation_plan(
        content,
        shared.plan,
        pages=(revision.stats or {}).get("pages"),
    )
    assert not_shrunk.target_block_ids == full_scope.in_scope_block_ids
    assert not_shrunk.include_appendix is True


@pytest.mark.parametrize("change", ["targets", "table"])
async def test_reuse_complete_translation_set_becomes_partial_when_work_expands(
    change: str,
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    content = DocumentContent.model_validate(
        {
            "quality_level": "A",
            "sections": [
                {
                    "id": "main",
                    "heading": {"number": "1", "title": "Main"},
                    "blocks": [
                        {
                            "id": "main-block",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "Main."}],
                        }
                    ],
                },
                {
                    "id": "appendix",
                    "heading": {"number": "A", "title": "Details"},
                    "blocks": [
                        {
                            "id": "appendix-block",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "Appendix."}],
                        }
                    ],
                },
            ],
        }
    )
    user = User(email=f"{uuid.uuid4().hex}@reuse-partial.test")
    paper = Paper(
        arxiv_id=_arxiv_id(),
        title="Reuse partial",
        visibility="public",
        license="cc-by-4.0",
    )
    db_session.add_all([user, paper])
    await db_session.flush()
    revision = DocumentRevision(
        paper_id=str(paper.id),
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content=content.model_dump(mode="json"),
        stats={"pages": 2},
    )
    item = LibraryItem(user_id=str(user.id), paper_id=str(paper.id), status="planned")
    db_session.add_all([revision, item])
    await db_session.flush()
    existing_settings = TranslationSettings(
        auto_translate_appendix=change != "targets",
        translate_table_cells=False,
    )
    requested_settings = TranslationSettings(
        auto_translate_appendix=True,
        translate_table_cells=True,
    )
    existing_plan = build_translation_plan(content, existing_settings, pages=2)
    requested_plan = build_translation_plan(content, requested_settings, pages=2)
    shared = TranslationSet(
        revision_id=str(revision.id),
        style="natural",
        scope="shared",
        plan=existing_plan.model_dump(mode="json"),
        status="complete",
    )
    job = Job(
        kind="ingest",
        status="running",
        payload={
            "mode": "initial",
            "source": "arxiv",
            "arxiv_id": paper.arxiv_id,
            "library_item_id": str(item.id),
        },
        user_id=str(user.id),
        paper_id=str(paper.id),
        library_item_id=str(item.id),
    )
    db_session.add_all([shared, job])
    await db_session.commit()

    run = IngestRun(db_session, JobStore(db_session), job, deps_from_ctx(worker_ctx))
    run.content = content
    run.revision_id = str(revision.id)
    await run._reuse_translation_set(shared, requested_plan)

    await db_session.refresh(shared)
    assert shared.status == "partial"
    merged = resolve_translation_plan(content, shared.plan, pages=2)
    if change == "targets":
        assert set(merged.target_block_ids) > set(existing_plan.target_block_ids)
    else:
        assert merged.target_block_ids == existing_plan.target_block_ids
        assert existing_plan.translate_table_cells is False
    assert merged.translate_table_cells is True


async def test_reuse_complete_set_requeues_existing_blocking_units_for_repair(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    content = DocumentContent.model_validate(
        {
            "quality_level": "A",
            "sections": [
                {
                    "id": "sec-main",
                    "heading": {"number": "1", "title": "Main"},
                    "blocks": [
                        {
                            "id": "blk-main",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "Main prose."}],
                        }
                    ],
                },
                {
                    "id": "sec-method",
                    "heading": {"number": "2", "title": "Method"},
                    "blocks": [
                        {
                            "id": "blk-method",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "Method prose."}],
                        }
                    ],
                },
            ],
        }
    )
    user = User(email=f"{uuid.uuid4().hex}@reuse-repair.test")
    paper = Paper(arxiv_id=_arxiv_id(), title="Reuse repair", visibility="public")
    db_session.add_all([user, paper])
    await db_session.flush()
    revision = DocumentRevision(
        paper_id=str(paper.id),
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(mode="json"),
        stats={"pages": 2},
    )
    item = LibraryItem(user_id=str(user.id), paper_id=str(paper.id), status="planned")
    db_session.add_all([revision, item])
    await db_session.flush()
    plan = build_translation_plan(content, TranslationSettings(), pages=2)
    shared = TranslationSet(
        revision_id=str(revision.id),
        style="natural",
        scope="shared",
        plan=plan.model_dump(mode="json"),
        status="complete",
    )
    job = Job(
        kind="ingest",
        status="running",
        payload={
            "mode": "initial",
            "source": "arxiv",
            "arxiv_id": paper.arxiv_id,
            "library_item_id": str(item.id),
        },
        user_id=str(user.id),
        paper_id=str(paper.id),
        library_item_id=str(item.id),
    )
    db_session.add_all([shared, job])
    await db_session.flush()
    blocks_by_id = {block.id: block for _section, block in content.iter_blocks()}
    db_session.add_all(
        [
            TranslationUnit(
                set_id=str(shared.id),
                block_id="blk-main",
                source_hash=encode_block(blocks_by_id["blk-main"].model_dump()).source_hash,
                content_ja=[{"t": "text", "v": "本文"}],
                text_ja="本文",
                state="machine",
                quality_flags=[],
            ),
            TranslationUnit(
                set_id=str(shared.id),
                block_id="blk-method",
                source_hash="blocked-method",
                content_ja=[],
                text_ja="",
                state="machine",
                quality_flags=["placeholder_mismatch"],
            ),
        ]
    )
    await db_session.commit()

    run = IngestRun(db_session, JobStore(db_session), job, deps_from_ctx(worker_ctx))
    run.content = content
    run.revision_id = str(revision.id)
    await run._reuse_translation_set(shared, plan)

    assert run._translation_set_needs_repair is True
    assert run._translation_repair_block_ids == frozenset({"blk-method"})
    await db_session.refresh(shared)
    assert shared.status == "partial"
    job_ids = await run._enqueue_body_jobs(
        ["sec-method"],
        {"sec-method": ["blk-method"]},
        appendix_untranslated=False,
    )
    repair_job = await db_session.get(Job, job_ids[0])
    assert repair_job is not None
    assert repair_job.payload["reason"] == "retry_failed"
    assert repair_job.payload["block_ids"] == ["blk-method"]
    assert repair_job.payload["ingest_job_id"] == str(job.id)


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
    from alinea_core.db.models import User

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
