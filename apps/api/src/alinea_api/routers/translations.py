"""translations — 翻訳セット取得・ユニット・優先繰り上げ・オンデマンド翻訳・再翻訳、
および読書位置の保存(plans/03 §7・§5.8)。認証はすべて `session`。

翻訳系ジョブは DB では `jobs.kind='translation'` 1 種で、用途は `payload.reason` で判別する
(plans/06 §3.1)。API はジョブを作成して job_id を返すところまでを担い、実行は worker(§21)。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import structlog
from alinea_core.db.models import (
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.document.plaintext import block_to_plain
from alinea_core.jobs.store import JobStore
from alinea_core.translation import (
    CanonicalTableGrid,
    TableTranslationContent,
    content_to_text_ja,
    parse_table_grid,
    translation_unit_satisfies_block,
    validate_table_translation_content,
)
from alinea_core.translation import glossary as glossary_core
from alinea_core.translation.glossary import glossary_hash
from alinea_core.translation.pipeline import (
    BLOCKING_FLAGS,
    TranslationPlan,
    TranslationSettings,
    build_translation_plan,
    compute_progress,
    compute_translation_scope,
    merge_translation_plans,
    resolve_display_units,
    resolve_effective_translation_plan,
    resolve_translation_plan,
    resolve_translation_set_units,
    run_quality_checks,
    select_translation_plan_sections,
    translation_execution_scope_from_plan,
    translation_plan_awaits_section_selection,
    translation_scope_from_plan,
)
from alinea_core.translation.placeholder import encode_block
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.deps import CurrentUser, DbDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.routers.viewer import (
    resolve_accessible_revision,
    resolve_owned_library_item,
)
from alinea_api.schemas.viewer import (
    PositionRequest,
    PositionResponse,
    PrioritizeRequest,
    PrioritizeResponse,
    RetranslateRequest,
    RetranslateResponse,
    RetryFailedTranslationsRequest,
    RetryFailedTranslationsResponse,
    SectionSelectionRequest,
    SectionSelectionResponse,
    SectionTranslateRequest,
    SectionTranslateResponse,
    TranslationSetItem,
    TranslationsListResponse,
    TranslationUnitItem,
    UnitProposal,
    UnitsResponse,
)

log = structlog.get_logger("alinea.api.translations")

router = APIRouter(tags=["translations"])

_ON_DEMAND_PRIORITY = 100  # plans/06 §3.1: オンデマンド系は作成時 priority=100(alinea:interactive)
_INTERACTIVE_QUEUE = "alinea:interactive"
_BULK_QUEUE = "alinea:bulk"
_EXACT_WORK_CANDIDATE_LIMIT = 32
_LEGACY_WORK_CANDIDATE_LIMIT = 32
_MAX_WORK_GENERATION = 2_147_483_647
_ACTIVE_WORK_STATUSES = ("queued", "running", "waiting_quota")

_WORK_REASON_BY_KIND = {
    "literal": "literal",
    "easy": "easy",
    "full": "on_demand",
    "table": "table",
    "retry": "retry_failed",
}


# ---------------------------------------------------------------------------
# 起床通知(テストで差し替え可能。apps/api/routers/ingest.py の get_job_wakeup と同方針)
#
# 決定(M2-17 followup): 本ファイルはこれまで `JobStore.enqueue` のみを呼び、arq への起床
# 通知(wakeup)を一切行っていなかった実装バグだった(`enqueue` は PostgreSQL の `jobs` 行を
# 作るだけで、`pool.enqueue_job` を呼ぶ wakeup が無いと worker には一切見えない — 手動の
# `python -m alinea_core.jobs.requeue` を実行するまで `status='queued'` のまま無限に
# 止まる)。PW-07(直訳オンデマンド生成)がこの経路で実際に止まることを確認し、他ルータ
# (ingest.py 等)と同じ規約で追加する(deviations 参照)。
# ---------------------------------------------------------------------------
JobWakeup = Callable[[str, str], Awaitable[None]]


async def _default_wakeup(redis_url: str, job_id: str, queue_name: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        await pool.enqueue_job("run_job", job_id, _queue_name=queue_name)
    finally:
        await pool.aclose()


def get_translations_job_wakeup(settings: SettingsDep) -> JobWakeup:
    """arq への起床通知を返す。失敗しても翻訳ジョブの作成自体は成功させる(DB が真実)。"""

    async def wakeup(job_id: str, queue_name: str) -> None:
        try:
            await _default_wakeup(settings.redis_url, job_id, queue_name)
        except Exception:
            await log.awarning("translations_wakeup_failed", job_id=job_id)

    return wakeup


TranslationsJobWakeupDep = Annotated[JobWakeup, Depends(get_translations_job_wakeup)]


def _queue_for_priority(priority: int) -> str:
    return _INTERACTIVE_QUEUE if priority >= _ON_DEMAND_PRIORITY else _BULK_QUEUE


# --- 解決ヘルパ ---------------------------------------------------------------------


def _as_content(revision: DocumentRevision) -> DocumentContent:
    try:
        content = DocumentContent.model_validate(revision.content)
        compute_translation_scope(content)
    except (TypeError, ValueError) as exc:
        raise ProblemException(
            "validation_error",
            detail="論文の構造化データが不正なため翻訳操作を実行できません。",
        ) from exc
    return content


def _find_section(content: DocumentContent, section_id: str) -> Section | None:
    def walk(sec: Section) -> Section | None:
        if sec.id == section_id:
            return sec
        for sub in sec.sections:
            found = walk(sub)
            if found is not None:
                return found
        return None

    for top in content.sections:
        found = walk(top)
        if found is not None:
            return found
    return None


def _revision_pages(revision: DocumentRevision) -> int | None:
    raw_pages = (revision.stats or {}).get("pages")
    return raw_pages if isinstance(raw_pages, int) and not isinstance(raw_pages, bool) else None


def _with_auxiliary_targets(
    content: DocumentContent,
    plan: TranslationPlan,
    requested_block_ids: list[str],
) -> TranslationPlan:
    """primary を変えず、追加 execution work を文書順の auxiliary へ単調追加する。"""
    auxiliary = (set(plan.auxiliary_block_ids) | set(requested_block_ids)) - set(
        plan.target_block_ids
    )
    canonical = [
        block_id
        for block_id in compute_translation_scope(content).in_scope_block_ids
        if block_id in auxiliary
    ]
    return plan.model_copy(update={"auxiliary_block_ids": canonical})


def _retranslate_blocks_hash(block_ids: list[str]) -> str:
    """Keep the released retranslate idempotency-key encoding stable."""
    payload = ",".join(sorted(block_ids))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()


def _work_request_key(
    set_id: str,
    section_id: str,
    work_kind: str,
    block_ids: list[str],
) -> str:
    identity = {
        "set_id": set_id,
        "section_id": section_id,
        "work_kind": work_kind,
        "block_ids": block_ids,
    }
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
    return f"xlate:work:v1:{digest}"


def _legacy_work_kind(payload: Mapping[str, Any]) -> str | None:
    reason = payload.get("reason")
    if reason == "literal":
        return "literal"
    if reason == "easy":
        return "easy"
    if reason == "table" and payload.get("table_block_id"):
        return "table"
    if reason == "on_demand" and payload.get("table_block_id") is None:
        return "full"
    if reason == "retry_failed":
        return "retry"
    return None


def _legacy_table_marker_matches(
    payload: Mapping[str, Any],
    work_kind: str,
    block_ids: list[str],
) -> bool:
    marker = payload.get("table_block_id")
    if work_kind == "table":
        return len(block_ids) == 1 and marker == block_ids[0]
    return marker is None


def _matches_work_request(
    job: Job,
    *,
    request_key: str,
    set_id: str,
    section_id: str,
    work_kind: str,
    block_ids: list[str],
) -> bool:
    payload = job.payload if isinstance(job.payload, dict) else {}
    stored_request_key = payload.get("request_key")
    if isinstance(stored_request_key, str):
        return stored_request_key == request_key
    stored_ids = payload.get("block_ids")
    return bool(
        str(payload.get("set_id") or "") == set_id
        and str(payload.get("section_id") or "") == section_id
        and _legacy_work_kind(payload) == work_kind
        and isinstance(stored_ids, list)
        and stored_ids == block_ids
        and _legacy_table_marker_matches(payload, work_kind, block_ids)
    )


def _job_generation(job: Job) -> int:
    value = (job.payload or {}).get("generation")
    return value if type(value) is int and 0 <= value <= _MAX_WORK_GENERATION else 0


async def _all_blocks_displayable(
    db: AsyncSession,
    tset: TranslationSet,
    content: DocumentContent,
    block_ids: list[str],
    *,
    require_table_cells: bool,
) -> bool:
    units = await resolve_translation_set_units(db, tset)
    blocks = {block.id: block for _section, block in content.iter_blocks()}
    for block_id in block_ids:
        unit = units.get(block_id)
        block = blocks.get(block_id)
        if unit is None or block is None:
            return False
        if not translation_unit_satisfies_block(
            unit,
            block,
            require_table_cells=require_table_cells,
        ):
            return False
    return True


@dataclass(frozen=True, slots=True)
class _ScheduledWork:
    job_id: str
    should_wake: bool
    effective_priority: int
    queue_name: str


async def _exact_jobs_with_status(
    db: AsyncSession,
    request_key: str,
    statuses: tuple[str, ...],
) -> list[Job]:
    return list(
        (
            await db.execute(
                select(Job)
                .where(
                    text("jobs.kind = 'translation'"),
                    text("(jobs.payload ->> 'request_key') = :request_key"),
                    Job.status.in_(statuses),
                )
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(_EXACT_WORK_CANDIDATE_LIMIT)
                .with_for_update(),
                {"request_key": request_key},
            )
        )
        .scalars()
        .all()
    )


async def _max_exact_generation(db: AsyncSession, request_key: str) -> int | None:
    value = await db.scalar(
        text(
            """
            SELECT max(
                CASE
                    WHEN jsonb_typeof(payload -> 'generation') = 'number'
                     AND (payload ->> 'generation') ~ '^(0|[1-9][0-9]{0,9})$'
                    THEN CASE
                        WHEN (payload ->> 'generation')::bigint <= :max_generation
                        THEN (payload ->> 'generation')::bigint
                        ELSE 0
                    END
                    ELSE 0
                END
            )
            FROM jobs
            WHERE kind = 'translation'
              AND (payload ->> 'request_key') = :request_key
            """
        ),
        {
            "request_key": request_key,
            "max_generation": _MAX_WORK_GENERATION,
        },
    )
    return int(value) if value is not None else None


async def _legacy_work_candidates(
    db: AsyncSession,
    *,
    set_id: str,
    section_id: str,
    work_kind: str,
    block_ids: list[str],
) -> list[Job]:
    block_ids_json = json.dumps(block_ids, ensure_ascii=False, separators=(",", ":"))
    marker_clause = (
        text("(jobs.payload ->> 'table_block_id') = :table_block_id")
        if work_kind == "table"
        else text("(jobs.payload ->> 'table_block_id') IS NULL")
    )
    params: dict[str, Any] = {
        "set_id": set_id,
        "section_id": section_id,
        "reason": _WORK_REASON_BY_KIND[work_kind],
        "block_ids": block_ids_json,
    }
    if work_kind == "table":
        params["table_block_id"] = block_ids[0]
    return list(
        (
            await db.execute(
                select(Job)
                .where(
                    text("jobs.kind = 'translation'"),
                    text("(jobs.payload ->> 'request_key') IS NULL"),
                    text("(jobs.payload ->> 'set_id') = :set_id"),
                    text("(jobs.payload ->> 'section_id') = :section_id"),
                    text("(jobs.payload ->> 'reason') = :reason"),
                    text(
                        "md5((jobs.payload -> 'block_ids')::text) = "
                        "md5(CAST(:block_ids AS jsonb)::text)"
                    ),
                    text("(jobs.payload -> 'block_ids') = CAST(:block_ids AS jsonb)"),
                    marker_clause,
                )
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(_LEGACY_WORK_CANDIDATE_LIMIT)
                .with_for_update(),
                params,
            )
        )
        .scalars()
        .all()
    )


async def _schedule_translation_work(
    db: AsyncSession,
    store: JobStore,
    *,
    tset: TranslationSet,
    content: DocumentContent,
    section_id: str,
    work_kind: str,
    block_ids: list[str],
    require_table_cells: bool,
    priority: int,
    user_id: str,
    paper_id: str,
    library_item_id: str | None,
    payload: dict[str, Any],
) -> _ScheduledWork:
    """同一 work を再利用し、terminal incomplete のみ次世代を flush する。"""
    set_id = str(tset.id)
    request_key = _work_request_key(set_id, section_id, work_kind, block_ids)

    def reused_work(job: Job) -> _ScheduledWork:
        # pickup 待ちの queued だけを単調に優先化して再通知する。running は既に実行中、
        # waiting_quota はクォータ解除側が再開するため重複 wakeup を送らない。
        if job.status == "queued":
            job.priority = max(job.priority, priority)
        effective_priority = int(job.priority)
        return _ScheduledWork(
            job_id=str(job.id),
            should_wake=job.status == "queued",
            effective_priority=effective_priority,
            queue_name=_queue_for_priority(effective_priority),
        )

    active = await _exact_jobs_with_status(db, request_key, _ACTIVE_WORK_STATUSES)
    if active:
        reused = max(active, key=lambda job: (_job_generation(job), job.created_at, str(job.id)))
        return reused_work(reused)

    succeeded = await _exact_jobs_with_status(db, request_key, ("succeeded",))
    if succeeded and await _all_blocks_displayable(
        db,
        tset,
        content,
        block_ids,
        require_table_cells=require_table_cells,
    ):
        reused = max(
            succeeded,
            key=lambda job: (_job_generation(job), job.created_at, str(job.id)),
        )
        return reused_work(reused)

    max_generation = await _max_exact_generation(db, request_key)
    if max_generation is None:
        legacy = await _legacy_work_candidates(
            db,
            set_id=set_id,
            section_id=section_id,
            work_kind=work_kind,
            block_ids=block_ids,
        )
        matching = [
            job
            for job in legacy
            if _matches_work_request(
                job,
                request_key=request_key,
                set_id=set_id,
                section_id=section_id,
                work_kind=work_kind,
                block_ids=block_ids,
            )
        ]
        active = [job for job in matching if job.status in _ACTIVE_WORK_STATUSES]
        if active:
            reused = max(
                active,
                key=lambda job: (_job_generation(job), job.created_at, str(job.id)),
            )
            return reused_work(reused)
        succeeded = [job for job in matching if job.status == "succeeded"]
        if succeeded and await _all_blocks_displayable(
            db,
            tset,
            content,
            block_ids,
            require_table_cells=require_table_cells,
        ):
            reused = max(
                succeeded,
                key=lambda job: (_job_generation(job), job.created_at, str(job.id)),
            )
            return reused_work(reused)
        max_generation = max((_job_generation(job) for job in matching), default=-1)

    if max_generation >= _MAX_WORK_GENERATION:
        raise ProblemException(
            "conflict",
            detail="翻訳作業の再試行世代が上限に達しています。",
        )
    generation = max_generation + 1
    job_id = await store.enqueue_uncommitted(
        kind="translation",
        priority=priority,
        user_id=user_id,
        paper_id=paper_id,
        library_item_id=library_item_id,
        idempotency_key=f"{request_key}:g{generation}",
        payload={
            **payload,
            "request_key": request_key,
            "generation": generation,
        },
    )
    return _ScheduledWork(
        job_id=job_id,
        should_wake=True,
        effective_priority=priority,
        queue_name=_queue_for_priority(priority),
    )


async def _set_with_access(
    db: AsyncSession, set_id: str, user: User
) -> tuple[TranslationSet, DocumentRevision, Paper]:
    tset = await db.get(TranslationSet, set_id)
    if tset is None:
        raise ProblemException("not_found")
    if tset.scope == "personal":
        if tset.user_id is None or str(tset.user_id) != str(user.id):
            raise ProblemException("not_found")
    elif tset.scope != "shared":
        raise ProblemException("not_found")
    revision, paper = await resolve_accessible_revision(db, str(tset.revision_id), user)
    return tset, revision, paper


async def _user_library_item_id(db: AsyncSession, user: User, paper_id: str) -> str | None:
    li = await db.scalar(
        select(LibraryItem.id).where(
            LibraryItem.user_id == user.id, LibraryItem.paper_id == paper_id
        )
    )
    return str(li) if li else None


async def _effective_set_id(
    db: AsyncSession, revision_id: str, style: str, user_id: str
) -> TranslationSet | None:
    rows = (
        (
            await db.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == revision_id,
                    TranslationSet.style == style,
                    or_(TranslationSet.scope == "shared", TranslationSet.user_id == user_id),
                )
            )
        )
        .scalars()
        .all()
    )
    personal = next((s for s in rows if s.scope == "personal"), None)
    return personal or next((s for s in rows if s.scope == "shared"), None)


def _strict_stored_plan(
    content: DocumentContent,
    tset: TranslationSet,
    *,
    pages: int | None,
) -> TranslationPlan:
    if not isinstance(tset.plan, dict):
        raise ProblemException("conflict", detail="翻訳対象の選択状態が壊れています。")
    try:
        candidate = TranslationPlan.model_validate(tset.plan)
    except ValidationError as exc:
        raise ProblemException(
            "conflict",
            detail="翻訳対象の選択状態が壊れています。",
        ) from exc
    if resolve_translation_plan(content, candidate, pages=pages) != candidate:
        raise ProblemException("conflict", detail="翻訳対象の選択状態が壊れています。")
    return candidate


def _selection_checkpoint(job: Job) -> dict[str, Any] | None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    checkpoints = payload.get("_checkpoint")
    if not isinstance(checkpoints, dict):
        return None
    selection = checkpoints.get("section_selection")
    return dict(selection) if isinstance(selection, dict) else None


def _replace_selection_checkpoint(job: Job, checkpoint: dict[str, Any]) -> None:
    payload = dict(job.payload) if isinstance(job.payload, dict) else {}
    checkpoints = payload.get("_checkpoint")
    updated = dict(checkpoints) if isinstance(checkpoints, dict) else {}
    updated["section_selection"] = checkpoint
    job.payload = {**payload, "_checkpoint": updated}


async def _selection_ingest_job(
    db: AsyncSession,
    *,
    user: User,
    paper: Paper,
    revision: DocumentRevision,
    tset: TranslationSet,
) -> tuple[Job, dict[str, Any]]:
    library_item_id = await _user_library_item_id(db, user, str(paper.id))
    if library_item_id is None:
        raise ProblemException("not_found")
    rows = (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.kind == "ingest",
                    Job.user_id == user.id,
                    Job.paper_id == paper.id,
                    Job.library_item_id == library_item_id,
                )
                .order_by(Job.created_at.desc(), Job.id.desc())
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for job in rows:
        checkpoint = _selection_checkpoint(job)
        if (
            checkpoint is not None
            and checkpoint.get("set_id") == str(tset.id)
            and checkpoint.get("revision_id") == str(revision.id)
        ):
            return job, checkpoint
    raise ProblemException("conflict", detail="セクション選択待ちの取り込みがありません。")


@router.put(
    "/api/translation-sets/{set_id}/section-selection",
    response_model=SectionSelectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_select_sections",
)
async def select_translation_sections(
    set_id: str,
    body: SectionSelectionRequest,
    user: CurrentUser,
    db: DbDep,
    wakeup: TranslationsJobWakeupDep,
) -> SectionSelectionResponse:
    tset, revision, paper = await _set_with_access(db, set_id, user)
    if tset.scope != "personal" or str(tset.user_id) != str(user.id):
        raise ProblemException(
            "conflict",
            detail="共有翻訳セットの対象範囲は個別に変更できません。",
        )
    await db.refresh(tset, with_for_update=True)
    content = _as_content(revision)
    pages = _revision_pages(revision)
    stored_plan = _strict_stored_plan(content, tset, pages=pages)
    job, checkpoint = await _selection_ingest_job(
        db,
        user=user,
        paper=paper,
        revision=revision,
        tset=tset,
    )

    checkpoint_status = checkpoint.get("status")
    if checkpoint_status == "accepted":
        raw_accepted = checkpoint.get("plan")
        try:
            accepted = TranslationPlan.model_validate(raw_accepted)
        except ValidationError as exc:
            raise ProblemException(
                "conflict",
                detail="受理済みのセクション選択が壊れています。",
            ) from exc
        pending = accepted.model_copy(
            update={
                "target_section_ids": [],
                "target_block_ids": [],
                "auxiliary_block_ids": [],
            }
        )
        try:
            requested = select_translation_plan_sections(content, pending, body.section_ids)
        except ValueError as exc:
            raise ProblemException("validation_error", detail=str(exc)) from exc
        stored_primary = stored_plan.model_copy(update={"auxiliary_block_ids": []})
        if requested != accepted or stored_primary != accepted:
            raise ProblemException(
                "conflict",
                detail="セクション選択はすでに確定しています。",
            )
        should_wake = job.status == "queued"
        selected = accepted
    elif checkpoint_status == "pending":
        if job.status != "waiting_input" or not translation_plan_awaits_section_selection(
            content, stored_plan
        ):
            raise ProblemException("conflict", detail="セクション選択は受け付けられません。")
        try:
            selected = select_translation_plan_sections(content, stored_plan, body.section_ids)
        except ValueError as exc:
            raise ProblemException("validation_error", detail=str(exc)) from exc
        tset.plan = selected.model_dump(mode="json")
        tset.status = "pending"
        _replace_selection_checkpoint(
            job,
            {
                "status": "accepted",
                "set_id": str(tset.id),
                "revision_id": str(revision.id),
                "plan": selected.model_dump(mode="json"),
            },
        )
        job.status = "queued"
        job.error = None
        job.finished_at = None
        job.next_retry_at = None
        should_wake = True
    else:
        raise ProblemException("conflict", detail="セクション選択状態が不正です。")

    await db.commit()
    if should_wake:
        try:
            await wakeup(str(job.id), _BULK_QUEUE)
        except Exception as exc:  # DB is authoritative; queued work remains recoverable.
            await log.awarning(
                "section_selection_wakeup_failed",
                job_id=str(job.id),
                error=str(exc),
            )
    return SectionSelectionResponse(
        set_id=str(tset.id),
        job_id=str(job.id),
        section_ids=list(selected.target_section_ids),
    )


# --- §7.1 翻訳セット一覧 ------------------------------------------------------------


@router.get(
    "/api/revisions/{revision_id}/translations",
    response_model=TranslationsListResponse,
    operation_id="translations_list_sets",
)
async def list_translation_sets(
    revision_id: str, user: CurrentUser, db: DbDep
) -> TranslationsListResponse:
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    content = _as_content(revision)
    raw_pages = (revision.stats or {}).get("pages")
    pages = raw_pages if isinstance(raw_pages, int) and not isinstance(raw_pages, bool) else None

    sets = (
        (
            await db.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == revision_id,
                    or_(
                        TranslationSet.scope == "shared",
                        TranslationSet.user_id == user.id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    items: list[TranslationSetItem] = []
    for tset in sets:
        in_scope = set(
            translation_scope_from_plan(content, tset.plan, pages=pages).in_scope_block_ids
        )
        units = await resolve_translation_set_units(db, tset)
        scoped = [
            {"quality_flags": unit.quality_flags}
            for block_id, unit in units.items()
            if block_id in in_scope
        ]
        items.append(
            TranslationSetItem(
                set_id=str(tset.id),
                style=tset.style,
                scope=tset.scope,
                status="complete" if not in_scope else tset.status,
                progress_pct=compute_progress(scoped, len(in_scope)),
                glossary_snapshot_id=glossary_hash(list(tset.glossary_snapshot or [])),
            )
        )
    return TranslationsListResponse(items=items)


# --- §7.2 翻訳ユニット --------------------------------------------------------------


@router.get(
    "/api/revisions/{revision_id}/translations/{style}/units",
    response_model=UnitsResponse,
    operation_id="translations_list_units",
)
async def list_units(
    revision_id: str,
    style: str,
    section_id: str,
    user: CurrentUser,
    db: DbDep,
) -> UnitsResponse:
    if style not in ("natural", "literal", "easy"):
        raise ProblemException("validation_error", detail="style は natural / literal / easy のみ")
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    tset = await _effective_set_id(db, revision_id, style, str(user.id))
    if tset is None:
        raise ProblemException("not_found")

    content = _as_content(revision)
    section = _find_section(content, section_id)
    if section is None:
        raise ProblemException("not_found")
    section_block_ids = [b.id for b in section.blocks]

    units = await resolve_display_units(db, revision_id, style, str(user.id))
    items: list[TranslationUnitItem] = []
    for bid in section_block_ids:
        unit = units.get(bid)
        if unit is None:
            continue  # ユニット未生成のブロックはクライアントが原文で合成する(§7.2)
        flags = list(unit.quality_flags or [])
        blocked = bool(set(flags) & BLOCKING_FLAGS)
        proposal = None
        if isinstance(unit.proposal, dict) and unit.proposal:
            proposal = UnitProposal(
                text_ja=str(unit.proposal.get("text_ja", "")),
                generated_at=str(unit.proposal.get("generated_at", "")),
                model=str(unit.proposal.get("model", "")),
            )
        items.append(
            TranslationUnitItem(
                unit_id=str(unit.id),
                block_id=bid,
                text_ja=None if blocked else unit.text_ja,
                content_ja=None if blocked else unit.content_ja,
                state=unit.state,
                quality_flags=flags,
                proposal=proposal,
            )
        )
    return UnitsResponse(set_id=str(tset.id), items=items)


# --- §7.3 直訳のオンデマンド生成開始(plans/06 §10.2) ------------------------------


class LiteralTranslationRequest(BaseModel):
    style: Literal["literal"]
    priority_section_id: str | None = None


class LiteralTranslationResponse(BaseModel):
    set_id: str
    job_id: str | None


async def _create_literal_set(
    db: AsyncSession,
    revision: DocumentRevision,
    paper: Paper,
    user: User,
    plan: TranslationPlan,
) -> TranslationSet:
    """style='literal' の TranslationSet を未commitで確保する。

    public 論文は shared(全ユーザー共通)、private は personal。用語スナップショットは
    §8.1 の 3 層マージ(shared 構築時は global のみ)。一意インデックス
    (``uq_translation_sets_shared`` / ``uq_translation_sets_personal``)への競合は
    SAVEPOINT 内の INSERT だけを戻して既存行を再取得する。外側の呼び出し元が plan と
    jobs をまとめて commit / rollback する。
    """
    revision_id = str(revision.id)
    library_item_id = await _user_library_item_id(db, user, str(paper.id))
    shared = paper.visibility == "public"
    snapshot, _ghash = await glossary_core.build_snapshot(
        db, user_id=str(user.id), library_item_id=library_item_id, shared=shared
    )
    tset = TranslationSet(
        revision_id=revision_id,
        style="literal",
        scope="shared" if shared else "personal",
        user_id=None if shared else str(user.id),
        glossary_snapshot=snapshot,
        status="pending",
        plan=plan.model_dump(mode="json"),
    )
    try:
        async with db.begin_nested():
            db.add(tset)
            await db.flush()
    except IntegrityError:
        existing = await _effective_set_id(db, revision_id, "literal", str(user.id))
        if existing is None:
            raise
        return existing
    return tset


# --- §7.3b やさしい訳のオンデマンド生成開始(S11 M3) ----------------------------


class EasyTranslationRequest(BaseModel):
    style: Literal["easy"]
    priority_section_id: str | None = None


# easy / literal は単一エンドポイントを共有し、レスポンス形も同一(set_id / job_id)なので
# ``LiteralTranslationResponse`` を両スタイルで再利用する(専用の easy 版は設けない)。


async def _create_easy_set(
    db: AsyncSession,
    revision: DocumentRevision,
    paper: Paper,
    user: User,
    plan: TranslationPlan,
) -> TranslationSet:
    """style='easy' の TranslationSet を未commitで確保する。

    _create_literal_set と同一ロジック。style 文字列のみ異なる。
    """
    revision_id = str(revision.id)
    library_item_id = await _user_library_item_id(db, user, str(paper.id))
    shared = paper.visibility == "public"
    snapshot, _ghash = await glossary_core.build_snapshot(
        db, user_id=str(user.id), library_item_id=library_item_id, shared=shared
    )
    tset = TranslationSet(
        revision_id=revision_id,
        style="easy",
        scope="shared" if shared else "personal",
        user_id=None if shared else str(user.id),
        glossary_snapshot=snapshot,
        status="pending",
        plan=plan.model_dump(mode="json"),
    )
    try:
        async with db.begin_nested():
            db.add(tset)
            await db.flush()
    except IntegrityError:
        existing = await _effective_set_id(db, revision_id, "easy", str(user.id))
        if existing is None:
            raise
        return existing
    return tset


# FastAPI は同一 path+method に複数ルートを登録できないため、literal / easy を単一エンドポイントで
# 受け取り body.style で内部ディスパッチする。Pydantic v2 の discriminated union を使い、
# style フィールド値により自動選択する。
_OnDemandTranslationRequest = Annotated[
    LiteralTranslationRequest | EasyTranslationRequest,
    Field(discriminator="style"),
]


@router.post(
    "/api/revisions/{revision_id}/translations",
    response_model=LiteralTranslationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_start_easy",
)
async def start_literal_translation(
    revision_id: str,
    body: _OnDemandTranslationRequest,
    user: CurrentUser,
    db: DbDep,
    response: Response,
    wakeup: TranslationsJobWakeupDep,
) -> LiteralTranslationResponse:
    style = body.style  # "literal" or "easy"
    revision, paper = await resolve_accessible_revision(db, revision_id, user)
    content = _as_content(revision)
    raw_pages = (revision.stats or {}).get("pages")
    pages = raw_pages if isinstance(raw_pages, int) and not isinstance(raw_pages, bool) else None
    requested_plan = build_translation_plan(
        content,
        TranslationSettings.from_user_settings(user.settings),
        pages=pages,
    )

    tset = await _effective_set_id(db, revision_id, style, str(user.id))
    if tset is None:
        if style == "easy":
            tset = await _create_easy_set(db, revision, paper, user, requested_plan)
        else:
            tset = await _create_literal_set(db, revision, paper, user, requested_plan)

    # public の shared set は異なるユーザーから同時に再利用される。既存対象を失わないよう
    # 行ロック下で単調に統合し、対象が増えた complete set だけを再開する。
    await db.refresh(tset, with_for_update=True)
    stored_plan = resolve_translation_plan(content, tset.plan, pages=pages)
    merged_plan = merge_translation_plans(
        content,
        tset.plan,
        requested_plan,
        pages=pages,
    )
    expanded = set(merged_plan.target_block_ids) > set(stored_plan.target_block_ids)
    tset.plan = merged_plan.model_dump(mode="json")
    scope = translation_scope_from_plan(content, merged_plan, pages=pages)
    if not scope.in_scope_block_ids:
        tset.status = "complete"
    elif tset.status == "complete":
        primary_displayable = await _all_blocks_displayable(
            db,
            tset,
            content,
            list(scope.in_scope_block_ids),
            require_table_cells=merged_plan.translate_table_cells,
        )
        if expanded or not primary_displayable:
            tset.status = "partial"

    if tset.status == "complete":
        # 2 回目以降の切替は即時(plans/06 §10.2 手順1)。
        await db.commit()
        response.status_code = status.HTTP_200_OK
        return LiteralTranslationResponse(set_id=str(tset.id), job_id=None)

    scheduled_by_section: dict[str, _ScheduledWork] = {}
    try:
        library_item_id = await _user_library_item_id(db, user, str(paper.id))
        store = JobStore(db)
        for sec in scope.sections:
            section_id = str(sec["section_id"])
            block_ids = list(sec["block_ids"])
            # 表示中セクション優先(alinea:interactive 相当)。
            # 残りはセクション順(alinea:bulk 相当。§10.2 手順3)。
            priority = _ON_DEMAND_PRIORITY if section_id == body.priority_section_id else 0
            scheduled_by_section[section_id] = await _schedule_translation_work(
                db,
                store,
                tset=tset,
                content=content,
                section_id=section_id,
                work_kind=style,
                block_ids=block_ids,
                require_table_cells=merged_plan.translate_table_cells,
                priority=priority,
                user_id=str(user.id),
                paper_id=str(paper.id),
                library_item_id=library_item_id,
                payload={
                    "set_id": str(tset.id),
                    "section_id": section_id,
                    "block_ids": block_ids,
                    "reason": style,
                    "table_block_id": None,
                },
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    # 起床通知(§4.5 と同じ理由: enqueue だけでは worker に見えない。deviations 参照)。
    for scheduled in scheduled_by_section.values():
        if scheduled.should_wake:
            await wakeup(scheduled.job_id, scheduled.queue_name)

    representative_work = (
        scheduled_by_section.get(body.priority_section_id) if body.priority_section_id else None
    ) or next(iter(scheduled_by_section.values()), None)
    return LiteralTranslationResponse(
        set_id=str(tset.id),
        job_id=representative_work.job_id if representative_work is not None else None,
    )


# --- §7.4 開いたセクションを優先翻訳 -----------------------------------------------


async def _bump_priority(db: AsyncSession, set_ids: list[str], section_id: str) -> list[str]:
    """該当する queued な翻訳ジョブの priority を +100 する(plans/06 §10.1)。返り値=job_id 列。"""
    if not set_ids:
        return []
    result = await db.execute(
        text(
            "UPDATE jobs SET priority = priority + 100 "
            "WHERE kind = 'translation' AND status = 'queued' "
            "AND payload->>'set_id' = ANY(:set_ids) "
            "AND payload->>'section_id' = :section_id "
            "RETURNING id"
        ),
        {"set_ids": set_ids, "section_id": section_id},
    )
    job_ids = [str(r[0]) for r in result.all()]
    await db.commit()
    return job_ids


@router.post(
    "/api/translation-sets/{set_id}/prioritize",
    response_model=PrioritizeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_prioritize",
)
async def prioritize(
    set_id: str, body: PrioritizeRequest, user: CurrentUser, db: DbDep, response: Response
) -> PrioritizeResponse:
    tset, _revision, _paper = await _set_with_access(db, set_id, user)
    await _bump_priority(db, [str(tset.id)], body.section_id)
    # 該当ジョブなし(実行中・完了・対象外)は 202 のまま no-op(§10.1)。
    response.status_code = status.HTTP_202_ACCEPTED
    return PrioritizeResponse(ok=True)


# --- §7.5 付録等のオンデマンド翻訳 -------------------------------------------------


@router.post(
    "/api/translation-sets/{set_id}/sections/{section_id}/translate",
    response_model=SectionTranslateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_section_translate",
)
async def section_translate(
    set_id: str,
    section_id: str,
    body: SectionTranslateRequest,
    user: CurrentUser,
    db: DbDep,
    wakeup: TranslationsJobWakeupDep,
) -> SectionTranslateResponse:
    tset, revision, paper = await _set_with_access(db, set_id, user)
    content = _as_content(revision)
    section = _find_section(content, section_id)
    if section is None:
        raise ProblemException("not_found")

    await db.refresh(tset, with_for_update=True)
    full_scope = compute_translation_scope(content)
    section_scope = next(
        (entry for entry in full_scope.sections if entry["section_id"] == section_id),
        None,
    )
    eligible_ids = list(section_scope["block_ids"]) if section_scope is not None else []

    if body.block_id is not None:
        reason = "table"
        requested_block = next(
            (block for block in section.blocks if block.id == body.block_id), None
        )
        if (
            requested_block is None
            or requested_block.type != "table"
            or requested_block.id not in eligible_ids
        ):
            raise ProblemException(
                "validation_error",
                detail="block_id は指定セクション直下の翻訳可能な table である必要があります。",
            )
        block_ids = [requested_block.id]
        table_block_id: str | None = body.block_id
        source_grid = parse_table_grid(requested_block.raw)
        if not source_grid.supported or not any(
            cell.translatable for row in source_grid.rows for cell in row
        ):
            raise ProblemException(
                "validation_error",
                detail="この表には翻訳可能なセルがありません。",
            )
    else:
        reason = "on_demand"
        block_ids = eligible_ids
        table_block_id = None
        if not block_ids:
            raise ProblemException(
                "validation_error",
                detail="指定セクションには翻訳可能なブロックがありません。",
            )

    pages = _revision_pages(revision)
    stored_plan = resolve_translation_plan(content, tset.plan, pages=pages)
    effective_plan = await resolve_effective_translation_plan(
        db,
        tset,
        content,
        pages=pages,
    )
    effective_ids = set(
        translation_execution_scope_from_plan(
            content,
            effective_plan,
            pages=pages,
        ).in_scope_block_ids
    )
    missing_ids = [block_id for block_id in block_ids if block_id not in effective_ids]
    if missing_ids and not translation_plan_awaits_section_selection(content, stored_plan):
        tset.plan = _with_auxiliary_targets(
            content,
            stored_plan,
            missing_ids,
        ).model_dump(mode="json")

    store = JobStore(db)
    try:
        scheduled = await _schedule_translation_work(
            db,
            store,
            tset=tset,
            content=content,
            section_id=section_id,
            work_kind="table" if table_block_id is not None else "full",
            block_ids=block_ids,
            require_table_cells=(
                table_block_id is not None or effective_plan.translate_table_cells
            ),
            priority=_ON_DEMAND_PRIORITY,
            user_id=str(user.id),
            paper_id=str(paper.id),
            library_item_id=await _user_library_item_id(db, user, str(paper.id)),
            payload={
                "set_id": str(tset.id),
                "section_id": section_id,
                "block_ids": block_ids,
                "reason": reason,
                "table_block_id": table_block_id,
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    if scheduled.should_wake:
        await wakeup(scheduled.job_id, scheduled.queue_name)
    return SectionTranslateResponse(job_id=scheduled.job_id)


# --- §7.6 再翻訳(指示なし。指示つきは M1 で拡張) --------------------------------


@router.post(
    "/api/translation-sets/{set_id}/retry-failed",
    response_model=RetryFailedTranslationsResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_retry_failed",
)
async def retry_failed_translations(
    set_id: str,
    body: RetryFailedTranslationsRequest,
    user: CurrentUser,
    db: DbDep,
    wakeup: TranslationsJobWakeupDep,
) -> RetryFailedTranslationsResponse:
    tset, revision, paper = await _set_with_access(db, set_id, user)
    content = _as_content(revision)
    await db.refresh(tset, with_for_update=True)
    pages = _revision_pages(revision)
    full_scope = compute_translation_scope(content)
    block_section_ids = {
        str(block_id): str(section["section_id"])
        for section in full_scope.sections
        for block_id in section["block_ids"]
    }

    section_filter: str | None = None
    if body.section_id is not None:
        section = _find_section(content, body.section_id)
        if section is None:
            raise ProblemException("not_found")
        section_filter = body.section_id

    stored_plan = resolve_translation_plan(content, tset.plan, pages=pages)
    effective_plan = await resolve_effective_translation_plan(
        db,
        tset,
        content,
        pages=pages,
    )
    effective_ids = set(
        translation_execution_scope_from_plan(
            content,
            effective_plan,
            pages=pages,
        ).in_scope_block_ids
    )
    units = await resolve_translation_set_units(db, tset)
    failed_block_ids = {
        block_id
        for block_id, unit in units.items()
        if block_id in block_section_ids
        and unit.state == "machine"
        and set(unit.quality_flags or []) & BLOCKING_FLAGS
        and (section_filter is None or block_section_ids[block_id] == section_filter)
    }
    if not failed_block_ids:
        return RetryFailedTranslationsResponse(job_ids=[], block_count=0)

    missing_ids = [
        block_id
        for block_id in full_scope.in_scope_block_ids
        if block_id in failed_block_ids and block_id not in effective_ids
    ]
    if missing_ids:
        tset.plan = _with_auxiliary_targets(
            content,
            stored_plan,
            missing_ids,
        ).model_dump(mode="json")

    blocks_by_section = [
        (
            str(section["section_id"]),
            [block_id for block_id in section["block_ids"] if block_id in failed_block_ids],
        )
        for section in full_scope.sections
        if any(block_id in failed_block_ids for block_id in section["block_ids"])
    ]

    store = JobStore(db)
    library_item_id = await _user_library_item_id(db, user, str(paper.id))
    scheduled_work: list[_ScheduledWork] = []
    try:
        for section_id, block_ids in blocks_by_section:
            scheduled_work.append(
                await _schedule_translation_work(
                    db,
                    store,
                    tset=tset,
                    content=content,
                    section_id=section_id,
                    work_kind="retry",
                    block_ids=block_ids,
                    require_table_cells=False,
                    priority=_ON_DEMAND_PRIORITY,
                    user_id=str(user.id),
                    paper_id=str(paper.id),
                    library_item_id=library_item_id,
                    payload={
                        "set_id": str(tset.id),
                        "section_id": section_id,
                        "block_ids": block_ids,
                        "reason": "retry_failed",
                        "table_block_id": None,
                    },
                )
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    for scheduled in scheduled_work:
        if scheduled.should_wake:
            await wakeup(scheduled.job_id, scheduled.queue_name)
    job_ids = [scheduled.job_id for scheduled in scheduled_work]
    return RetryFailedTranslationsResponse(job_ids=job_ids, block_count=len(failed_block_ids))


@router.post(
    "/api/translation-units/{unit_id}/retranslate",
    response_model=RetranslateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="translations_retranslate",
)
async def retranslate(
    unit_id: str,
    body: RetranslateRequest,
    user: CurrentUser,
    db: DbDep,
    wakeup: TranslationsJobWakeupDep,
) -> RetranslateResponse:
    try:
        unit = await db.get(TranslationUnit, int(unit_id))
    except ValueError:
        raise ProblemException("not_found") from None
    if unit is None:
        raise ProblemException("not_found")
    tset, _revision, paper = await _set_with_access(db, str(unit.set_id), user)

    # state=edited への再翻訳は discard_edit 必須(409 conflict / detail edit_protected)。
    if unit.state == "edited" and not body.discard_edit:
        raise ProblemException(
            "conflict",
            detail="edit_protected: 編集済みユニットの再翻訳には discard_edit=true が必要です",
        )

    instruction = (body.instruction or "").strip()
    reason = "instructed" if instruction else "retranslate"
    instruction_hash = (
        hashlib.blake2b(instruction.encode("utf-8"), digest_size=8).hexdigest()
        if instruction
        else "0"
    )

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="translation",
        priority=_ON_DEMAND_PRIORITY,
        user_id=str(user.id),
        paper_id=str(paper.id),
        library_item_id=await _user_library_item_id(db, user, str(paper.id)),
        idempotency_key=(
            f"rexlate:{tset.id}:{_retranslate_blocks_hash([unit.block_id])}:{instruction_hash}"
        ),
        payload={
            "set_id": str(tset.id),
            "block_ids": [unit.block_id],
            "unit_id": str(unit.id),
            "reason": reason,
            "instruction": instruction,
        },
    )
    await wakeup(job_id, _queue_for_priority(_ON_DEMAND_PRIORITY))
    return RetranslateResponse(job_id=job_id)


# --- §7.7 手動編集 -------------------------------------------------------------------


class UnitEditRequest(BaseModel):
    text_ja: str


class UnitEditResponse(BaseModel):
    unit_id: str
    state: str
    text_ja: str
    set_id: str


def _table_text_projection(content: TableTranslationContent) -> str:
    """Derive the only persisted/searchable projection accepted for a typed table."""

    parts: list[str] = []
    if content.caption is not None:
        caption = content_to_text_ja(content.caption)
        if caption:
            parts.append(caption)
    if content.cells is not None:
        parts.extend(cell for row in content.cells for cell in row if cell)
    return "\n".join(parts)


async def _get_unit_or_404(db: AsyncSession, unit_id: str) -> TranslationUnit:
    try:
        unit = await db.get(TranslationUnit, int(unit_id))
    except ValueError:
        raise ProblemException("not_found") from None
    if unit is None:
        raise ProblemException("not_found")
    return unit


async def _ensure_personal_unit(
    db: AsyncSession, tset: TranslationSet, unit: TranslationUnit, user: User
) -> tuple[TranslationSet, TranslationUnit]:
    """共有セットへの書き込みは personal フォーク+ユニット複製を用意する(plans/06 §9.2)。

    ``tset.scope != "shared"`` ならそのまま返す(すでに自分の personal セット)。
    """
    if tset.scope != "shared":
        return tset, unit
    personal_set = await glossary_core.resolve_or_create_personal_set(
        db, revision_id=str(tset.revision_id), style=tset.style, user_id=str(user.id)
    )
    existing = await db.scalar(
        select(TranslationUnit).where(
            TranslationUnit.set_id == personal_set.id, TranslationUnit.block_id == unit.block_id
        )
    )
    if existing is not None:
        return personal_set, existing
    forked = TranslationUnit(
        set_id=str(personal_set.id),
        block_id=unit.block_id,
        source_hash=unit.source_hash,
        content_ja=unit.content_ja,
        text_ja=unit.text_ja,
        state=unit.state,
        quality_flags=list(unit.quality_flags or []),
        proposal=unit.proposal,
        model=unit.model,
    )
    db.add(forked)
    await db.flush()
    return personal_set, forked


@router.put(
    "/api/translation-units/{unit_id}",
    response_model=UnitEditResponse,
    operation_id="translations_edit_unit",
)
async def edit_unit(
    unit_id: str, body: UnitEditRequest, user: CurrentUser, db: DbDep
) -> UnitEditResponse:
    unit = await _get_unit_or_404(db, unit_id)
    tset, revision, _paper = await _set_with_access(db, str(unit.set_id), user)
    target_set, target_unit = await _ensure_personal_unit(db, tset, unit, user)

    persisted_content: list[dict[str, Any]] | dict[str, Any] = [{"t": "text", "v": body.text_ja}]
    persisted_text = body.text_ja
    block = _find_block(_as_content(revision), unit.block_id)
    if block is not None and block.type == "table" and isinstance(target_unit.content_ja, dict):
        grid = parse_table_grid(block.raw)
        current = validate_table_translation_content(target_unit.content_ja, grid)
        if current is not None:
            candidate = validate_table_translation_content(
                {
                    "kind": "table",
                    "version": 1,
                    "caption": [{"t": "text", "v": body.text_ja}],
                    "cells": current.cells,
                },
                grid,
            )
            if candidate is None:
                raise ProblemException(
                    "validation_error",
                    detail="表キャプションを安全な形式で保存できません。",
                )
            persisted_content = candidate.model_dump(mode="json")
            persisted_text = _table_text_projection(candidate)

    # Legacy/invalid table values deliberately retain the historical Inline[] edit contract;
    # the Core table-augmentation path upgrades that caption when cells are requested later.
    target_unit.text_ja = persisted_text
    target_unit.content_ja = persisted_content
    target_unit.state = "edited"
    target_unit.quality_flags = []
    await db.commit()
    return UnitEditResponse(
        unit_id=str(target_unit.id),
        state="edited",
        text_ja=target_unit.text_ja,
        set_id=str(target_set.id),
    )


# --- §7.8 proposal の採用・破棄 ------------------------------------------------------


class ProposalAcceptResponse(BaseModel):
    unit_id: str
    text_ja: str
    state: str


def _find_block(content: DocumentContent, block_id: str) -> Block | None:
    for _sec, blk in content.iter_blocks():
        if blk.id == block_id:
            return blk
    return None


def _recompute_quality_flags(
    revision: DocumentRevision, block_id: str, text_ja: str, snapshot: list[Any]
) -> list[str]:
    """proposal 採用時の品質フラグ再計算(plans/06 §11.2)。プレースホルダ検証は対象外(§11.1 で
    保存前に済んでいるため)。ブロックが見つからない場合はフラグなしとする。
    """
    block = _find_block(_as_content(revision), block_id)
    if block is None:
        return []
    encoded = encode_block(block.model_dump())
    source_plain = block_to_plain(block)
    return run_quality_checks(encoded, source_plain, text_ja, snapshot)


def _without_protected_math(value: str, fragments: list[str]) -> str:
    """Remove each validated math atom once before prose-only quality checks."""

    result = value
    for fragment in fragments:
        result = result.replace(fragment, "", 1)
    return result


def _recompute_table_quality_flags(
    block: Block,
    content: TableTranslationContent,
    grid: CanonicalTableGrid,
    snapshot: list[Any],
) -> list[str]:
    """Re-run quality checks per caption/cell instead of against the aggregate projection."""

    flags: list[str] = []
    if content.caption is not None:
        encoded_caption = encode_block(block.model_dump(mode="json"))
        flags.extend(
            run_quality_checks(
                encoded_caption,
                block_to_plain(block),
                content_to_text_ja(content.caption),
                snapshot,
            )
        )
    if content.cells is not None:
        for source_row, translated_row in zip(grid.rows, content.cells, strict=True):
            for source_cell, translated in zip(source_row, translated_row, strict=True):
                if translated is None:
                    continue
                source_plain = _without_protected_math(source_cell.source, source_cell.math)
                translated_plain = _without_protected_math(translated, source_cell.math)
                encoded_cell = encode_block([{"t": "text", "v": source_plain}])
                flags.extend(
                    run_quality_checks(
                        encoded_cell,
                        source_plain,
                        translated_plain,
                        snapshot,
                    )
                )
    return list(dict.fromkeys(flags))


@router.post(
    "/api/translation-units/{unit_id}/proposal/accept",
    response_model=ProposalAcceptResponse,
    operation_id="translations_accept_proposal",
)
async def accept_proposal(unit_id: str, user: CurrentUser, db: DbDep) -> ProposalAcceptResponse:
    unit = await _get_unit_or_404(db, unit_id)
    tset, revision, _paper = await _set_with_access(db, str(unit.set_id), user)

    proposal = unit.proposal
    if not isinstance(proposal, dict) or not proposal:
        raise ProblemException("not_found", detail="採用可能な proposal がありません")

    text_ja = str(proposal.get("text_ja", ""))
    content_ja = proposal.get("content_ja")
    typed_table: TableTranslationContent | None = None
    table_grid: CanonicalTableGrid | None = None
    block = _find_block(_as_content(revision), unit.block_id)
    if block is not None and block.type == "table":
        table_grid = parse_table_grid(block.raw)
        typed_table = validate_table_translation_content(content_ja, table_grid)
        if typed_table is None:
            raise ProblemException(
                "validation_error",
                detail="表の再翻訳案が現在の表構造と一致しません。",
            )
        content_ja = typed_table.model_dump(mode="json")
        text_ja = _table_text_projection(typed_table)

    # A malformed table proposal is rejected before this point, so it cannot create a fork or
    # replace an existing complete matrix.
    target_set, target_unit = await _ensure_personal_unit(db, tset, unit, user)
    target_unit.text_ja = text_ja
    if content_ja is not None:
        target_unit.content_ja = content_ja
    target_unit.state = "machine"
    if proposal.get("model"):
        target_unit.model = str(proposal["model"])
    target_unit.quality_flags = (
        _recompute_table_quality_flags(
            block,
            typed_table,
            table_grid,
            list(target_set.glossary_snapshot or []),
        )
        if block is not None and typed_table is not None and table_grid is not None
        else _recompute_quality_flags(
            revision, unit.block_id, text_ja, list(target_set.glossary_snapshot or [])
        )
    )
    target_unit.proposal = None
    await db.commit()
    return ProposalAcceptResponse(
        unit_id=str(target_unit.id), text_ja=target_unit.text_ja, state=target_unit.state
    )


@router.delete(
    "/api/translation-units/{unit_id}/proposal",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="translations_discard_proposal",
)
async def discard_proposal(unit_id: str, user: CurrentUser, db: DbDep) -> Response:
    unit = await _get_unit_or_404(db, unit_id)
    await _set_with_access(db, str(unit.set_id), user)
    unit.proposal = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- §5.8 読書位置の自動保存 -------------------------------------------------------


@router.put(
    "/api/library-items/{item_id}/position",
    response_model=PositionResponse,
    operation_id="library_items_save_position",
)
async def save_position(
    item_id: str, body: PositionRequest, user: CurrentUser, db: DbDep
) -> PositionResponse:
    item = await resolve_owned_library_item(db, item_id, user)
    revision, _paper = await resolve_accessible_revision(db, body.revision_id, user)
    if str(revision.paper_id) != str(item.paper_id):
        raise ProblemException("not_found")
    section_id = next(
        (
            section.id
            for section, block in _as_content(revision).iter_blocks()
            if block.id == body.block_id
        ),
        None,
    )
    if section_id is None:
        raise ProblemException("not_found")
    item.reading_position = {
        "revision_id": body.revision_id,
        "block_id": body.block_id,
        "view_mode": body.mode,
    }
    saved_at = dt.datetime.now(dt.UTC)
    await db.commit()

    # 副作用(§10.1-a): 位置のセクションを解決し、当該リビジョンの queued 翻訳ジョブを繰り上げる。
    await _prioritize_from_position(db, str(revision.id), section_id, str(user.id))
    return PositionResponse(saved_at=saved_at.isoformat())


async def _prioritize_from_position(
    db: AsyncSession,
    revision_id: str,
    section_id: str,
    user_id: str,
) -> None:
    set_ids = [
        str(sid)
        for sid in (
            await db.execute(
                select(TranslationSet.id).where(
                    TranslationSet.revision_id == revision_id,
                    or_(
                        TranslationSet.scope == "shared",
                        and_(
                            TranslationSet.scope == "personal",
                            TranslationSet.user_id == user_id,
                        ),
                    ),
                )
            )
        )
        .scalars()
        .all()
    ]
    await _bump_priority(db, set_ids, section_id)
