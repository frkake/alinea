"""ジョブ基盤 + SSE テスト。

- PY-JOB-01: claim 排他・冪等性キー再投入・checkpoint 段階再開。
- PY-JOB-03: 指数バックオフ 30s→2min→8min の 3 回・以後 failed。部分成功はジョブを fail させない。
- SSE 再送: `Last-Event-ID` で Stream から取りこぼしを再送する(plans/03 §21・plans/01 §5)。
- jobs ルータ(GET /api/jobs/{id}・GET /api/library-items/{id}/jobs)の所有権/フィルタ・
  SSE ヘルパー(_parse_envelope/_translate_event/_job_state_frame)の単体テスト。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from types import SimpleNamespace
from typing import Any

import factories
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.routers.jobs import _job_state_frame, _parse_envelope, _translate_event
from yakudoku_api.schemas.common import sse_json_frame
from yakudoku_api.services.events import publish_event, read_events_since, stream_key
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_core.jobs.store import JobStore


# ---------------------------------------------------------------------------
# PY-JOB-01
# ---------------------------------------------------------------------------
async def test_claim_is_exclusive(db_session: AsyncSession) -> None:
    store = JobStore(db_session)
    jid = await store.enqueue(kind="translation", payload={}, idempotency_key="excl-" + _k())
    first = await store.claim(jid)
    second = await store.claim(jid)
    assert first is not None
    assert first.status == "running"
    assert first.attempt == 1
    assert second is None  # 二重 enqueue・二重起床でも 1 回だけ実行


async def test_idempotency_key_returns_existing_job(db_session: AsyncSession) -> None:
    store = JobStore(db_session)
    key = "idem-" + _k()
    first = await store.enqueue(kind="translation", payload={"a": 1}, idempotency_key=key)
    second = await store.enqueue(kind="translation", payload={"a": 2}, idempotency_key=key)
    assert first == second


async def test_checkpoint_enables_stage_resume(db_session: AsyncSession) -> None:
    store = JobStore(db_session)
    jid = await store.enqueue(kind="ingest", payload={}, idempotency_key="ckpt-" + _k())
    await store.claim(jid)
    await store.checkpoint(jid, "fetching", {"source_asset_id": "ast_1"}, progress=20)
    await store.checkpoint(jid, "parsing", {"blocks": 42}, progress=40)

    job = await store.get(jid)
    assert job is not None
    assert JobStore.has_stage_completed(job, "fetching")
    assert JobStore.has_stage_completed(job, "parsing")
    assert JobStore.get_checkpoint(job)["fetching"] == {"source_asset_id": "ast_1"}
    assert job.stage == "parsing"
    assert job.progress == 40

    # クラッシュ→再試行: checkpoint は残り、完了済み stage をスキップして再開できる。
    await store.fail_with_retry(jid, {"stage": "structuring", "message": "boom"})
    resumed = await store.claim(jid)
    assert resumed is not None
    assert JobStore.has_stage_completed(resumed, "fetching")
    assert JobStore.has_stage_completed(resumed, "parsing")
    assert resumed.attempt == 2  # 二重処理ではなく再開


# ---------------------------------------------------------------------------
# PY-JOB-03
# ---------------------------------------------------------------------------
async def test_retry_backoff_then_failed(db_session: AsyncSession) -> None:
    store = JobStore(db_session)
    jid = await store.enqueue(kind="translation", payload={}, idempotency_key="retry-" + _k())

    expected_backoffs = [30, 120, 480]
    for expected in expected_backoffs:
        claimed = await store.claim(jid)
        assert claimed is not None
        retrying = await store.fail_with_retry(jid, {"message": "provider error"})
        assert retrying is True
        job = await store.get(jid)
        assert job is not None
        assert job.status == "queued"
        assert job.next_retry_at is not None
        delta = (job.next_retry_at - dt.datetime.now(dt.UTC)).total_seconds()
        assert abs(delta - expected) <= 5, f"backoff {delta} != {expected}"

    # 4 回目の失敗で自動リトライ尽き → failed。
    claimed = await store.claim(jid)
    assert claimed is not None
    retrying = await store.fail_with_retry(jid, {"message": "final"})
    assert retrying is False
    final = await store.get(jid)
    assert final is not None
    assert final.status == "failed"
    assert final.finished_at is not None


async def test_partial_failure_does_not_fail_job(db_session: AsyncSession) -> None:
    store = JobStore(db_session)
    jid = await store.enqueue(kind="ingest", payload={}, idempotency_key="partial-" + _k())
    await store.claim(jid)
    # 図抽出失敗などの部分失敗は記録するが status は変えない(部分成功は正の状態)。
    await store.record_partial_failure(jid, "structuring", {"figure": "blk-7", "reason": "ocr"})
    await store.succeed(jid, {"library_item_id": "li_1"})

    job = await store.get(jid)
    assert job is not None
    assert job.status == "succeeded"
    assert job.progress == 100
    assert any(entry.get("level") == "partial_failure" for entry in job.log)
    assert job.result == {"library_item_id": "li_1"}


# ---------------------------------------------------------------------------
# SSE 再送(Last-Event-ID による Stream からの取りこぼし再送。§1.9・§5)
#
# 注: 開いたままの SSE ストリームは httpx ASGITransport が完了待ちでバッファするため
# in-process では検証できない(E2E は実サーバで検証)。ここでは再送の実体
# (read_events_since + フレーム整形)を検証する。
# ---------------------------------------------------------------------------
async def test_events_resend_by_last_event_id(redis_client: Any, unique_email: str) -> None:
    uid = "sse-" + _k()
    id1 = await publish_event(
        redis_client, uid, "job.progress", {"job_id": "j1", "progress_percent": 10}
    )
    id2 = await publish_event(
        redis_client, uid, "job.progress", {"job_id": "j1", "progress_percent": 20}
    )
    id3 = await publish_event(redis_client, uid, "notification.created", {"notification_id": "n1"})
    try:
        # Last-Event-ID = id1 → id2, id3 のみ(排他)を再送する。
        resent = await read_events_since(redis_client, uid, id1)
        assert [event_id for event_id, _, _ in resent] == [id2, id3]

        # Last-Event-ID = "0-0" → 全件。
        everything = await read_events_since(redis_client, uid, "0-0")
        assert len(everything) == 3

        # 再送フレームは id:/event:/data: を持つ。
        first_id, first_event, first_data = resent[0]
        frame = sse_json_frame(first_data, event=first_event, event_id=first_id)
        assert frame.startswith(f"id: {id2}")
        assert "event: job.progress" in frame
        assert '"progress_percent":20' in frame
    finally:
        await redis_client.delete(stream_key(uid))


# ---------------------------------------------------------------------------
# jobs ルータ: GET /api/jobs/{id}・GET /api/library-items/{id}/jobs
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def job_http_ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, unique_email: str
) -> Any:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    item = await factories.make_library_item(db_session, user=user, status="reading")
    await db_session.commit()
    token = await create_session(redis_client, user.id)
    client.cookies.set("yk_session", token)
    try:
        yield SimpleNamespace(db=db_session, user=user, item_id=str(item.id))
    finally:
        await db_session.rollback()
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_get_job_returns_detail_for_owner(client: AsyncClient, job_http_ctx: Any) -> None:
    job = await factories.make_job(
        job_http_ctx.db,
        kind="translation",
        status="queued",
        user=job_http_ctx.user,
    )
    job.library_item_id = job_http_ctx.item_id
    await job_http_ctx.db.commit()
    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(job.id)
    assert body["kind"] == "translation"
    assert body["status"] == "queued"
    assert body["library_item_id"] == job_http_ctx.item_id


async def test_get_job_not_found_for_unknown_id(client: AsyncClient, job_http_ctx: Any) -> None:
    resp = await client.get(f"/api/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


async def test_get_job_not_found_for_other_users_job(
    client: AsyncClient, job_http_ctx: Any, db_session: AsyncSession
) -> None:
    other = await factories.make_user(db_session)
    job = await factories.make_job(db_session, kind="translation", user=other)
    await db_session.commit()
    try:
        resp = await client.get(f"/api/jobs/{job.id}")
        assert resp.status_code == 404
    finally:
        await purge_user(db_session, str(other.id))
        await db_session.commit()


async def test_get_job_reports_parsed_json_error_payload(
    client: AsyncClient, job_http_ctx: Any
) -> None:
    job = await factories.make_job(
        job_http_ctx.db, kind="translation", status="failed", user=job_http_ctx.user
    )
    job.error = json.dumps({"message": "provider down", "code": "provider_error"})
    await job_http_ctx.db.commit()
    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["error"] == {"message": "provider down", "code": "provider_error"}


async def test_get_job_wraps_non_json_error_as_message(
    client: AsyncClient, job_http_ctx: Any
) -> None:
    job = await factories.make_job(
        job_http_ctx.db, kind="translation", status="failed", user=job_http_ctx.user
    )
    job.error = "boom (not valid json)"
    await job_http_ctx.db.commit()
    resp = await client.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["error"] == {"message": "boom (not valid json)"}


async def test_list_library_item_jobs_active_filter_and_ordering(
    client: AsyncClient, job_http_ctx: Any
) -> None:
    item = job_http_ctx.item_id
    old = await factories.make_job(
        job_http_ctx.db,
        kind="translation",
        status="succeeded",
        user=job_http_ctx.user,
    )
    old.library_item_id = item
    new = await factories.make_job(
        job_http_ctx.db,
        kind="translation",
        status="queued",
        user=job_http_ctx.user,
    )
    new.library_item_id = item
    await job_http_ctx.db.commit()

    everything = await client.get(f"/api/library-items/{item}/jobs")
    assert everything.status_code == 200
    ids = {j["id"] for j in everything.json()["items"]}
    assert ids == {str(old.id), str(new.id)}

    active_only = await client.get(f"/api/library-items/{item}/jobs", params={"active": "true"})
    assert active_only.status_code == 200
    active_ids = [j["id"] for j in active_only.json()["items"]]
    assert active_ids == [str(new.id)]


async def test_list_library_item_jobs_not_found_for_missing_or_foreign_item(
    client: AsyncClient, job_http_ctx: Any, db_session: AsyncSession
) -> None:
    missing = await client.get(f"/api/library-items/{uuid.uuid4()}/jobs")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    other = await factories.make_user(db_session)
    other_item = await factories.make_library_item(db_session, user=other)
    await db_session.commit()
    try:
        foreign = await client.get(f"/api/library-items/{other_item.id}/jobs")
        assert foreign.status_code == 404
    finally:
        await purge_user(db_session, str(other.id))
        await db_session.commit()


# ---------------------------------------------------------------------------
# jobs ルータの SSE ヘルパー(純粋関数)の単体テスト
# ---------------------------------------------------------------------------
def test_parse_envelope_rejects_malformed_input() -> None:
    assert _parse_envelope(123) is None  # 文字列以外
    assert _parse_envelope("not json") is None  # JSON でない
    assert _parse_envelope(json.dumps(["a", "b"])) is None  # dict でない
    assert _parse_envelope(json.dumps({"event": "job.progress"})) is None  # data キーが無い


def test_parse_envelope_accepts_valid_envelope() -> None:
    raw = json.dumps({"id": "1-0", "event": "job.progress", "data": {"job_id": "j1"}})
    assert _parse_envelope(raw) == {"id": "1-0", "event": "job.progress", "data": {"job_id": "j1"}}


def test_translate_event_maps_failed_to_error_others_to_progress() -> None:
    assert _translate_event("job.failed") == "error"
    assert _translate_event("job.progress") == "progress"
    assert _translate_event("notification.created") == "progress"


def test_job_state_frame_succeeded_failed_and_in_progress() -> None:
    succeeded = SimpleNamespace(id="job_1", status="succeeded", result={"library_item_id": "li_1"})
    frame = _job_state_frame(succeeded)  # type: ignore[arg-type]
    assert frame is not None
    assert "event: done" in frame
    assert '"library_item_id":"li_1"' in frame

    failed = SimpleNamespace(id="job_2", status="failed", error="boom")
    frame = _job_state_frame(failed)  # type: ignore[arg-type]
    assert frame is not None
    assert "event: error" in frame
    assert '"detail":"boom"' in frame

    running = SimpleNamespace(id="job_3", status="running", stage="parsing", progress=40)
    frame = _job_state_frame(running)  # type: ignore[arg-type]
    assert frame is not None
    assert "event: progress" in frame
    assert '"stage":"parsing"' in frame
    assert '"progress_pct":40' in frame


def _k() -> str:
    import uuid

    return uuid.uuid4().hex
