"""サーバーサイドセッション・拡張トークン・メールリンク・OAuth state の保管(plans/01 §6.2)。

**決定(逸脱)**: plans/02 の初期 DDL には `sessions` / 拡張トークン / メールリンクトークンの
物理テーブルが存在しない(全 30 テーブルに含まれない)。plans/03 §1.3 は「セッション実体は
Redis `session:{sid}`」と定めており、本実装は JWT を使わずサーバーセッションを **Redis に一元化**
する(即時失効・全デバイス失効・アカウント削除時パージが確実)。値は常に SHA-256 ハッシュを
キーにして保存し、平文は発行レスポンス(Set-Cookie / トークン応答)でのみ返す。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import redis.asyncio as redis

from alinea_api.ids import new_token, sha256_hex

COOKIE_NAME = "yk_session"
EXTENSION_TOKEN_PREFIX = "yk_ext_"  # noqa: S105 — トークンの接頭辞であり秘匿値ではない

SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 日
SESSION_REFRESH_THRESHOLD = 15 * 24 * 3600  # 残り 15 日を切ったら延長(スライディング)
EXTENSION_TTL_SECONDS = 180 * 24 * 3600  # 180 日
EMAIL_LINK_TTL_SECONDS = 15 * 60  # 15 分
OAUTH_STATE_TTL_SECONDS = 10 * 60  # 10 分


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _session_key(token_hash: str) -> str:
    return f"session:{token_hash}"


def _user_sessions_key(user_id: str) -> str:
    return f"user_sessions:{user_id}"


def _ext_token_key(token_hash: str) -> str:
    return f"exttoken:{token_hash}"


def _user_ext_token_key(user_id: str) -> str:
    return f"user_exttoken:{user_id}"


def _email_link_key(token_hash: str) -> str:
    return f"emaillink:{token_hash}"


def _oauth_state_key(state: str) -> str:
    return f"oauth_state:{state}"


# ---------------------------------------------------------------------------
# セッション
# ---------------------------------------------------------------------------
async def create_session(r: redis.Redis, user_id: str) -> str:
    """新しいセッションを発行し、平文トークン(Cookie 値)を返す。"""
    token = new_token(32)
    token_hash = sha256_hex(token)
    payload = {"user_id": user_id, "created_at": _now_iso(), "last_seen_at": _now_iso()}
    await r.setex(_session_key(token_hash), SESSION_TTL_SECONDS, json.dumps(payload))
    await r.sadd(_user_sessions_key(user_id), token_hash)  # type: ignore[misc]  # redis-py: sync/async union
    await r.expire(_user_sessions_key(user_id), SESSION_TTL_SECONDS)
    return token


async def resolve_session(r: redis.Redis, token: str) -> str | None:
    """Cookie トークンから user_id を解決。スライディング延長を行う。無効なら None。"""
    if not token:
        return None
    token_hash = sha256_hex(token)
    key = _session_key(token_hash)
    raw = await r.get(key)
    if raw is None:
        return None
    try:
        payload: dict[str, Any] = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    user_id = payload.get("user_id")
    if not isinstance(user_id, str):
        return None
    remaining = await r.ttl(key)
    if isinstance(remaining, int) and 0 < remaining < SESSION_REFRESH_THRESHOLD:
        payload["last_seen_at"] = _now_iso()
        await r.setex(key, SESSION_TTL_SECONDS, json.dumps(payload))
        await r.expire(_user_sessions_key(user_id), SESSION_TTL_SECONDS)
    return user_id


async def destroy_session(r: redis.Redis, token: str) -> None:
    """現在のセッションのみ破棄する(ログアウト)。"""
    if not token:
        return
    token_hash = sha256_hex(token)
    raw = await r.get(_session_key(token_hash))
    await r.delete(_session_key(token_hash))
    if raw is not None:
        try:
            user_id = json.loads(raw).get("user_id")
        except (ValueError, json.JSONDecodeError):
            user_id = None
        if isinstance(user_id, str):
            await r.srem(_user_sessions_key(user_id), token_hash)  # type: ignore[misc]  # redis-py union


async def destroy_all_sessions(r: redis.Redis, user_id: str) -> None:
    """当該ユーザーの全セッションを即時失効(アカウント削除・全端末ログアウト)。"""
    set_key = _user_sessions_key(user_id)
    hashes = await r.smembers(set_key)  # type: ignore[misc]  # redis-py: sync/async union
    for token_hash in hashes:
        await r.delete(_session_key(token_hash))
    await r.delete(set_key)
    await destroy_extension_token(r, user_id)


# ---------------------------------------------------------------------------
# 拡張トークン(1 ユーザー 1 トークン、再発行で旧トークン即時失効)
# ---------------------------------------------------------------------------
async def create_extension_token(r: redis.Redis, user_id: str) -> tuple[str, dt.datetime]:
    """`yk_ext_` + urlsafe を発行し、(平文トークン, 失効日時) を返す。旧トークンは失効。"""
    old_hash = await r.get(_user_ext_token_key(user_id))
    if isinstance(old_hash, str):
        await r.delete(_ext_token_key(old_hash))
    token = EXTENSION_TOKEN_PREFIX + new_token(32)
    token_hash = sha256_hex(token)
    await r.setex(_ext_token_key(token_hash), EXTENSION_TTL_SECONDS, user_id)
    await r.setex(_user_ext_token_key(user_id), EXTENSION_TTL_SECONDS, token_hash)
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=EXTENSION_TTL_SECONDS)
    return token, expires_at


async def resolve_extension_token(r: redis.Redis, token: str) -> str | None:
    """`Authorization: Bearer yk_ext_…` から user_id を解決。無効なら None。"""
    if not token or not token.startswith(EXTENSION_TOKEN_PREFIX):
        return None
    user_id = await r.get(_ext_token_key(sha256_hex(token)))
    return user_id if isinstance(user_id, str) else None


async def destroy_extension_token(r: redis.Redis, user_id: str) -> None:
    old_hash = await r.get(_user_ext_token_key(user_id))
    if isinstance(old_hash, str):
        await r.delete(_ext_token_key(old_hash))
    await r.delete(_user_ext_token_key(user_id))


# ---------------------------------------------------------------------------
# メールリンク(有効 15 分・単回)
# ---------------------------------------------------------------------------
async def create_email_link_token(r: redis.Redis, email: str, next_path: str) -> str:
    token = new_token(32)
    payload = json.dumps({"email": email, "next": next_path})
    await r.setex(_email_link_key(sha256_hex(token)), EMAIL_LINK_TTL_SECONDS, payload)
    return token


async def consume_email_link_token(r: redis.Redis, token: str) -> dict[str, str] | None:
    """トークンを検証し即時に単回消費(GETDEL)。有効なら {email, next}、失効なら None。"""
    if not token:
        return None
    raw = await r.getdel(_email_link_key(sha256_hex(token)))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "email" not in data:
        return None
    return {"email": str(data["email"]), "next": str(data.get("next", "/"))}


# ---------------------------------------------------------------------------
# OAuth state(有効 10 分・単回)
# ---------------------------------------------------------------------------
async def store_oauth_state(r: redis.Redis, state: str, data: dict[str, str]) -> None:
    await r.setex(_oauth_state_key(state), OAUTH_STATE_TTL_SECONDS, json.dumps(data))


async def consume_oauth_state(r: redis.Redis, state: str) -> dict[str, str] | None:
    if not state:
        return None
    raw = await r.getdel(_oauth_state_key(state))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
