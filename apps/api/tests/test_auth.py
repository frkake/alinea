"""認証テスト。

- PY-AUTH-01: 認証スイープ(anonymous 以外は未認証で 401)。
- PY-AUTH-02: メールリンク(発行→単回検証で Set-Cookie、再利用/失効は link_expired へ 302、応答不変)。
- PY-AUTH-03: 拡張トークン(スコープ内のみ通り、スコープ外は 403、再発行で旧トークン即時失効)。
- PY-DB-03: users 1 行 DELETE で個人資産が全カスケード削除。
- レート制限: email/request 5 回/10 分超過で 429 + Retry-After。
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from alinea_api.deps import get_settings_dep
from alinea_api.services import session_service
from alinea_api.services.oauth import OAuthProfile
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_api.settings import ApiSettings
from alinea_core.db.models import (
    ByokApiKey,
    ChatMessage,
    ChatThread,
    Collection,
    Job,
    LibraryItem,
    Notification,
    Paper,
    SavedFilter,
    User,
)
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# anonymous 区分(plans/03 §1.2)+ 運用エンドポイント。ここに無い経路は未認証で 401 必須。
ANONYMOUS_PATHS: set[tuple[str, str]] = {
    ("get", "/api/healthz"),
    ("get", "/api/readyz"),
    ("get", "/api/auth/oauth/{provider}/start"),
    ("get", "/api/auth/oauth/{provider}/callback"),
    ("post", "/api/auth/email/request"),
    ("get", "/api/auth/email/verify"),
    # 共有ページ(4c)は匿名アクセスが仕様(plans/03 §14。トークン自体が資格情報)。
    ("get", "/api/share/collections/{token}"),
}

_DUMMY_UUID = "00000000-0000-0000-0000-000000000000"


def _fill_path(path: str) -> str:
    out = path
    while "{" in out:
        start = out.index("{")
        end = out.index("}")
        out = out[:start] + _DUMMY_UUID + out[end + 1 :]
    return out


# ---------------------------------------------------------------------------
# PY-AUTH-01
# ---------------------------------------------------------------------------
async def test_auth_sweep_non_anonymous_requires_login(bare_client: AsyncClient) -> None:
    from alinea_api.main import app

    schema = app.openapi()
    checked = 0
    for path, methods in schema["paths"].items():
        for method in methods:
            if (method, path) in ANONYMOUS_PATHS:
                continue
            url = _fill_path(path)
            resp = await bare_client.request(method.upper(), url, json={})
            assert resp.status_code == 401, f"{method.upper()} {url} -> {resp.status_code}"
            assert resp.json()["code"] == "unauthorized"
            checked += 1
    assert checked >= 4


# ---------------------------------------------------------------------------
# PY-AUTH-02
# ---------------------------------------------------------------------------
async def test_email_link_login_flow(client: AsyncClient, mailpit: Any, unique_email: str) -> None:
    r = await client.post("/api/auth/email/request", json={"email": unique_email})
    assert r.status_code == 202
    assert r.json() == {"sent": True}

    token = await mailpit.latest_link_token(unique_email)
    assert token, "Mailpit にログインリンクが届いていません"

    verify = await client.get(f"/api/auth/email/verify?token={token}", follow_redirects=False)
    assert verify.status_code == 302
    assert "yk_session" in verify.headers.get("set-cookie", "")

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == unique_email
    assert "email" in me.json()["user"]["providers"]


async def test_email_link_is_single_use_and_expiry_redirects(
    client: AsyncClient, mailpit: Any, unique_email: str
) -> None:
    await client.post("/api/auth/email/request", json={"email": unique_email})
    token = await mailpit.latest_link_token(unique_email)
    assert token

    first = await client.get(f"/api/auth/email/verify?token={token}", follow_redirects=False)
    assert first.status_code == 302
    # 単回: 同じトークンの再利用は失敗して link_expired へ。
    second = await client.get(f"/api/auth/email/verify?token={token}", follow_redirects=False)
    assert second.status_code == 302
    assert "error=link_expired" in second.headers.get("location", "")
    # 不正トークンも同様。
    bogus = await client.get("/api/auth/email/verify?token=deadbeef", follow_redirects=False)
    assert bogus.status_code == 302
    assert "error=link_expired" in bogus.headers.get("location", "")


async def test_email_request_response_is_uniform(
    client: AsyncClient, db_session: AsyncSession, unique_email: str
) -> None:
    # 既存アカウントを作っておく。
    await upsert_user_by_email(db_session, unique_email, provider="email")
    existing = await client.post("/api/auth/email/request", json={"email": unique_email})
    new = await client.post(
        "/api/auth/email/request", json={"email": f"nobody-{uuid.uuid4().hex}@example.com"}
    )
    assert existing.status_code == new.status_code == 202
    assert existing.json() == new.json() == {"sent": True}


# ---------------------------------------------------------------------------
# PY-AUTH-03
# ---------------------------------------------------------------------------
async def test_extension_token_scope_and_reissue(
    client: AsyncClient,
    bare_client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    session_token = await create_session(redis_client, user.id)
    client.cookies.set("yk_session", session_token)

    issued = await client.post("/api/auth/extension-token")
    assert issued.status_code == 201
    token1 = issued.json()["token"]
    assert token1.startswith("yk_ext_")

    # スコープ内: GET /api/auth/me は拡張トークンで通る。
    me = await bare_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token1}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == unique_email

    # スコープ外: logout はセッション専用 → 403 token_scope_exceeded。
    logout = await bare_client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {token1}"}
    )
    assert logout.status_code == 403
    assert logout.json()["code"] == "token_scope_exceeded"

    # 再発行: 旧トークンは即時失効(401)、新トークンは有効。
    reissued = await client.post("/api/auth/extension-token")
    assert reissued.status_code == 201
    token2 = reissued.json()["token"]
    assert token2 != token1

    old = await bare_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token1}"})
    assert old.status_code == 401
    new = await bare_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token2}"})
    assert new.status_code == 200


async def test_logout_destroys_session(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    session_token = await create_session(redis_client, user.id)
    client.cookies.set("yk_session", session_token)

    assert (await client.get("/api/auth/me")).status_code == 200
    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204
    # クッキーはクライアント側で失効し、セッションも Redis から消えている。
    client.cookies.set("yk_session", session_token)
    assert (await client.get("/api/auth/me")).status_code == 401


# ---------------------------------------------------------------------------
# レート制限
# ---------------------------------------------------------------------------
async def test_email_request_rate_limited(client: AsyncClient, unique_email: str) -> None:
    last = None
    for _ in range(6):
        last = await client.post("/api/auth/email/request", json={"email": unique_email})
    assert last is not None
    assert last.status_code == 429
    assert last.json()["code"] == "rate_limited"
    assert last.headers.get("Retry-After")


# ---------------------------------------------------------------------------
# PY-DB-03: アカウント削除カスケード
# ---------------------------------------------------------------------------
async def test_user_deletion_cascades_personal_assets(db_session: AsyncSession) -> None:
    email = f"cascade-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")

    paper = Paper(title="Cascade Test", visibility="private", owner_user_id=user.id)
    db_session.add(paper)
    await db_session.flush()

    item = LibraryItem(user_id=user.id, paper_id=paper.id, status="reading")
    db_session.add(item)
    await db_session.flush()

    thread = ChatThread(library_item_id=item.id, title="メイン", is_main=True)
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        ChatMessage(thread_id=thread.id, role="user", content={"blocks": []}, text_plain="hi")
    )
    db_session.add(Collection(user_id=user.id, name="読書会"))
    db_session.add(Notification(user_id=user.id, kind="translation_complete", payload={}))
    db_session.add(SavedFilter(user_id=user.id, name="未読"))
    db_session.add(
        ByokApiKey(user_id=user.id, provider="openai", encrypted_key=b"x", key_hint="abcd")
    )
    await db_session.commit()

    user_id = str(user.id)
    paper_id = str(paper.id)
    thread_id = str(thread.id)

    async def _count(model: Any, column: Any) -> int:
        result = await db_session.execute(
            select(func.count()).select_from(model).where(column == user_id)
        )
        return int(result.scalar_one())

    assert await _count(LibraryItem, LibraryItem.user_id) == 1
    assert await _count(Collection, Collection.user_id) == 1

    # 実行: ユーザー行を削除 → FK ON DELETE CASCADE で個人資産が全消去。
    assert await purge_user(db_session, user_id) is True
    db_session.expire_all()  # DB カスケード削除を ORM 側にも反映(identity map をクリア)

    assert (await db_session.get(User, user_id)) is None
    assert await _count(LibraryItem, LibraryItem.user_id) == 0
    assert await _count(Collection, Collection.user_id) == 0
    assert await _count(Notification, Notification.user_id) == 0
    assert await _count(SavedFilter, SavedFilter.user_id) == 0
    assert await _count(ByokApiKey, ByokApiKey.user_id) == 0
    # private Paper(owner) と配下(ChatThread/Message)も消える。
    assert (await db_session.get(Paper, paper_id)) is None
    thread_count = await db_session.execute(
        select(func.count()).select_from(ChatThread).where(ChatThread.id == thread_id)
    )
    assert int(thread_count.scalar_one()) == 0


# ---------------------------------------------------------------------------
# OAuth: /api/auth/oauth/{provider}/start・/callback(plans/01 §6.1・plans/03 §2.1-2.2)
# ---------------------------------------------------------------------------
async def test_oauth_start_rejects_unsupported_provider(client: AsyncClient) -> None:
    resp = await client.get("/api/auth/oauth/twitter/start", follow_redirects=False)
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_oauth_start_redirects_to_login_when_provider_unconfigured(
    client: AsyncClient,
) -> None:
    from alinea_api.main import app

    unconfigured = ApiSettings(oauth_google_client_id="", oauth_google_client_secret="")
    app.dependency_overrides[get_settings_dep] = lambda: unconfigured
    try:
        resp = await client.get("/api/auth/oauth/google/start", follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)
    assert resp.status_code == 302
    assert "error=oauth_unavailable" in resp.headers["location"]


async def test_oauth_start_stores_state_and_redirects_to_provider(
    client: AsyncClient, redis_client: Any
) -> None:
    from alinea_api.main import app

    configured = ApiSettings(oauth_google_client_id="gid", oauth_google_client_secret="gsecret")
    app.dependency_overrides[get_settings_dep] = lambda: configured
    try:
        resp = await client.get(
            "/api/auth/oauth/google/start?next=/library", follow_redirects=False
        )
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=gid" in location

    state = parse_qs(urlparse(location).query)["state"][0]
    stored = await session_service.consume_oauth_state(redis_client, state)
    assert stored == {"next": "/library", "provider": "google"}


async def test_oauth_callback_fails_when_code_or_state_missing(client: AsyncClient) -> None:
    no_params = await client.get("/api/auth/oauth/google/callback", follow_redirects=False)
    assert no_params.status_code == 302
    assert "error=oauth_failed" in no_params.headers["location"]

    unsupported = await client.get(
        "/api/auth/oauth/twitter/callback?code=x&state=y", follow_redirects=False
    )
    assert unsupported.status_code == 302
    assert "error=oauth_failed" in unsupported.headers["location"]


async def test_oauth_callback_fails_when_state_unknown(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/auth/oauth/google/callback?code=abc&state=does-not-exist",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=oauth_failed" in resp.headers["location"]


async def test_oauth_callback_success_creates_user_and_sets_session(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alinea_api.main import app
    from alinea_api.routers import auth as auth_router

    email = f"oauth-{uuid.uuid4().hex}@example.com"

    async def _fake_exchange(provider: Any, code: str, redirect: str) -> OAuthProfile:
        assert code == "auth-code-1"
        return OAuthProfile(
            subject="g-123", email=email, display_name="Google User", avatar_url=None
        )

    monkeypatch.setattr(auth_router, "exchange_and_fetch_profile", _fake_exchange)

    state = "state-" + uuid.uuid4().hex
    await session_service.store_oauth_state(
        redis_client, state, {"next": "/library", "provider": "google"}
    )
    configured = ApiSettings(oauth_google_client_id="gid", oauth_google_client_secret="gsecret")
    app.dependency_overrides[get_settings_dep] = lambda: configured
    try:
        resp = await client.get(
            f"/api/auth/oauth/google/callback?code=auth-code-1&state={state}",
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)

    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:3000/library"
    assert "yk_session" in resp.headers.get("set-cookie", "")

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    try:
        assert user.display_name == "Google User"
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_oauth_callback_exchange_failure_redirects_to_login_error(
    client: AsyncClient, redis_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alinea_api.main import app
    from alinea_api.routers import auth as auth_router

    async def _boom(provider: Any, code: str, redirect: str) -> OAuthProfile:
        raise ValueError("token exchange failed")

    monkeypatch.setattr(auth_router, "exchange_and_fetch_profile", _boom)

    state = "state-" + uuid.uuid4().hex
    await session_service.store_oauth_state(
        redis_client, state, {"next": "/dashboard", "provider": "google"}
    )
    configured = ApiSettings(oauth_google_client_id="gid", oauth_google_client_secret="gsecret")
    app.dependency_overrides[get_settings_dep] = lambda: configured
    try:
        resp = await client.get(
            f"/api/auth/oauth/google/callback?code=bad&state={state}", follow_redirects=False
        )
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)

    assert resp.status_code == 302
    assert "error=oauth_failed" in resp.headers["location"]


# ---------------------------------------------------------------------------
# アカウント削除: DELETE /api/auth/account
# ---------------------------------------------------------------------------
async def test_delete_account_requires_confirm_phrase(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    session_token = await create_session(redis_client, user.id)
    client.cookies.set("yk_session", session_token)
    try:
        resp = await client.request("DELETE", "/api/auth/account", json={"confirm": "yes please"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation_error"
    finally:
        await purge_user(db_session, str(user.id))
        await db_session.commit()


async def test_delete_account_enqueues_job_and_destroys_all_sessions(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, unique_email: str
) -> None:
    user = await upsert_user_by_email(db_session, unique_email, provider="email")
    session_token = await create_session(redis_client, user.id)
    client.cookies.set("yk_session", session_token)

    resp = await client.request("DELETE", "/api/auth/account", json={"confirm": "delete"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert job_id

    job = await db_session.get(Job, job_id)
    assert job is not None
    assert job.kind == "account_delete"
    assert str(job.user_id) == str(user.id)

    # 全セッション即時失効 + クッキー削除(データ本体の削除はジョブが行う。ここでは未実行)。
    client.cookies.set("yk_session", session_token)
    after = await client.get("/api/auth/me")
    assert after.status_code == 401

    await purge_user(db_session, str(user.id))
    await db_session.commit()
