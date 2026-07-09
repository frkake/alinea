"""reading_sessions サービス層の直接呼び出しテスト(M1-05 / plans/03 §5.9・plans/07 §8)。

``test_reading_sessions.py`` は HTTP 経由(PY-LIB-05)で同じ振る舞いを検証済みだが、本ファイルは
``record_heartbeat`` をルータ(ASGI)を経由せず直接呼ぶ。挙動は同じでも、DB 実書き込み・
`_reached_end` の本文内位置判定・3 分ルール/読了間近の状態遷移まで、実処理を直接アサートする。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from alinea_api.services.reading_sessions import ReadingHeartbeatBody, record_heartbeat
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import Notification, ReadingSession, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _iso(t: dt.datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def _body(*, started_at: dt.datetime, active_seconds: int) -> ReadingHeartbeatBody:
    return ReadingHeartbeatBody(
        client_session_id=str(uuid.uuid4()),
        started_at=_iso(started_at),
        last_activity_at=_iso(started_at),
        active_seconds=active_seconds,
    )


async def _set_reading_settings(db: AsyncSession, user: User, patch: dict[str, Any]) -> None:
    settings = dict(user.settings or {})
    reading = dict(settings.get("reading", {}))
    reading.update(patch)
    settings["reading"] = reading
    user.settings = settings
    await db.commit()


async def test_record_heartbeat_creates_session_row_and_accumulates_across_calls(
    db_session: AsyncSession, redis_client: Any, factories: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=60)
    try:
        first = await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=30),
        )
        assert first.reading_seconds_total == 30
        assert first.today_reading_minutes == 0  # 30秒 < 60秒 → 0分

        rows = (
            (
                await db_session.execute(
                    select(ReadingSession).where(ReadingSession.library_item_id == item.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].active_seconds == 30

        # 同じ started_at(= 同一クライアントセッション)の 2 回目は差分のみ加算する。
        second = await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=90),
        )
        assert second.reading_seconds_total == 90

        rows_after = (
            (
                await db_session.execute(
                    select(ReadingSession).where(ReadingSession.library_item_id == item.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows_after) == 1  # 新規行を作らず既存行を更新(upsert)。
        assert rows_after[0].active_seconds == 90

        # リトライで同一値を再送しても二重加算しない。
        retry = await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=90),
        )
        assert retry.reading_seconds_total == 90
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_record_heartbeat_skips_recording_when_track_reading_time_false(
    db_session: AsyncSession, redis_client: Any, factories: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    await _set_reading_settings(db_session, user, {"track_reading_time": False})
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    try:
        result = await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=999),
        )
        assert result.reading_seconds_total == item.total_active_seconds == 0

        rows = (
            (
                await db_session.execute(
                    select(ReadingSession).where(ReadingSession.library_item_id == item.id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []  # 記録自体をスキップする。
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_record_heartbeat_read_3min_rule_suggest_mode(
    db_session: AsyncSession, redis_client: Any, factories: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=200)
    try:
        await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=200),
        )
        await db_session.refresh(item)
        assert item.status == "planned"  # suggest モードは自動変更しない。

        notes = (
            (
                await db_session.execute(
                    select(Notification).where(
                        Notification.user_id == user.id, Notification.kind == "status_suggestion"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(notes) == 1
        assert notes[0].payload["reason"] == "read_3min"
        assert notes[0].payload["suggested_status"] == "reading"
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_record_heartbeat_read_3min_rule_auto_mode_applies_status(
    db_session: AsyncSession, redis_client: Any, factories: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    await _set_reading_settings(db_session, user, {"status_transition": "auto"})
    item = await factories.make_library_item(db_session, user=user, status="up_next")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=200)
    try:
        await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=200),
        )
        await db_session.refresh(item)
        assert item.status == "reading"

        notes = (
            (
                await db_session.execute(
                    select(Notification).where(
                        Notification.user_id == user.id, Notification.kind == "status_suggestion"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert notes == []  # auto モードは通知しない。
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_record_heartbeat_read_3min_rule_off_mode_does_nothing(
    db_session: AsyncSession, redis_client: Any, factories: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    await _set_reading_settings(db_session, user, {"status_transition": "off"})
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=200)
    try:
        await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=200),
        )
        await db_session.refresh(item)
        assert item.status == "planned"

        notes = (
            (await db_session.execute(select(Notification).where(Notification.user_id == user.id)))
            .scalars()
            .all()
        )
        assert notes == []
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_record_heartbeat_reached_end_suggest_and_auto_modes(
    db_session: AsyncSession, redis_client: Any, factories: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    # 既定ドキュメント: sec-1(blk-p1,blk-p2)+sec-2(blk-p3,blk-fig1)。blk-fig1 は本文
    # ブロック 4 件中 4 番目(100%)かつ最終セクション(sec-2)内 → 読了間近(§8.2)。
    item = await factories.make_library_item(
        db_session,
        user=user,
        paper=paper,
        status="reading",
        reading_position={"revision_id": str(rev.id), "block_id": "blk-fig1"},
    )
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    try:
        await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=1),
        )
        await db_session.refresh(item)
        assert item.status == "reading"  # suggest モードは自動変更しない。

        notes = (
            (
                await db_session.execute(
                    select(Notification).where(
                        Notification.user_id == user.id, Notification.kind == "status_suggestion"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(notes) == 1
        assert notes[0].payload["reason"] == "reached_end"
        assert notes[0].payload["suggested_status"] == "done"

        # auto モードへ切り替えて再送すると done + finished_at が入る。
        await _set_reading_settings(db_session, user, {"status_transition": "auto"})
        await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=2),
        )
        await db_session.refresh(item)
        assert item.status == "done"
        assert item.finished_at is not None
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_record_heartbeat_reached_end_not_triggered_before_90_percent(
    db_session: AsyncSession, redis_client: Any, factories: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    # blk-p3 は本文ブロック 4 件中 3 番目(75%)。閾値未満のため提案なし。
    item = await factories.make_library_item(
        db_session,
        user=user,
        paper=paper,
        status="reading",
        reading_position={"revision_id": str(rev.id), "block_id": "blk-p3"},
    )
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    try:
        await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=1),
        )
        await db_session.refresh(item)
        assert item.status == "reading"

        notes = (
            (await db_session.execute(select(Notification).where(Notification.user_id == user.id)))
            .scalars()
            .all()
        )
        assert notes == []
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_record_heartbeat_reached_end_ignored_without_reading_position(
    db_session: AsyncSession, redis_client: Any, factories: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    item = await factories.make_library_item(db_session, user=user, status="reading")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    try:
        result = await record_heartbeat(
            db_session,
            redis_client,
            user=user,
            item=item,
            body=_body(started_at=started, active_seconds=1),
        )
        assert result.reading_seconds_total == 1
        await db_session.refresh(item)
        assert item.status == "reading"
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()
