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

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_core.db.models import (
    ByokApiKey,
    ChatMessage,
    ChatThread,
    Collection,
    LibraryItem,
    Notification,
    Paper,
    SavedFilter,
    User,
)

# anonymous 区分(plans/03 §1.2)+ 運用エンドポイント。ここに無い経路は未認証で 401 必須。
ANONYMOUS_PATHS: set[tuple[str, str]] = {
    ("get", "/api/healthz"),
    ("get", "/api/readyz"),
    ("get", "/api/auth/oauth/{provider}/start"),
    ("get", "/api/auth/oauth/{provider}/callback"),
    ("post", "/api/auth/email/request"),
    ("get", "/api/auth/email/verify"),
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
    from yakudoku_api.main import app

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
