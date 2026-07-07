"""notifications API テスト(M1-07 / plans/03 §16・plans/05 §12・docs/06 §2, §7)。

- PY-NTF-01(締切以外): 通知 2 種の生成(翻訳完了/提案)・重複防止(§2.3 の 1 回限り保証・
  docs/06 §2 の「1 回だけ出す」・plans/05 §12.3 の未読ダブり防止)・設定 OFF ゲート・
  SSE ``notification.created`` publish・一覧の未読件数・cursor ページング・read-all。
  ``deadline_reminder`` は M2-09(本タスク対象外)。
- PY-NTF-02: 提案 2 択 action(apply=ステータス変更が §5.4 と同一の内部処理・
  dismiss=そのまま)・resolved 済み再操作は 409・apply が status_suggestion 以外で 422。

DB は実 PostgreSQL。認証はセッション直発行 + cookie(test_library_api.py と同じ方式)。
他タスクの WIP ルータを巻き込まないよう、本タスク所有のルータ(notifications)のみを
マウントした専用アプリで検証する。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.events import read_events_since
from yakudoku_api.services.notifications import fire_status_suggestion, fire_translation_complete
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_core.db.models import User


def _build_app() -> FastAPI:
    """本タスク所有ルータ(notifications)のみをマウントしたアプリ。

    並行タスクの WIP ルータに import を巻き込まれないよう、test_library_api.py /
    test_dashboard.py と同じ方針で専用アプリを組み立てる。``notifications`` ルータは
    ``library_items`` の読み出し専用ヘルパ(``_summary_for``)をモジュールレベルで
    import するのみ(ルータそのものはマウントしない)。
    """
    from yakudoku_api.errors import register_exception_handlers
    from yakudoku_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from yakudoku_api.ratelimit import RateLimitMiddleware
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.routers import notifications
    from yakudoku_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(notifications.router)
    return app


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, User]]:
    email = f"ntf-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)  # rollback 後に ORM 属性へ触れないよう先に確定させる
    token = await create_session(redis_client, user.id)
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield ac, user
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


# ============================================================================
# PY-NTF-01: 発火関数(重複防止・設定ゲート・SSE publish)
# ============================================================================
async def test_fire_translation_complete_dedupes_and_publishes_sse(
    db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    user = await factories.make_user(db_session)
    item = await factories.make_library_item(db_session, user=user)
    await db_session.commit()

    note = await fire_translation_complete(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        job_id="job-1",
    )
    assert note is not None
    assert note.kind == "translation_complete"
    assert note.payload == {
        "library_item_id": str(item.id),
        "paper_title": "Rectified Flow",
        "job_id": "job-1",
    }

    # 同一 job_id は 1 回限り保証(§2.3)で再度発火しない。
    dup = await fire_translation_complete(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        job_id="job-1",
    )
    assert dup is None

    # SSE notification.created が 1 回だけ publish されている(plans/05 §12.1)。
    events = await read_events_since(redis_client, str(user.id), "0-0")
    created = [e for e in events if e[1] == "notification.created"]
    assert len(created) == 1
    assert created[0][2]["kind"] == "translation_complete"
    assert created[0][2]["payload"]["job_id"] == "job-1"
    assert created[0][2]["payload"]["kind"] == "translation_complete"


async def test_fire_translation_complete_respects_settings_off(
    db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    user = await factories.make_user(db_session)
    user.settings = {"notifications": {"translation_complete": False}}
    item = await factories.make_library_item(db_session, user=user)
    await db_session.commit()

    gated = await fire_translation_complete(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        job_id="job-off",
    )
    assert gated is None


async def test_fire_status_suggestion_proposal_fires_once(
    db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    user = await factories.make_user(db_session)
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    note = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        reason="read_3min",
        suggested_status="reading",
    )
    assert note is not None
    assert note.payload["suggested_status"] == "reading"
    assert note.payload["reason"] == "read_3min"
    assert note.payload["resolved"] is None

    # 既読にしても「1 回だけ出す」(docs/06 §2)ので再度出ない。
    note.read = True
    await db_session.commit()
    again = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        reason="read_3min",
        suggested_status="reading",
    )
    assert again is None

    # reason が異なる reached_end は別枠として発火できる。
    end_note = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        reason="reached_end",
        suggested_status="done",
    )
    assert end_note is not None
    assert end_note.payload["reason"] == "reached_end"


async def test_fire_status_suggestion_promotion_dedupes_unread_only(
    db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    user = await factories.make_user(db_session)
    item = await factories.make_library_item(db_session, user=user)
    await db_session.commit()

    first = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        reason="promotion_b_to_a",
        revision_id=str(uuid.uuid4()),
    )
    assert first is not None
    assert first.payload["action"] == "promote_revision"

    # 未読が残っている間は再挿入しない(plans/05 §12.3)。
    dup = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        reason="promotion_b_to_a",
        revision_id=str(uuid.uuid4()),
    )
    assert dup is None

    # 既読後(=7 日後の再検知相当)は再度発火できる。
    first.read = True
    await db_session.commit()
    again = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Rectified Flow",
        reason="promotion_b_to_a",
        revision_id=str(uuid.uuid4()),
    )
    assert again is not None


# ============================================================================
# PY-NTF-01: 一覧・未読件数・cursor・read-all
# ============================================================================
async def test_list_notifications_unread_count_and_read_all(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any
) -> None:
    ac, user = auth
    for i in range(3):
        note = await fire_translation_complete(
            db_session,
            redis_client,
            user_id=str(user.id),
            library_item_id=str(uuid.uuid4()),
            paper_title=f"Paper {i}",
            job_id=f"job-list-{i}",
        )
        assert note is not None

    res = await ac.get("/api/notifications")
    assert res.status_code == 200
    body = res.json()
    assert body["unread"] == 3
    assert len(body["items"]) == 3
    assert {item["kind"] for item in body["items"]} == {"translation_complete"}
    # created_at 降順(新しい順)。
    created_ats = [item["created_at"] for item in body["items"]]
    assert created_ats == sorted(created_ats, reverse=True)

    res2 = await ac.post("/api/notifications/read-all")
    assert res2.status_code == 200
    assert res2.json()["updated"] == 3

    res3 = await ac.get("/api/notifications")
    assert res3.json()["unread"] == 0


async def test_notifications_cursor_pagination_no_dup_no_miss(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any
) -> None:
    ac, user = auth
    for i in range(5):
        note = await fire_translation_complete(
            db_session,
            redis_client,
            user_id=str(user.id),
            library_item_id=str(uuid.uuid4()),
            paper_title=f"Paper {i}",
            job_id=f"job-page-{i}",
        )
        assert note is not None

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        res = await ac.get("/api/notifications", params=params)
        assert res.status_code == 200
        body = res.json()
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_notifications_invalid_cursor_is_422(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any
) -> None:
    ac, _user = auth
    res = await ac.get("/api/notifications", params={"cursor": "not-a-valid-cursor"})
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "validation_error"


async def test_patch_notification_marks_read(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any
) -> None:
    ac, user = auth
    note = await fire_translation_complete(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(uuid.uuid4()),
        paper_title="Paper",
        job_id="job-patch",
    )
    assert note is not None

    res = await ac.patch(f"/api/notifications/{note.id}", json={"read": True})
    assert res.status_code == 200
    assert res.json()["read"] is True


async def test_notification_action_requires_target_user(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    ac, _user = auth
    other = await factories.make_user(db_session)
    await db_session.commit()
    note = await fire_translation_complete(
        db_session,
        redis_client,
        user_id=str(other.id),
        library_item_id=str(uuid.uuid4()),
        paper_title="Paper",
        job_id="job-other-user",
    )
    assert note is not None

    res = await ac.patch(f"/api/notifications/{note.id}", json={"read": True})
    assert res.status_code == 404
    res2 = await ac.post("/api/notifications/not-an-int/action", json={"action": "apply"})
    assert res2.status_code == 404


# ============================================================================
# PY-NTF-02: 提案 2 択 action
# ============================================================================
async def test_action_apply_changes_status_like_patch(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    ac, user = auth
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    note = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Paper",
        reason="read_3min",
        suggested_status="reading",
    )
    assert note is not None

    res = await ac.post(f"/api/notifications/{note.id}/action", json={"action": "apply"})
    assert res.status_code == 200
    body = res.json()
    assert body["notification"]["payload"]["resolved"] == "applied"
    assert body["library_item"]["status"] == "reading"

    await db_session.refresh(item)
    assert item.status == "reading"


async def test_action_apply_done_records_finished_at_once(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    ac, user = auth
    item = await factories.make_library_item(db_session, user=user, status="reading")
    await db_session.commit()

    note = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Paper",
        reason="reached_end",
        suggested_status="done",
    )
    assert note is not None

    res = await ac.post(f"/api/notifications/{note.id}/action", json={"action": "apply"})
    assert res.status_code == 200
    body = res.json()
    assert body["library_item"]["status"] == "done"
    assert body["library_item"]["finished_at"] is not None


async def test_action_dismiss_keeps_status_unchanged(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    ac, user = auth
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    note = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Paper",
        reason="read_3min",
        suggested_status="reading",
    )
    assert note is not None

    res = await ac.post(f"/api/notifications/{note.id}/action", json={"action": "dismiss"})
    assert res.status_code == 200
    body = res.json()
    assert body["notification"]["payload"]["resolved"] == "dismissed"
    assert body["library_item"]["status"] == "planned"


async def test_action_conflict_when_already_resolved(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    ac, user = auth
    item = await factories.make_library_item(db_session, user=user)
    await db_session.commit()

    note = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Paper",
        reason="read_3min",
        suggested_status="reading",
    )
    assert note is not None

    first = await ac.post(f"/api/notifications/{note.id}/action", json={"action": "apply"})
    assert first.status_code == 200

    second = await ac.post(f"/api/notifications/{note.id}/action", json={"action": "dismiss"})
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"


async def test_action_422_for_non_status_suggestion_kind(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any
) -> None:
    ac, user = auth
    note = await fire_translation_complete(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(uuid.uuid4()),
        paper_title="Paper",
        job_id="job-wrong-kind",
    )
    assert note is not None

    res = await ac.post(f"/api/notifications/{note.id}/action", json={"action": "apply"})
    assert res.status_code == 422
    assert res.json()["code"] == "validation_error"


async def test_action_apply_promotion_variant_marks_resolved_without_crash(
    auth: tuple[AsyncClient, User], db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    """B→A 昇格提案の apply。adopt-revision 本体接続は M1-22(followup)。"""
    ac, user = auth
    item = await factories.make_library_item(db_session, user=user)
    await db_session.commit()

    note = await fire_status_suggestion(
        db_session,
        redis_client,
        user_id=str(user.id),
        library_item_id=str(item.id),
        paper_title="Paper",
        reason="promotion_b_to_a",
        revision_id=str(uuid.uuid4()),
    )
    assert note is not None

    res = await ac.post(f"/api/notifications/{note.id}/action", json={"action": "apply"})
    assert res.status_code == 200
    body = res.json()
    assert body["notification"]["payload"]["resolved"] == "applied"
    assert body["notification"]["payload"]["action"] == "promote_revision"
