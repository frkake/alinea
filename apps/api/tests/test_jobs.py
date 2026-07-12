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
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast

import factories
import pytest_asyncio
from alinea_api.routers.jobs import (
    _job_event_frame,
    _job_state_frame,
    _parse_envelope,
    _resolve_job_access,
    _translate_event,
    job_events,
)
from alinea_api.schemas.common import sse_json_frame
from alinea_api.services.events import channel_key, publish_event, read_events_since, stream_key
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import Job
from alinea_core.jobs.store import JobStore
from fastapi import Request
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession


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


async def test_enqueue_uncommitted_flushes_without_committing_and_can_rollback(
    db_session: AsyncSession,
) -> None:
    store = JobStore(db_session)
    job_id = await store.enqueue_uncommitted(
        kind="translation",
        payload={"request_key": "atomic-work"},
        idempotency_key="atomic-" + _k(),
    )

    assert db_session.in_transaction()
    assert await db_session.get(Job, job_id) is not None

    await db_session.rollback()
    assert await db_session.get(Job, job_id) is None


async def test_enqueue_keeps_commit_contract(db_session: AsyncSession) -> None:
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="translation", payload={}, idempotency_key="commit-contract-" + _k()
    )

    assert not db_session.in_transaction()
    assert await db_session.get(Job, job_id) is not None


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


async def test_mark_waiting_input_pauses_job_without_losing_checkpoint(
    db_session: AsyncSession,
) -> None:
    store = JobStore(db_session)
    jid = await store.enqueue(kind="ingest", payload={}, idempotency_key="input-" + _k())
    await store.claim(jid)
    await store.checkpoint(jid, "readable", {"revision_id": "rev-1"}, progress=45)

    await store.mark_waiting_input(jid, stage="selecting_sections")

    paused = await store.get(jid)
    assert paused is not None
    assert paused.status == "waiting_input"
    assert paused.stage == "selecting_sections"
    assert paused.progress == 45
    assert JobStore.get_checkpoint(paused)["readable"] == {"revision_id": "rev-1"}
    assert await store.claim(jid) is None


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


async def test_success_after_retry_clears_stale_error_and_schedule(
    db_session: AsyncSession,
) -> None:
    store = JobStore(db_session)
    jid = await store.enqueue(kind="translation", payload={}, idempotency_key="recover-" + _k())
    assert await store.claim(jid) is not None
    assert await store.fail_with_retry(jid, {"message": "temporary provider error"})
    assert await store.claim(jid) is not None

    await store.succeed(jid, {"translated": 1})

    recovered = await store.get(jid)
    assert recovered is not None
    assert recovered.status == "succeeded"
    assert recovered.error is None
    assert recovered.next_retry_at is None


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
    user_id = str(user.id)
    token = await create_session(redis_client, user_id)
    client.cookies.set("yk_session", token)
    try:
        yield SimpleNamespace(db=db_session, user=user, item_id=str(item.id))
    finally:
        await db_session.rollback()
        await purge_user(db_session, user_id)
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


async def test_shared_translation_job_can_be_observed_without_leaking_owner_item(
    client: AsyncClient, job_http_ctx: Any, db_session: AsyncSession
) -> None:
    producer = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=producer, visibility="public")
    revision = await factories.make_revision(db_session, paper=paper)
    tset = await factories.make_translation_set(
        db_session,
        revision=revision,
        style="literal",
        scope="shared",
        status="complete",
    )
    producer_item = await factories.make_library_item(
        db_session,
        user=producer,
        paper=paper,
    )
    job = await factories.make_job(
        db_session,
        kind="translation",
        status="succeeded",
        progress=100,
        user=producer,
        paper=paper,
        library_item=producer_item,
        payload={
            "set_id": str(tset.id),
            "section_id": "sec-1",
            "block_ids": ["blk-p1", "blk-p2"],
            "reason": "literal",
        },
    )
    job.result = {
        "section_id": "sec-1",
        "translated": 2,
        "library_item_id": producer_item.id,
        "secret": "producer-result-sentinel",
    }
    failed = await factories.make_job(
        db_session,
        kind="translation",
        status="failed",
        progress=10,
        user=producer,
        paper=paper,
        library_item=producer_item,
        payload={
            "set_id": str(tset.id),
            "section_id": "sec-2",
            "block_ids": ["blk-p3"],
            "reason": "literal",
        },
    )
    failed.error = json.dumps(
        {"message": "producer-error-sentinel", "library_item_id": str(producer_item.id)}
    )
    await db_session.commit()
    try:
        access = await _resolve_job_access(db_session, job, job_http_ctx.user)
        assert access is not None
        assert access.shared_observer is True
        assert access.event_user_id == str(producer.id)

        detail = await client.get(f"/api/jobs/{job.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == str(job.id)
        # The shared work is observable, but the producer's private library row is not.
        assert detail.json()["library_item_id"] is None
        assert detail.json()["result"] is None
        assert "producer-result-sentinel" not in detail.text

        events = await client.get(f"/api/jobs/{job.id}/events")
        assert events.status_code == 200, events.text
        assert "event: done" in events.text
        assert f'"job_id":"{job.id}"' in events.text
        assert "producer-result-sentinel" not in events.text
        assert str(producer_item.id) not in events.text

        failed_detail = await client.get(f"/api/jobs/{failed.id}")
        assert failed_detail.status_code == 200, failed_detail.text
        assert failed_detail.json()["library_item_id"] is None
        assert failed_detail.json()["error"] == {"message": "共有翻訳ジョブに失敗しました。"}
        assert "producer-error-sentinel" not in failed_detail.text

        failed_events = await client.get(f"/api/jobs/{failed.id}/events")
        assert failed_events.status_code == 200, failed_events.text
        assert "event: error" in failed_events.text
        assert "producer-error-sentinel" not in failed_events.text
        assert str(producer_item.id) not in failed_events.text
    finally:
        await purge_user(db_session, str(producer.id))
        await db_session.commit()


class _ConnectedRequest:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


class _SequencePubSub:
    def __init__(self, messages: list[dict[str, str]]) -> None:
        self.messages = iter(messages)
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def subscribe(self, key: str) -> None:
        self.subscribed.append(key)

    async def unsubscribe(self, key: str) -> None:
        self.unsubscribed.append(key)

    async def get_message(self, **_kwargs: Any) -> dict[str, str] | None:
        return next(self.messages, None)

    async def aclose(self) -> None:
        return None


class _SequenceRedis:
    def __init__(self, messages: list[dict[str, str]]) -> None:
        self.subscription = _SequencePubSub(messages)

    def pubsub(self) -> _SequencePubSub:
        return self.subscription


async def test_shared_translation_sse_uses_owner_stream_and_projects_live_events(
    job_http_ctx: Any, db_session: AsyncSession
) -> None:
    producer = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=producer, visibility="public")
    revision = await factories.make_revision(db_session, paper=paper)
    tset = await factories.make_translation_set(
        db_session,
        revision=revision,
        style="literal",
        scope="shared",
    )
    producer_item = await factories.make_library_item(
        db_session,
        user=producer,
        paper=paper,
    )
    job = await factories.make_job(
        db_session,
        kind="translation",
        status="running",
        progress=10,
        user=producer,
        paper=paper,
        library_item=producer_item,
        payload={
            "set_id": str(tset.id),
            "section_id": "sec-1",
            "block_ids": ["blk-p1"],
            "reason": "literal",
        },
    )
    other_job_id = str(uuid.uuid4())
    private_fields = {
        "library_item_id": str(producer_item.id),
        "secret": "producer-event-sentinel",
    }
    messages = [
        {
            "data": json.dumps(
                {
                    "id": "1-0",
                    "event": "job.progress",
                    "data": {"job_id": other_job_id, "progress_pct": 99, **private_fields},
                }
            )
        },
        {
            "data": json.dumps(
                {
                    "id": "2-0",
                    "event": "job.progress",
                    "data": {"job_id": str(job.id), "progress_pct": 42, **private_fields},
                }
            )
        },
    ]
    fake_redis = _SequenceRedis(messages)
    await db_session.commit()
    try:
        response = await job_events(
            str(job.id),
            cast(Request, _ConnectedRequest()),
            job_http_ctx.user,
            db_session,
            cast(Redis, fake_redis),
        )
        stream = cast(AsyncGenerator[str, None], response.body_iterator)
        initial = await anext(stream)
        projected = await anext(stream)
        await stream.aclose()

        assert '"progress_pct":10' in initial
        assert f'"job_id":"{job.id}"' in projected
        assert '"progress_pct":42' in projected
        assert other_job_id not in projected
        assert "producer-event-sentinel" not in projected
        assert str(producer_item.id) not in projected
        assert fake_redis.subscription.subscribed == [channel_key(str(producer.id))]
        assert fake_redis.subscription.unsubscribed == [channel_key(str(producer.id))]
    finally:
        await purge_user(db_session, str(producer.id))
        await db_session.commit()


async def test_orphaned_shared_translation_job_keeps_shared_projection(
    client: AsyncClient, job_http_ctx: Any, db_session: AsyncSession
) -> None:
    producer = await factories.make_user(db_session)
    # Public arXiv papers need no owner, so deleting the producer leaves the shared work intact
    # while jobs.user_id is changed to NULL by its FK.
    paper = await factories.make_paper(db_session, visibility="public")
    revision = await factories.make_revision(db_session, paper=paper)
    tset = await factories.make_translation_set(
        db_session,
        revision=revision,
        style="literal",
        scope="shared",
    )
    succeeded = await factories.make_job(
        db_session,
        kind="translation",
        status="succeeded",
        progress=100,
        user=producer,
        paper=paper,
        payload={
            "set_id": str(tset.id),
            "section_id": "sec-1",
            "block_ids": ["blk-p1"],
            "reason": "literal",
        },
    )
    succeeded.result = {"secret": "deleted-producer-result-sentinel"}
    failed = await factories.make_job(
        db_session,
        kind="translation",
        status="failed",
        user=producer,
        paper=paper,
        payload={
            "set_id": str(tset.id),
            "section_id": "sec-2",
            "block_ids": ["blk-p3"],
            "reason": "literal",
        },
    )
    failed.error = "deleted-producer-error-sentinel"
    await db_session.commit()
    succeeded_id = str(succeeded.id)
    failed_id = str(failed.id)
    await purge_user(db_session, str(producer.id))
    await db_session.refresh(succeeded)

    orphaned = await db_session.get(Job, succeeded_id)
    assert orphaned is not None
    assert orphaned.user_id is None
    access = await _resolve_job_access(db_session, orphaned, job_http_ctx.user)
    assert access is not None
    assert access.shared_observer is True
    assert access.event_user_id == str(job_http_ctx.user.id)

    succeeded_detail = await client.get(f"/api/jobs/{succeeded_id}")
    assert succeeded_detail.status_code == 200, succeeded_detail.text
    assert succeeded_detail.json()["result"] is None
    assert "deleted-producer-result-sentinel" not in succeeded_detail.text
    succeeded_events = await client.get(f"/api/jobs/{succeeded_id}/events")
    assert succeeded_events.status_code == 200, succeeded_events.text
    assert "deleted-producer-result-sentinel" not in succeeded_events.text

    failed_detail = await client.get(f"/api/jobs/{failed_id}")
    assert failed_detail.status_code == 200, failed_detail.text
    assert failed_detail.json()["error"] == {"message": "共有翻訳ジョブに失敗しました。"}
    assert "deleted-producer-error-sentinel" not in failed_detail.text
    failed_events = await client.get(f"/api/jobs/{failed_id}/events")
    assert failed_events.status_code == 200, failed_events.text
    assert "deleted-producer-error-sentinel" not in failed_events.text


async def test_active_orphaned_shared_translation_sse_polls_terminal_state(
    job_http_ctx: Any, db_session: AsyncSession
) -> None:
    producer = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, visibility="public")
    revision = await factories.make_revision(db_session, paper=paper)
    tset = await factories.make_translation_set(
        db_session,
        revision=revision,
        style="literal",
        scope="shared",
    )
    job = await factories.make_job(
        db_session,
        kind="translation",
        status="running",
        progress=35,
        user=producer,
        paper=paper,
        payload={
            "set_id": str(tset.id),
            "section_id": "sec-1",
            "block_ids": ["blk-p1"],
            "reason": "literal",
        },
    )
    await db_session.commit()
    job_id = str(job.id)
    producer_id = str(producer.id)

    fake_redis = _SequenceRedis([])
    response = await job_events(
        job_id,
        cast(Request, _ConnectedRequest()),
        job_http_ctx.user,
        db_session,
        cast(Redis, fake_redis),
    )
    stream = cast(AsyncGenerator[str, None], response.body_iterator)
    initial = await anext(stream)
    assert '"status":"running"' in initial

    # The producer disappears after the foreign observer connected.  jobs.user_id becomes NULL and
    # no Worker event can reach the already-subscribed owner stream.
    await purge_user(db_session, producer_id)
    # Simulate completion in another process. synchronize_session=False leaves the request's
    # initially loaded object stale, matching a separate API/Worker process.
    await db_session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status="succeeded",
            progress=100,
            result={"secret": "orphaned-terminal-result-sentinel"},
        ),
        execution_options={"synchronize_session": False},
    )
    await db_session.commit()

    terminal = await anext(stream)
    await stream.aclose()
    assert "event: done" in terminal
    assert '"status":"succeeded"' in terminal
    assert "orphaned-terminal-result-sentinel" not in terminal
    assert fake_redis.subscription.subscribed == [channel_key(producer_id)]


async def test_foreign_translation_job_observation_fails_closed(
    client: AsyncClient, job_http_ctx: Any, db_session: AsyncSession
) -> None:
    producer = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=producer, visibility="public")
    revision = await factories.make_revision(db_session, paper=paper)
    shared = await factories.make_translation_set(
        db_session,
        revision=revision,
        style="literal",
        scope="shared",
    )
    other_paper = await factories.make_paper(db_session, owner=producer, visibility="public")
    personal = await factories.make_translation_set(
        db_session,
        revision=revision,
        style="natural",
        scope="personal",
        user=producer,
    )
    unsafe = await factories.make_job(
        db_session,
        kind="translation",
        status="succeeded",
        user=producer,
        paper=paper,
        payload={"set_id": str(shared.id), "reason": "retranslate"},
    )
    wrong_paper = await factories.make_job(
        db_session,
        kind="translation",
        status="succeeded",
        user=producer,
        paper=other_paper,
        payload={"set_id": str(shared.id), "reason": "literal"},
    )
    private_set = await factories.make_job(
        db_session,
        kind="translation",
        status="succeeded",
        user=producer,
        paper=paper,
        payload={"set_id": str(personal.id), "reason": "literal"},
    )
    malformed = await factories.make_job(
        db_session,
        kind="translation",
        status="succeeded",
        user=producer,
        paper=paper,
        payload={"set_id": "not-a-uuid", "reason": "literal"},
    )
    await db_session.commit()
    try:
        for job in (unsafe, wrong_paper, private_set, malformed):
            detail = await client.get(f"/api/jobs/{job.id}")
            assert detail.status_code == 404, (str(job.id), detail.text)
            events = await client.get(f"/api/jobs/{job.id}/events")
            assert events.status_code == 404, (str(job.id), events.text)
    finally:
        await purge_user(db_session, str(producer.id))
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
    waiting = await factories.make_job(
        job_http_ctx.db,
        kind="ingest",
        status="waiting_input",
        user=job_http_ctx.user,
    )
    waiting.library_item_id = item
    new.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    waiting.created_at = dt.datetime.now(dt.UTC)
    await job_http_ctx.db.commit()

    everything = await client.get(f"/api/library-items/{item}/jobs")
    assert everything.status_code == 200
    ids = {j["id"] for j in everything.json()["items"]}
    assert ids == {str(old.id), str(new.id), str(waiting.id)}

    active_only = await client.get(f"/api/library-items/{item}/jobs", params={"active": "true"})
    assert active_only.status_code == 200
    active_ids = [j["id"] for j in active_only.json()["items"]]
    assert active_ids == [str(waiting.id), str(new.id)]


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


async def test_job_updated_event_frame_reads_latest_db_state(db_session: AsyncSession) -> None:
    user = await factories.make_user(db_session)
    job = await factories.make_job(
        db_session,
        kind="article",
        stage="generating",
        status="queued",
        progress=40,
        user=user,
    )
    await db_session.commit()
    try:
        frame, terminal = await _job_event_frame(
            db_session,
            str(job.id),
            "job.updated",
            {"job_id": str(job.id)},
            "1-0",
        )
        assert terminal is False
        assert frame is not None
        assert "id: 1-0" in frame
        assert "event: progress" in frame
        assert '"stage":"generating"' in frame
        assert '"progress_pct":40' in frame

        job.status = "failed"
        job.error = "provider failed"
        await db_session.commit()
        frame, terminal = await _job_event_frame(
            db_session,
            str(job.id),
            "job.updated",
            {"job_id": str(job.id)},
            "2-0",
        )
        assert terminal is True
        assert frame is not None
        assert "id: 2-0" in frame
        assert "event: error" in frame
        assert '"detail":"provider failed"' in frame
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


def _k() -> str:
    import uuid

    return uuid.uuid4().hex
