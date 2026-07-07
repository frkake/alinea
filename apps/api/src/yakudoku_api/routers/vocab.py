"""vocab — 語彙帳 CRUD・AI 生成トリガ・SRS(plans/03 §11、plans/07 §7、docs/11)。

- 語彙帳は用語集([03-translation.md](../../../../../docs/03-translation.md))とは独立の資産
  (docs/11 §1・§10)。本ルータは ``vocab_entries`` のみを扱い、``glossary_terms`` には触れない。
- ``POST /api/vocab`` は保存直後に 201 を返し、AI 生成(8 フィールド。plans/07 §7)は
  ``jobs(kind='vocab')`` へ委譲する(interactive キュー。:mod:`yakudoku_worker.tasks.
  generate_vocab_ai` が処理する。worker への登録行は followups 参照)。
- 重複判定は ``term`` の正規化一致(trim + 小文字化。docs/11 §2)。DB 側は
  ``uq_vocab_entries_user_term``(``user_id, lower(term)``)が二重の安全網になる。
- SRS は :mod:`yakudoku_api.services.srs_service`(固定段階方式。docs/11 §7.1)に委譲する。
- 出典表示(``source.display`` / ``anchor.display``)は ``block_search_index`` から
  :mod:`yakudoku_api.chat.evidence` 経由で導出する(注釈・メモと同方式)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.elements import ColumnElement
from yakudoku_core.db.models import LibraryItem, Paper, VocabEntry
from yakudoku_core.jobs.store import JobStore

from yakudoku_api.chat.evidence import EvidenceValidator, load_validator
from yakudoku_api.deps import CurrentUser, DbDep, RedisDep, SettingsDep
from yakudoku_api.errors import PROBLEM_CONTENT_TYPE, ProblemException, build_problem
from yakudoku_api.llm.deps import check_quota
from yakudoku_api.schemas.common import decode_cursor, encode_cursor
from yakudoku_api.schemas.vocab import (
    GENERATION_FIELDS,
    GenerationState,
    VocabAi,
    VocabCounts,
    VocabCreate,
    VocabCreateResponse,
    VocabEntryDetail,
    VocabEntrySummary,
    VocabHighlight,
    VocabListResponse,
    VocabMeaning,
    VocabPatch,
    VocabRegenerateRequest,
    VocabRegenerateResponse,
    VocabReviewHistoryEntry,
    VocabReviewQueueResponse,
    VocabReviewRequest,
    VocabReviewResponse,
    VocabSource,
    VocabSrs,
)
from yakudoku_api.services.deadlines import today_jst
from yakudoku_api.services.srs_service import apply_review, next_review_display

router = APIRouter(tags=["vocab"])
log = structlog.get_logger("yakudoku.api.vocab")

_KINDS = ("word", "collocation", "idiom")
_SORTS = ("added_at", "term")
# plans/01 §4.3(apps/worker/settings.INTERACTIVE_QUEUE と同値。apps 間 import 禁止のため定数で
# 持つ)。
_INTERACTIVE_QUEUE = "yk:interactive"


# ---------------------------------------------------------------------------
# 起床通知(テストで差し替え可能。apps/api/routers/ingest.py の get_job_wakeup と同方針)
# ---------------------------------------------------------------------------
JobWakeup = Callable[[str], Awaitable[None]]


async def _default_wakeup(redis_url: str, job_id: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        await pool.enqueue_job("run_job", job_id, _queue_name=_INTERACTIVE_QUEUE)
    finally:
        await pool.aclose()


def get_vocab_job_wakeup(settings: SettingsDep) -> JobWakeup:
    """arq への起床通知を返す。失敗しても「語彙に追加」自体は成功させる(DB が真実)。"""

    async def wakeup(job_id: str) -> None:
        try:
            await _default_wakeup(settings.redis_url, job_id)
        except Exception:
            await log.awarning("vocab_wakeup_failed", job_id=job_id)

    return wakeup


VocabJobWakeupDep = Annotated[JobWakeup, Depends(get_vocab_job_wakeup)]


# ---------------------------------------------------------------------------
# 所有チェック
# ---------------------------------------------------------------------------
def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _owned_item(db: DbDep, user_id: str, item_id: str) -> LibraryItem:
    if not _valid_uuid(item_id):
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return item


async def _owned_entry(db: DbDep, user_id: str, vocab_id: str) -> VocabEntry:
    if not _valid_uuid(vocab_id):
        raise ProblemException("not_found")
    entry = await db.get(VocabEntry, vocab_id)
    if entry is None or str(entry.user_id) != str(user_id):
        raise ProblemException("not_found")
    return entry


def _normalize_term(term: str) -> str:
    """重複判定用の正規化(trim + 小文字化。docs/11 §2)。"""
    return term.strip().lower()


async def _find_duplicate(db: DbDep, user_id: str, term: str) -> VocabEntry | None:
    normalized = _normalize_term(term)
    return (
        await db.execute(
            select(VocabEntry).where(
                VocabEntry.user_id == user_id, func.lower(VocabEntry.term) == normalized
            )
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# 出典・display の導出(revision/paper 単位でキャッシュ)
# ---------------------------------------------------------------------------
@dataclass
class _SourceCache:
    validators: dict[str, EvidenceValidator] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)


async def _validator_for(db: DbDep, revision_id: str, cache: _SourceCache) -> EvidenceValidator:
    if revision_id not in cache.validators:
        cache.validators[revision_id] = (
            await load_validator(db, revision_id) if revision_id else EvidenceValidator("", [])
        )
    return cache.validators[revision_id]


async def _paper_title_for(db: DbDep, library_item_id: str, cache: _SourceCache) -> str:
    if library_item_id not in cache.titles:
        row = (
            await db.execute(
                select(Paper.title)
                .join(LibraryItem, LibraryItem.paper_id == Paper.id)
                .where(LibraryItem.id == library_item_id)
            )
        ).scalar_one_or_none()
        cache.titles[library_item_id] = str(row) if row else ""
    return cache.titles[library_item_id]


async def _source_for(db: DbDep, entry: VocabEntry, cache: _SourceCache) -> VocabSource:
    anchor = entry.context_anchor if isinstance(entry.context_anchor, dict) else {}
    revision_id = str(anchor.get("revision_id", ""))
    block_id = str(anchor.get("block_id", ""))
    validator = await _validator_for(db, revision_id, cache)
    label = validator.display_for(block_id) or ""
    paper_title = await _paper_title_for(db, str(entry.library_item_id), cache)
    display = f"{paper_title} · {label}" if label else paper_title
    return VocabSource(
        library_item_id=str(entry.library_item_id), paper_title=paper_title, display=display
    )


async def _anchor_ref(db: DbDep, entry: VocabEntry, cache: _SourceCache) -> dict[str, Any]:
    anchor = entry.context_anchor if isinstance(entry.context_anchor, dict) else {}
    revision_id = str(anchor.get("revision_id", ""))
    block_id = str(anchor.get("block_id", ""))
    validator = await _validator_for(db, revision_id, cache)
    display = validator.display_for(block_id) or ""
    return {
        "revision_id": revision_id,
        "block_id": block_id,
        "start": anchor.get("start"),
        "end": anchor.get("end"),
        "quote": anchor.get("quote"),
        "side": anchor.get("side", "source"),
        "display": display,
    }


# ---------------------------------------------------------------------------
# 出力整形(DB VocabEntry → API DTO)
# ---------------------------------------------------------------------------
def _map_generation(status: str) -> GenerationState:
    if status == "complete":
        return "done"
    if status == "failed":
        return "failed"
    return "pending"


def _nullable(value: str) -> str | None:
    return value or None


async def _summary_out(db: DbDep, entry: VocabEntry, cache: _SourceCache) -> VocabEntrySummary:
    source = await _source_for(db, entry, cache)
    return VocabEntrySummary(
        id=str(entry.id),
        kind=entry.kind,  # DB CHECK 制約が値域を保証(0001)
        term=entry.term,
        meaning_short=_nullable(entry.meaning_short),
        source=source,
        added_at=entry.created_at.isoformat(),
        generation=_map_generation(entry.generation_status),
    )


async def _detail_out(db: DbDep, entry: VocabEntry, cache: _SourceCache) -> VocabEntryDetail:
    summary = await _summary_out(db, entry, cache)
    anchor = await _anchor_ref(db, entry, cache)
    context_meaning = (
        VocabMeaning(short=entry.meaning_short, long=entry.meaning_long)
        if (entry.meaning_short or entry.meaning_long)
        else None
    )
    history = [
        VocabReviewHistoryEntry(result=h.get("result", "again"), at=h.get("at", ""))
        for h in (entry.srs_history or [])
        if isinstance(h, dict)
    ]
    ai = VocabAi(
        context_meaning=context_meaning,
        interpretation=_nullable(entry.interpretation),
        etymology=_nullable(entry.etymology),
        mnemonic=_nullable(entry.mnemonic),
        related_expressions=_nullable(entry.related_forms),
        edited_fields=list(entry.edited_fields or []),
        generation_error=entry.generation_error,
    )
    srs = VocabSrs(
        stage=entry.srs_stage,  # DB CHECK 制約が 1..5 を保証(0001)
        next_review_at=None if entry.srs_mastered else entry.srs_next_review_on.isoformat(),
        review_count=entry.srs_review_count,
        history=history,
    )
    return VocabEntryDetail(
        id=summary.id,
        kind=summary.kind,
        term=summary.term,
        meaning_short=summary.meaning_short,
        source=summary.source,
        added_at=summary.added_at,
        generation=summary.generation,
        pos_label=_nullable(entry.pos_label),
        ipa=_nullable(entry.ipa),
        anchor=anchor,  # dict は AnchorRef のフィールドと 1:1
        context_sentence=entry.context_sentence,
        highlight=VocabHighlight(start=entry.context_hl_start, end=entry.context_hl_end),
        ai=ai,
        srs=srs,
    )


# ---------------------------------------------------------------------------
# 一覧・エクスポート共通のフィルタ(§11.1・§11.9 は同一クエリ契約)
# ---------------------------------------------------------------------------
def _validate_kind(kind: list[str] | None) -> None:
    for k in kind or []:
        if k not in _KINDS:
            raise ProblemException("validation_error", detail=f"kind が不正です: {k}")


def _vocab_filters(
    user_id: str,
    *,
    kind: list[str] | None,
    due: bool | None,
    q: str | None,
    library_item_id: str | None,
    today: dt.date,
) -> list[Any]:
    conds: list[Any] = [VocabEntry.user_id == user_id]
    if library_item_id is not None:
        conds.append(VocabEntry.library_item_id == library_item_id)
    if kind:
        conds.append(VocabEntry.kind.in_(kind))
    if due:
        conds.append(VocabEntry.srs_mastered.is_(False))
        conds.append(VocabEntry.srs_next_review_on <= today)
    if q:
        like = f"%{q}%"
        conds.append(
            or_(
                VocabEntry.term.ilike(like),
                VocabEntry.meaning_short.ilike(like),
                VocabEntry.meaning_long.ilike(like),
            )
        )
    return conds


# ---------------------------------------------------------------------------
# 一覧のキーセットページング(§1.5)
# ---------------------------------------------------------------------------
def _vocab_keyset(sort: str, k: Any, last_id: str) -> ColumnElement[bool]:
    if sort == "term":
        id_cmp = VocabEntry.id > last_id
        term_bind = str(k)
        term_col = func.lower(VocabEntry.term)
        return or_(term_col > term_bind, and_(term_col == term_bind, id_cmp))
    id_cmp = VocabEntry.id < last_id
    dt_bind = dt.datetime.fromisoformat(str(k))
    return or_(VocabEntry.created_at < dt_bind, and_(VocabEntry.created_at == dt_bind, id_cmp))


# ============================================================================
# 一覧 + counts(§11.1)
# ============================================================================
@router.get("/api/vocab", response_model=VocabListResponse, operation_id="vocab_list")
async def list_vocab(
    user: CurrentUser,
    db: DbDep,
    kind: Annotated[list[str] | None, Query()] = None,
    due: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    library_item_id: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "added_at",
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> VocabListResponse:
    _validate_kind(kind)
    if sort not in _SORTS:
        raise ProblemException("validation_error", detail="sort は added_at|term のみ有効です")

    today = today_jst()
    base_conds: list[Any] = [VocabEntry.user_id == user.id]
    if library_item_id is not None:
        base_conds.append(VocabEntry.library_item_id == library_item_id)

    all_rows = (
        await db.execute(
            select(VocabEntry.kind, VocabEntry.srs_next_review_on, VocabEntry.srs_mastered).where(
                *base_conds
            )
        )
    ).all()
    counts = VocabCounts(
        all=len(all_rows),
        word=sum(1 for r in all_rows if r[0] == "word"),
        collocation=sum(1 for r in all_rows if r[0] == "collocation"),
        idiom=sum(1 for r in all_rows if r[0] == "idiom"),
        due=sum(1 for r in all_rows if (not r[2]) and r[1] <= today),
    )

    conds = _vocab_filters(
        str(user.id), kind=kind, due=due, q=q, library_item_id=library_item_id, today=today
    )

    total = (
        await db.execute(
            select(func.count()).select_from(select(VocabEntry.id).where(*conds).subquery())
        )
    ).scalar_one()

    asc = sort == "term"
    col = func.lower(VocabEntry.term) if asc else VocabEntry.created_at

    stmt = select(VocabEntry).where(*conds)
    if cursor:
        try:
            data = decode_cursor(cursor)
            stmt = stmt.where(_vocab_keyset(sort, data.get("k"), str(data["id"])))
        except (ValueError, KeyError, TypeError) as exc:
            raise ProblemException("validation_error", detail="カーソルが不正です") from exc

    primary = col.asc() if asc else col.desc()
    id_order = VocabEntry.id.asc() if asc else VocabEntry.id.desc()
    stmt = stmt.order_by(primary, id_order).limit(limit + 1)

    rows = (await db.execute(stmt)).scalars().all()
    has_next = len(rows) > limit
    kept = rows[:limit]

    cache = _SourceCache()
    items = [await _summary_out(db, e, cache) for e in kept]

    next_cursor: str | None = None
    if has_next:
        last = kept[-1]
        key_value = last.term.strip().lower() if asc else last.created_at.isoformat()
        next_cursor = encode_cursor(key_value, str(last.id))

    return VocabListResponse(items=items, next_cursor=next_cursor, total=int(total), counts=counts)


# ============================================================================
# 復習キュー(§11.7。GET /api/vocab/{vocab_id} と競合しないよう先に登録する)
# ============================================================================
@router.get(
    "/api/vocab/review-queue",
    response_model=VocabReviewQueueResponse,
    operation_id="vocab_review_queue",
)
async def review_queue(user: CurrentUser, db: DbDep) -> VocabReviewQueueResponse:
    today = today_jst()
    conds = [
        VocabEntry.user_id == user.id,
        VocabEntry.srs_mastered.is_(False),
        VocabEntry.srs_next_review_on <= today,
    ]
    total = (
        await db.execute(
            select(func.count()).select_from(select(VocabEntry.id).where(*conds).subquery())
        )
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(VocabEntry)
                .where(*conds)
                .order_by(VocabEntry.srs_next_review_on.asc(), VocabEntry.id.asc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    cache = _SourceCache()
    items = [await _detail_out(db, e, cache) for e in rows]
    return VocabReviewQueueResponse(items=items, total=int(total))


# ============================================================================
# Markdown エクスポート(§11.9。docs/11 §9・PY-VOC-08)
# ============================================================================
_KIND_LABEL: dict[str, str] = {
    "word": "単語",
    "collocation": "コロケーション",
    "idiom": "イディオム",
}


def _render_vocab_markdown(entries: list[VocabEntryDetail]) -> str:
    lines: list[str] = ["# 語彙帳", "", f"件数: {len(entries)}", ""]
    for e in entries:
        lines.append(f"## {e.term}({_KIND_LABEL.get(e.kind, e.kind)})")
        lines.append("")
        if e.ai.context_meaning is not None:
            lines.append(f"- 文脈での語義: {e.ai.context_meaning.long}")
        lines.append(f"- 出典: {e.source.display}")
        lines.append(f"- 追加: {e.added_at}")
        lines.append("")
        lines.append(f"> {e.context_sentence}")
        lines.append("")
        if e.ai.interpretation:
            lines.append(f"- 解釈のしかた: {e.ai.interpretation}")
        if e.ai.etymology:
            lines.append(f"- 語源メモ: {e.ai.etymology}")
        if e.ai.mnemonic:
            lines.append(f"- 覚えるコツ: {e.ai.mnemonic}")
        if e.ai.related_expressions:
            lines.append(f"- よく出る形・近い表現: {e.ai.related_expressions}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


@router.get("/api/vocab/export/markdown", operation_id="vocab_export_markdown")
async def export_vocab_markdown(
    user: CurrentUser,
    db: DbDep,
    kind: Annotated[list[str] | None, Query()] = None,
    due: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    library_item_id: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "added_at",
) -> Response:
    _validate_kind(kind)
    if sort not in _SORTS:
        raise ProblemException("validation_error", detail="sort は added_at|term のみ有効です")

    today = today_jst()
    conds = _vocab_filters(
        str(user.id), kind=kind, due=due, q=q, library_item_id=library_item_id, today=today
    )
    asc = sort == "term"
    col = func.lower(VocabEntry.term) if asc else VocabEntry.created_at
    stmt = (
        select(VocabEntry)
        .where(*conds)
        .order_by(col.asc() if asc else col.desc(), VocabEntry.id.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    cache = _SourceCache()
    entries = [await _detail_out(db, e, cache) for e in rows]
    content = _render_vocab_markdown(entries)
    filename = f"yakudoku-vocab-{today.strftime('%Y%m%d')}.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================================
# 作成(§11.2「語彙に追加」)
# ============================================================================
@router.post(
    "/api/vocab",
    response_model=VocabCreateResponse,
    status_code=201,
    operation_id="vocab_create",
)
async def create_vocab(
    body: VocabCreate,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    wakeup: VocabJobWakeupDep,
) -> VocabCreateResponse | JSONResponse:
    await _owned_item(db, user.id, body.library_item_id)

    if body.anchor.side != "source":
        raise ProblemException(
            "validation_error", detail="anchor.side は source のみ対応しています"
        )

    cache = _SourceCache()
    validator = await _validator_for(db, body.anchor.revision_id, cache)
    if validator.display_for(body.anchor.block_id) is None:
        raise ProblemException("validation_error", detail="anchor のブロックが存在しません")

    term = body.term.strip()
    if not term:
        raise ProblemException("validation_error", detail="term は必須です")

    duplicate = await _find_duplicate(db, user.id, term)
    if duplicate is not None:
        problem = build_problem(
            "duplicate",
            status=409,
            title="既に語彙帳にあります",
            instance="/api/vocab",
        )
        content: dict[str, Any] = problem.model_dump(mode="json")
        content["existing"] = {"vocab_id": str(duplicate.id)}
        return JSONResponse(status_code=409, content=content, media_type=PROBLEM_CONTENT_TYPE)

    # クォータ超過は 429(plans/07 §9.2)。BYOK 設定済みならスキップされる(check_quota 内)。
    await check_quota(db, str(user.id), "vocab", settings=settings, cache=r)

    entry = VocabEntry(
        id=str(uuid.uuid4()),
        user_id=str(user.id),
        library_item_id=body.library_item_id,
        term=term,
        context_anchor=body.anchor.model_dump(mode="json"),
        context_sentence=body.context_sentence,
        context_hl_start=body.highlight.start,
        context_hl_end=body.highlight.end,
        # DB 既定 `CURRENT_DATE + 1`(0001)は DB サーバーのタイムゾーン基準で JST の
        # 「翌日」と一致しない場合があるため、明示的に JST の翌日を設定する(docs/11 §7.1)。
        srs_next_review_on=today_jst() + dt.timedelta(days=1),
    )
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError:
        # uq_vocab_entries_user_term: 競合で重複挿入(二重の安全網。docs/11 §2)。
        await db.rollback()
        existing = await _find_duplicate(db, user.id, term)
        if existing is None:
            raise
        problem = build_problem(
            "duplicate", status=409, title="既に語彙帳にあります", instance="/api/vocab"
        )
        content = problem.model_dump(mode="json")
        content["existing"] = {"vocab_id": str(existing.id)}
        return JSONResponse(status_code=409, content=content, media_type=PROBLEM_CONTENT_TYPE)

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="vocab",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=body.library_item_id,
        payload={"vocab_id": str(entry.id), "fields": None},
    )
    await wakeup(job_id)

    detail = await _detail_out(db, entry, cache)
    return VocabCreateResponse(entry=detail, generation_job_id=job_id)


# ============================================================================
# 詳細(§11.3)
# ============================================================================
@router.get("/api/vocab/{vocab_id}", response_model=VocabEntryDetail, operation_id="vocab_get")
async def get_vocab(vocab_id: str, user: CurrentUser, db: DbDep) -> VocabEntryDetail:
    entry = await _owned_entry(db, user.id, vocab_id)
    cache = _SourceCache()
    return await _detail_out(db, entry, cache)


# ============================================================================
# 更新(§11.4)
# ============================================================================
@router.patch("/api/vocab/{vocab_id}", response_model=VocabEntryDetail, operation_id="vocab_update")
async def patch_vocab(
    vocab_id: str, body: VocabPatch, user: CurrentUser, db: DbDep
) -> VocabEntryDetail:
    entry = await _owned_entry(db, user.id, vocab_id)
    provided = body.model_fields_set
    edited: set[str] = set(entry.edited_fields or [])

    if "kind" in provided and body.kind is not None:
        entry.kind = body.kind
        edited.add("kind")
    if "term" in provided and body.term is not None:
        new_term = body.term.strip()
        if new_term:
            entry.term = new_term
    if "pos_label" in provided and body.pos_label is not None:
        entry.pos_label = body.pos_label
        edited.add("pos_label")
    if "ipa" in provided and body.ipa is not None:
        entry.ipa = body.ipa
        edited.add("ipa")

    if body.ai is not None:
        ai_provided = body.ai.model_fields_set
        if "context_meaning" in ai_provided and body.ai.context_meaning is not None:
            entry.meaning_short = body.ai.context_meaning.short
            entry.meaning_long = body.ai.context_meaning.long
            edited.update({"meaning_short", "meaning_long"})
        if "interpretation" in ai_provided and body.ai.interpretation is not None:
            entry.interpretation = body.ai.interpretation
            edited.add("interpretation")
        if "etymology" in ai_provided and body.ai.etymology is not None:
            entry.etymology = body.ai.etymology
            edited.add("etymology")
        if "mnemonic" in ai_provided and body.ai.mnemonic is not None:
            entry.mnemonic = body.ai.mnemonic
            edited.add("mnemonic")
        if "related_expressions" in ai_provided and body.ai.related_expressions is not None:
            entry.related_forms = body.ai.related_expressions
            edited.add("related_forms")

    entry.edited_fields = sorted(edited)
    try:
        await db.commit()
    except IntegrityError as exc:
        # uq_vocab_entries_user_term: term 編集が既存の別エントリと衝突(§11.2 と同判定)。
        await db.rollback()
        raise ProblemException("duplicate", detail="既に語彙帳にある見出し語です") from exc
    await db.refresh(entry)
    cache = _SourceCache()
    return await _detail_out(db, entry, cache)


# ============================================================================
# 削除(§11.5)
# ============================================================================
@router.delete("/api/vocab/{vocab_id}", status_code=204, operation_id="vocab_delete")
async def delete_vocab(vocab_id: str, user: CurrentUser, db: DbDep) -> Response:
    entry = await _owned_entry(db, user.id, vocab_id)
    await db.delete(entry)
    await db.commit()
    return Response(status_code=204)


# ============================================================================
# 再生成(§11.6)
# ============================================================================
@router.post(
    "/api/vocab/{vocab_id}/regenerate",
    response_model=VocabRegenerateResponse,
    status_code=202,
    operation_id="vocab_regenerate",
)
async def regenerate_vocab(
    vocab_id: str,
    body: VocabRegenerateRequest,
    user: CurrentUser,
    db: DbDep,
    wakeup: VocabJobWakeupDep,
) -> VocabRegenerateResponse:
    entry = await _owned_entry(db, user.id, vocab_id)

    fields: list[str] | None = None
    if body.fields is not None:
        fields = [f for f in body.fields if f in GENERATION_FIELDS]

    entry.generation_status = "pending"
    entry.generation_error = None
    await db.commit()

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="vocab",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(entry.library_item_id),
        payload={"vocab_id": str(entry.id), "fields": fields},
    )
    await wakeup(job_id)
    return VocabRegenerateResponse(job_id=job_id)


# ============================================================================
# 自己評価(§11.8。SRS)
# ============================================================================
@router.post(
    "/api/vocab/{vocab_id}/review",
    response_model=VocabReviewResponse,
    operation_id="vocab_review",
)
async def review_vocab(
    vocab_id: str, body: VocabReviewRequest, user: CurrentUser, db: DbDep
) -> VocabReviewResponse:
    entry = await _owned_entry(db, user.id, vocab_id)
    today = today_jst()
    now_iso = dt.datetime.now(dt.UTC).isoformat()

    state = apply_review(
        stage=entry.srs_stage,
        mastered=entry.srs_mastered,
        review_count=entry.srs_review_count,
        result=body.result,
        today=today,
    )
    entry.srs_stage = state.stage
    if state.next_review_on is not None:
        # 習得済み(next_review_on=None)は DB 列(NOT NULL)を変更しない(display は
        # srs_mastered で判定するため、残置された日付は API 応答に現れない)。
        entry.srs_next_review_on = state.next_review_on
    entry.srs_mastered = state.mastered
    entry.srs_review_count = state.review_count
    entry.srs_history = [*(entry.srs_history or []), {"result": body.result, "at": now_iso}]
    await db.commit()

    srs = VocabSrs(
        stage=entry.srs_stage,  # DB CHECK 制約が 1..5 を保証(0001)
        next_review_at=None if entry.srs_mastered else entry.srs_next_review_on.isoformat(),
        review_count=entry.srs_review_count,
        history=[
            VocabReviewHistoryEntry(result=h.get("result", "again"), at=h.get("at", ""))
            for h in entry.srs_history
            if isinstance(h, dict)
        ],
    )
    display = next_review_display(state, today=today)
    return VocabReviewResponse(srs=srs, next_review_display=display)


__all__ = ["router"]
