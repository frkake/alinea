"""ジョブ基盤 + SSE テスト。

- PY-JOB-01: claim 排他・冪等性キー再投入・checkpoint 段階再開。
- PY-JOB-03: 指数バックオフ 30s→2min→8min の 3 回・以後 failed。部分成功はジョブを fail させない。
- SSE 再送: `Last-Event-ID` で Stream から取りこぼしを再送する(plans/03 §21・plans/01 §5)。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.schemas.common import sse_json_frame
from yakudoku_api.services.events import publish_event, read_events_since, stream_key
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


def _k() -> str:
    import uuid

    return uuid.uuid4().hex
