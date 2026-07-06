"""jobs — 非同期ジョブ取得とユーザー単位/ジョブ単位の進捗 SSE(plans/03 §21・plans/01 §5)。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from yakudoku_core.db.models import Job, LibraryItem

from yakudoku_api.deps import CurrentUser, CurrentUserOrExt, DbDep, RedisDep
from yakudoku_api.errors import ProblemException, build_problem
from yakudoku_api.schemas.common import sse_json_frame
from yakudoku_api.schemas.jobs import JobListResponse, JobOut, job_to_out
from yakudoku_api.services.events import channel_key, read_events_since

router = APIRouter(tags=["jobs"])

PING_INTERVAL_SECONDS = 15.0

_SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


@router.get("/api/jobs/{job_id}", response_model=JobOut, operation_id="jobs_get")
async def get_job(job_id: str, user: CurrentUserOrExt, db: DbDep) -> JobOut:
    job = await db.get(Job, job_id)
    if job is None or (job.user_id is not None and str(job.user_id) != str(user.id)):
        raise ProblemException("not_found")
    return job_to_out(job)


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
        stmt = stmt.where(Job.status.in_(("queued", "running", "waiting_quota")))
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
    if job is None or (job.user_id is not None and str(job.user_id) != str(user.id)):
        raise ProblemException("not_found")
    user_id = str(user.id)
    last_event_id = request.headers.get("last-event-id", "")

    async def stream() -> AsyncIterator[str]:
        # 接続直後に現在状態を 1 回送る。
        frame = _job_state_frame(job)
        if frame is not None:
            yield frame
        if job.status in ("succeeded", "failed"):
            return
        # 取りこぼし再送(この job のイベントのみ)。
        for event_id, event_type, data in await read_events_since(r, user_id, last_event_id):
            if data.get("job_id") == job_id:
                yield sse_json_frame(data, event=_translate_event(event_type), event_id=event_id)
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
                if envelope is None or envelope["data"].get("job_id") != job_id:
                    continue
                yield sse_json_frame(
                    envelope["data"],
                    event=_translate_event(envelope["event"]),
                    event_id=envelope["id"],
                )
                # 終了イベントなら DB を確認して done/error を送り閉じる。
                fresh = await db.get(Job, job_id)
                if fresh is not None:
                    await db.refresh(fresh)
                    if fresh.status in ("succeeded", "failed"):
                        done = _job_state_frame(fresh)
                        if done is not None:
                            yield done
                        break
        finally:
            await pubsub.unsubscribe(channel_key(user_id))
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


def _job_state_frame(job: Job) -> str | None:
    if job.status == "succeeded":
        return sse_json_frame(
            {"job_id": str(job.id), "status": "succeeded", "result": job.result or {}},
            event="done",
        )
    if job.status == "failed":
        problem = build_problem(
            "provider_error",
            status=502,
            title="ジョブに失敗しました",
            detail=job.error,
        )
        return sse_json_frame(problem.model_dump(mode="json"), event="error")
    return sse_json_frame(
        {
            "job_id": str(job.id),
            "status": job.status,
            "stage": job.stage,
            "progress_pct": job.progress,
        },
        event="progress",
    )
