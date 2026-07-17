"""vocab_candidates — AI 単語抽出(S7)の抽出トリガ・候補一覧・accept/dismiss。

docs/superpowers/specs/2026-07-16-ai-word-extraction-design.md。

- 抽出は on-demand(``POST .../extract`` → ``jobs(kind='vocab_extract')`` を interactive キューへ)。
- accept は候補から本物の ``VocabEntry`` を作り、既存の ``kind='vocab'`` 生成ジョブを積む
  (手動「語彙に追加」と同一の下流フロー)。冪等: 既に accept 済み・既に同語が語彙帳にある場合は
  その既存エントリを返す。
- dismiss は行を残したまま ``status='dismissed'`` にする(同語の再提案を防ぐ。docs/12 §5 と同思想)。
- 語彙帳(``vocab_entries``)は用語集([03-translation.md](../../../../../docs/03-translation.md))
  とは独立(docs/11 §1)。本ルータは ``glossary_terms`` に触れない。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated

import structlog
from alinea_core.db.models import Job, LibraryItem, Paper, VocabCandidate, VocabEntry
from alinea_core.jobs.store import JobStore
from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from alinea_api.chat.evidence import EvidenceValidator, load_validator
from alinea_api.deps import CurrentUser, DbDep, RedisDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.llm.deps import check_quota
from alinea_api.schemas.vocab import VocabHighlight, VocabSource
from alinea_api.schemas.vocab_candidates import (
    VocabCandidateAcceptResponse,
    VocabCandidateListResponse,
    VocabCandidateOut,
    VocabExtractResponse,
)
from alinea_api.services.deadlines import today_jst

router = APIRouter(tags=["vocab"])
log = structlog.get_logger("alinea.api.vocab_candidates")

_INTERACTIVE_QUEUE = "alinea:interactive"
_ACTIVE_JOB_STATUSES = ("queued", "running")


# ---------------------------------------------------------------------------
# 起床通知(vocab.py と同方針。テストで差し替え可能)
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
    async def wakeup(job_id: str) -> None:
        try:
            await _default_wakeup(settings.redis_url, job_id)
        except Exception:
            await log.awarning("vocab_extract_wakeup_failed", job_id=job_id)

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


async def _owned_candidate(db: DbDep, user_id: str, candidate_id: str) -> VocabCandidate:
    if not _valid_uuid(candidate_id):
        raise ProblemException("not_found")
    cand = await db.get(VocabCandidate, candidate_id)
    if cand is None or str(cand.user_id) != str(user_id):
        raise ProblemException("not_found")
    return cand


def _normalize_term(term: str) -> str:
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
# 出典・display の導出(revision/paper 単位でキャッシュ。vocab.py と同型)
# ---------------------------------------------------------------------------
async def _paper_title(db: DbDep, library_item_id: str) -> str:
    row = (
        await db.execute(
            select(Paper.title)
            .join(LibraryItem, LibraryItem.paper_id == Paper.id)
            .where(LibraryItem.id == library_item_id)
        )
    ).scalar_one_or_none()
    return str(row) if row else ""


def _candidate_out(
    cand: VocabCandidate, *, validator: EvidenceValidator, paper_title: str
) -> VocabCandidateOut:
    anchor = cand.context_anchor if isinstance(cand.context_anchor, dict) else {}
    block_id = str(anchor.get("block_id", ""))
    display = validator.display_for(block_id) or ""
    source_display = f"{paper_title} · {display}" if display else paper_title
    return VocabCandidateOut(
        id=str(cand.id),
        term=cand.term,
        kind=cand.kind,  # DB CHECK が値域を保証
        reason=cand.reason or None,
        context_sentence=cand.context_sentence,
        highlight=VocabHighlight(start=cand.context_hl_start, end=cand.context_hl_end),
        anchor={
            "revision_id": anchor.get("revision_id", ""),
            "block_id": block_id,
            "start": anchor.get("start"),
            "end": anchor.get("end"),
            "quote": anchor.get("quote"),
            "side": anchor.get("side", "source"),
            "display": display,
        },
        source=VocabSource(
            library_item_id=str(cand.library_item_id),
            paper_title=paper_title,
            display=source_display,
        ),
        created_at=cand.created_at.isoformat(),
    )


# ============================================================================
# 抽出トリガ(on-demand)
# ============================================================================
@router.post(
    "/api/library-items/{item_id}/vocab-candidates/extract",
    response_model=VocabExtractResponse,
    status_code=202,
    operation_id="vocab_candidates_extract",
)
async def extract_candidates(
    item_id: str,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    wakeup: VocabJobWakeupDep,
) -> VocabExtractResponse:
    item = await _owned_item(db, user.id, item_id)

    # 進行中の抽出ジョブがあれば再利用する(重複抽出しない)。
    active = (
        await db.execute(
            select(Job.id)
            .where(
                Job.kind == "vocab_extract",
                Job.library_item_id == str(item.id),
                Job.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None:
        return VocabExtractResponse(job_id=str(active))

    # クォータは vocab カウンタを共有(docs/11 §8。BYOK 済みならスキップ)。
    await check_quota(db, str(user.id), "vocab", settings=settings, cache=r)

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="vocab_extract",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(item.id),
        payload={"library_item_id": str(item.id)},
    )
    await wakeup(job_id)
    return VocabExtractResponse(job_id=job_id)


# ============================================================================
# 候補一覧(pending のみ)
# ============================================================================
@router.get(
    "/api/library-items/{item_id}/vocab-candidates",
    response_model=VocabCandidateListResponse,
    operation_id="vocab_candidates_list",
)
async def list_candidates(
    item_id: str, user: CurrentUser, db: DbDep
) -> VocabCandidateListResponse:
    item = await _owned_item(db, user.id, item_id)
    rows = (
        (
            await db.execute(
                select(VocabCandidate)
                .where(
                    VocabCandidate.library_item_id == str(item.id),
                    VocabCandidate.status == "pending",
                )
                .order_by(VocabCandidate.created_at.asc(), VocabCandidate.id.asc())
            )
        )
        .scalars()
        .all()
    )
    paper_title = await _paper_title(db, str(item.id))
    validator = await _validator_for_item(db, rows)
    items = [_candidate_out(c, validator=validator, paper_title=paper_title) for c in rows]
    return VocabCandidateListResponse(items=items, count=len(items))


async def _validator_for_item(
    db: DbDep, rows: Sequence[VocabCandidate]
) -> EvidenceValidator:
    """候補は同一 revision 由来なので先頭行の revision_id で validator を 1 本ロードする。"""
    revision_id = ""
    for c in rows:
        anchor = c.context_anchor if isinstance(c.context_anchor, dict) else {}
        revision_id = str(anchor.get("revision_id", ""))
        if revision_id:
            break
    if not revision_id:
        return EvidenceValidator("", [])
    return await load_validator(db, revision_id)


# ============================================================================
# accept: 本物の VocabEntry を作り、生成ジョブを積む
# ============================================================================
@router.post(
    "/api/vocab-candidates/{candidate_id}/accept",
    response_model=VocabCandidateAcceptResponse,
    status_code=201,
    operation_id="vocab_candidates_accept",
)
async def accept_candidate(
    candidate_id: str,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    wakeup: VocabJobWakeupDep,
) -> VocabCandidateAcceptResponse:
    cand = await _owned_candidate(db, user.id, candidate_id)

    # 冪等: 既に accept 済みならリンク先を返す。
    if cand.status == "accepted" and cand.vocab_entry_id is not None:
        return VocabCandidateAcceptResponse(
            vocab_id=str(cand.vocab_entry_id), already_existed=True
        )

    term = cand.term.strip()
    # 既に語彙帳にある語(手動追加・別候補 accept 済み)なら既存を返す(重複を作らない)。
    duplicate = await _find_duplicate(db, str(user.id), term)
    if duplicate is not None:
        cand.status = "accepted"
        cand.vocab_entry_id = str(duplicate.id)
        await db.commit()
        return VocabCandidateAcceptResponse(vocab_id=str(duplicate.id), already_existed=True)

    await check_quota(db, str(user.id), "vocab", settings=settings, cache=r)

    entry = VocabEntry(
        id=str(uuid.uuid4()),
        user_id=str(user.id),
        library_item_id=str(cand.library_item_id),
        kind=cand.kind,
        term=term,
        context_anchor=cand.context_anchor,
        context_sentence=cand.context_sentence,
        context_hl_start=cand.context_hl_start,
        context_hl_end=cand.context_hl_end,
        srs_next_review_on=today_jst() + dt.timedelta(days=1),
    )
    db.add(entry)
    try:
        # entry を先に flush してから候補をリンクする(FK 順序。ORM リレーションは張らない)。
        await db.flush()
        cand.status = "accepted"
        cand.vocab_entry_id = str(entry.id)
        await db.commit()
    except IntegrityError:
        # uq_vocab_entries_user_term: 競合。既存へ寄せる(二重の安全網。docs/11 §2)。
        await db.rollback()
        cand = await _owned_candidate(db, user.id, candidate_id)
        existing = await _find_duplicate(db, str(user.id), term)
        if existing is None:
            raise
        cand.status = "accepted"
        cand.vocab_entry_id = str(existing.id)
        await db.commit()
        return VocabCandidateAcceptResponse(vocab_id=str(existing.id), already_existed=True)

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="vocab",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(cand.library_item_id),
        payload={"vocab_id": str(entry.id), "fields": None},
    )
    await wakeup(job_id)
    return VocabCandidateAcceptResponse(
        vocab_id=str(entry.id), generation_job_id=job_id, already_existed=False
    )


# ============================================================================
# dismiss: dismissed にする(冪等。同語を再提案しない)
# ============================================================================
@router.post(
    "/api/vocab-candidates/{candidate_id}/dismiss",
    status_code=204,
    operation_id="vocab_candidates_dismiss",
)
async def dismiss_candidate(candidate_id: str, user: CurrentUser, db: DbDep) -> Response:
    cand = await _owned_candidate(db, user.id, candidate_id)
    if cand.status == "pending":
        cand.status = "dismissed"
        await db.commit()
    return Response(status_code=204)


__all__ = ["router"]
