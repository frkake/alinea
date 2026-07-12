"""jobs — 非同期ジョブ取得とユーザー単位/ジョブ単位の進捗 SSE(plans/03 §21・plans/01 §5)。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from alinea_core.db.models import DocumentRevision, Job, LibraryItem, Paper, TranslationSet, User
from alinea_core.db.revisions import normalize_uuid
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from alinea_api.deps import CurrentUser, CurrentUserOrExt, DbDep, RedisDep
from alinea_api.errors import ProblemException, build_problem
from alinea_api.schemas.common import sse_json_frame
from alinea_api.schemas.jobs import JobListResponse, JobOut, job_to_out
from alinea_api.services.events import channel_key, read_events_since

router = APIRouter(tags=["jobs"])

PING_INTERVAL_SECONDS = 15.0

_SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}

_SHARED_TRANSLATION_REASONS = frozenset({"literal", "on_demand", "table", "retry_failed"})
_SHARED_JOB_FAILURE_MESSAGE = "共有翻訳ジョブに失敗しました。"
_SHARED_PROGRESS_EVENT_TYPES = frozenset({"job.progress", "translation.progress"})


@dataclass(frozen=True, slots=True)
class _JobAccess:
    event_user_id: str
    shared_observer: bool = False
    poll_db_on_timeout: bool = False


async def _resolve_job_access(db: Any, job: Job, user: User) -> _JobAccess | None:
    """Resolve owner access or narrowly scoped observation of reusable shared work.

    Public literal/on-demand translation work is intentionally reused across users.  A foreign
    user may observe only that reusable work, and only when the set, revision, and job all resolve
    to the same public paper.  Other translation reasons can contain user-specific instructions
    and remain owner-only.
    """
    requester_id = str(user.id)
    owner_id = str(job.user_id) if job.user_id is not None else None
    if owner_id == requester_id:
        assert owner_id is not None
        return _JobAccess(event_user_id=owner_id)

    # Preserve the released behavior for system/shared jobs such as ingest.  Translation jobs are
    # different: ON DELETE SET NULL can orphan producer-owned data, so they still pass the strict
    # public shared-work validation below and always use the redacted projection.
    if job.kind != "translation":
        return _JobAccess(event_user_id=requester_id) if owner_id is None else None

    payload = job.payload if isinstance(job.payload, dict) else {}
    if payload.get("reason") not in _SHARED_TRANSLATION_REASONS:
        return None
    set_id = normalize_uuid(payload.get("set_id"))
    job_paper_id = normalize_uuid(job.paper_id)
    if set_id is None or job_paper_id is None:
        return None
    tset = await db.get(TranslationSet, set_id)
    if tset is None or tset.scope != "shared" or tset.user_id is not None:
        return None
    revision_id = normalize_uuid(tset.revision_id)
    if revision_id is None:
        return None
    revision = await db.get(DocumentRevision, revision_id)
    if revision is None:
        return None
    revision_paper_id = normalize_uuid(revision.paper_id)
    if revision_paper_id is None or revision_paper_id != job_paper_id:
        return None
    paper = await db.get(Paper, revision_paper_id)
    if paper is None or paper.visibility != "public":
        return None
    return _JobAccess(
        event_user_id=owner_id or requester_id,
        shared_observer=True,
        # A producer can disappear after this access check.  Polling every existing SSE ping
        # interval closes that TOCTOU without increasing the normal request rate.
        poll_db_on_timeout=True,
    )


@router.get("/api/jobs/{job_id}", response_model=JobOut, operation_id="jobs_get")
async def get_job(job_id: str, user: CurrentUserOrExt, db: DbDep) -> JobOut:
    job = await db.get(Job, job_id)
    access = await _resolve_job_access(db, job, user) if job is not None else None
    if job is None or access is None:
        raise ProblemException("not_found")
    result = job_to_out(job)
    if access.shared_observer:
        # Producer-owned identifiers, provider details, and future result fields are not shared
        # paper state.  A foreign observer only needs lifecycle state to refresh the Viewer.
        result = result.model_copy(
            update={
                "library_item_id": None,
                "result": None,
                "error": (
                    {"message": _SHARED_JOB_FAILURE_MESSAGE} if result.error is not None else None
                ),
            }
        )
    return result


@router.get(
    "/api/library-items/{item_id}/jobs",
    response_model=JobListResponse,
    operation_id="jobs_list_for_library_item",
)
async def list_library_item_jobs(
    item_id: str,
    user: CurrentUser,
    db: DbDep,
    active: bool = Query(default=False),
) -> JobListResponse:
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user.id):
        raise ProblemException("not_found")
    stmt = select(Job).where(Job.library_item_id == item_id)
    if active:
        stmt = stmt.where(Job.status.in_(("queued", "running", "waiting_quota", "waiting_input")))
    stmt = stmt.order_by(Job.created_at.desc())
    result = await db.execute(stmt)
    jobs = list(result.scalars().all())
    return JobListResponse(items=[job_to_out(job) for job in jobs])


@router.get("/api/events", operation_id="events_stream")
async def user_events(request: Request, user: CurrentUser, r: RedisDep) -> StreamingResponse:
    """ユーザー単位の進捗 SSE。`Last-Event-ID` で Stream から取りこぼしを再送する。"""
    user_id = str(user.id)
    last_event_id = request.headers.get("last-event-id", "")

    async def stream() -> AsyncIterator[str]:
        for event_id, event_type, data in await read_events_since(r, user_id, last_event_id):
            yield sse_json_frame(data, event=event_type, event_id=event_id)
        pubsub = r.pubsub()
        await pubsub.subscribe(channel_key(user_id))
        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=PING_INTERVAL_SECONDS
                )
                if message is None:
                    yield ": ping\n\n"
                    continue
                envelope = _parse_envelope(message.get("data"))
                if envelope is None:
                    continue
                yield sse_json_frame(
                    envelope["data"], event=envelope["event"], event_id=envelope["id"]
                )
        finally:
            await pubsub.unsubscribe(channel_key(user_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py pubsub untyped

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/api/jobs/{job_id}/events", operation_id="jobs_events_stream")
async def job_events(
    job_id: str, request: Request, user: CurrentUser, db: DbDep, r: RedisDep
) -> StreamingResponse:
    """ジョブ単位の進捗 SSE(§21.2)。終了済みなら done/error を 1 回送って閉じる。"""
    job = await db.get(Job, job_id)
    access = await _resolve_job_access(db, job, user) if job is not None else None
    if job is None or access is None:
        raise ProblemException("not_found")
    # Reused shared work is published on its producer's stream.  We subscribe to that stream but
    # emit only the already-authorized job_id, so no other producer events cross the API boundary.
    event_user_id = access.event_user_id
    last_event_id = request.headers.get("last-event-id", "")

    async def stream() -> AsyncIterator[str]:
        # 接続直後に現在状態を 1 回送る。
        frame = _job_state_frame(job, shared_observer=access.shared_observer)
        if frame is not None:
            yield frame
        if job.status in ("succeeded", "failed"):
            return
        # 取りこぼし再送(この job のイベントのみ)。
        for event_id, event_type, data in await read_events_since(r, event_user_id, last_event_id):
            if data.get("job_id") == job_id:
                frame, terminal = await _job_event_frame(
                    db,
                    job_id,
                    event_type,
                    data,
                    event_id,
                    shared_observer=access.shared_observer,
                )
                if frame is not None:
                    yield frame
                if terminal:
                    return
        pubsub = r.pubsub()
        await pubsub.subscribe(channel_key(event_user_id))
        try:
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=PING_INTERVAL_SECONDS
                )
                if message is None:
                    if access.poll_db_on_timeout:
                        # A producer deletion changes jobs.user_id to NULL, so Worker has no user
                        # stream on which to publish job.updated.  Foreign shared-work observers
                        # poll at the existing ping cadence so this mid-connection race terminates.
                        frame, terminal = await _job_event_frame(
                            db,
                            job_id,
                            "job.updated",
                            {"job_id": job_id},
                            None,
                            shared_observer=True,
                        )
                        if terminal:
                            if frame is not None:
                                yield frame
                            break
                    yield ": ping\n\n"
                    continue
                envelope = _parse_envelope(message.get("data"))
                if envelope is None or envelope["data"].get("job_id") != job_id:
                    continue
                frame, terminal = await _job_event_frame(
                    db,
                    job_id,
                    envelope["event"],
                    envelope["data"],
                    envelope["id"],
                    shared_observer=access.shared_observer,
                )
                if frame is not None:
                    yield frame
                if terminal:
                    break
        finally:
            await pubsub.unsubscribe(channel_key(event_user_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py pubsub untyped

    return StreamingResponse(stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _parse_envelope(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        envelope = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, dict) or "data" not in envelope:
        return None
    return envelope


def _translate_event(user_event: str) -> str:
    """ユーザー単位イベント名(§5)をジョブ SSE のイベント名(§21.2)へ写像する。"""
    if user_event == "job.failed":
        return "error"
    return "progress"


async def _job_event_frame(
    db: Any,
    job_id: str,
    event_type: str,
    data: dict[str, Any],
    event_id: str | None,
    *,
    shared_observer: bool = False,
) -> tuple[str | None, bool]:
    """user event を job SSE フレームへ変換する。

    ``job.updated`` は軽量な起床通知なので、DB の最新状態を読み直して progress/done/error に
    展開する。translation の詳細 progress など payload 完結イベントはそのまま流す。
    """
    if event_type != "job.updated":
        if shared_observer:
            projected = _shared_progress_event(job_id, event_type, data)
            if projected is None:
                return None, False
            return sse_json_frame(
                projected,
                event=_translate_event(event_type),
                event_id=event_id,
            ), False
        return sse_json_frame(data, event=_translate_event(event_type), event_id=event_id), False

    fresh = await db.get(Job, job_id)
    if fresh is None:
        return None, True
    await db.refresh(fresh)
    return _job_state_frame(
        fresh,
        event_id=event_id,
        shared_observer=shared_observer,
    ), fresh.status in ("succeeded", "failed")


def _shared_progress_event(
    job_id: str, event_type: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    """Project a foreign producer event to primitive lifecycle fields only."""
    if event_type not in _SHARED_PROGRESS_EVENT_TYPES:
        return None
    projected: dict[str, Any] = {"job_id": job_id}
    for field in ("status", "stage"):
        value = data.get(field)
        if isinstance(value, str):
            projected[field] = value
    for field in ("progress", "progress_pct", "progress_percent", "total_progress"):
        value = data.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            projected[field] = value
    return projected


def _job_state_frame(
    job: Job,
    *,
    event_id: str | None = None,
    shared_observer: bool = False,
) -> str | None:
    if job.status == "succeeded":
        return sse_json_frame(
            {
                "job_id": str(job.id),
                "status": "succeeded",
                "result": {} if shared_observer else job.result or {},
            },
            event="done",
            event_id=event_id,
        )
    if job.status == "failed":
        problem = build_problem(
            "provider_error",
            status=502,
            title="ジョブに失敗しました",
            detail=_SHARED_JOB_FAILURE_MESSAGE if shared_observer else job.error,
        )
        return sse_json_frame(problem.model_dump(mode="json"), event="error", event_id=event_id)
    return sse_json_frame(
        {
            "job_id": str(job.id),
            "status": job.status,
            "stage": job.stage,
            "progress_pct": job.progress,
        },
        event="progress",
        event_id=event_id,
    )
