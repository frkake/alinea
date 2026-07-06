"""FastAPI 依存関係: DB セッション・Redis・認証ユーザー解決(plans/03 §1.2)。

認証区分:
- `require_session_user`: セッション必須。拡張トークンでの呼び出しは 403 `token_scope_exceeded`。
- `require_user_or_ext`: セッションまたは拡張トークン(§1.2.1 のスコープ内エンドポイント用)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import User
from yakudoku_core.db.session import get_sessionmaker

from yakudoku_api.errors import ProblemException
from yakudoku_api.redis_client import get_redis
from yakudoku_api.services import session_service
from yakudoku_api.settings import ApiSettings, get_api_settings


async def get_db() -> AsyncIterator[AsyncSession]:
    """1 リクエスト 1 セッション。"""
    maker = get_sessionmaker()
    async with maker() as session:
        yield session


def get_redis_dep() -> redis.Redis:
    return get_redis()


def get_settings_dep() -> ApiSettings:
    return get_api_settings()


DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[redis.Redis, Depends(get_redis_dep)]
SettingsDep = Annotated[ApiSettings, Depends(get_settings_dep)]


@dataclass(slots=True)
class AuthContext:
    """解決済み認証情報。`kind` は資格情報の種類。"""

    user: User
    kind: str  # "session" | "ext"
    session_token: str | None = None


async def _resolve_auth(request: Request, db: AsyncSession, r: redis.Redis) -> AuthContext | None:
    """Cookie セッション → Bearer 拡張トークンの順で認証を解決する。"""
    token = request.cookies.get(session_service.COOKIE_NAME)
    if token:
        user_id = await session_service.resolve_session(r, token)
        if user_id is not None:
            user = await db.get(User, user_id)
            if user is not None:
                return AuthContext(user=user, kind="session", session_token=token)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[len("Bearer ") :].strip()
        user_id = await session_service.resolve_extension_token(r, bearer)
        if user_id is not None:
            user = await db.get(User, user_id)
            if user is not None:
                return AuthContext(user=user, kind="ext")

    return None


async def get_auth_context(request: Request, db: DbDep, r: RedisDep) -> AuthContext | None:
    return await _resolve_auth(request, db, r)


AuthContextDep = Annotated[AuthContext | None, Depends(get_auth_context)]


async def require_user_or_ext(auth: AuthContextDep) -> User:
    """区分 `session|ext`。未認証は 401。"""
    if auth is None:
        raise ProblemException("unauthorized")
    return auth.user


async def require_session_context(auth: AuthContextDep) -> AuthContext:
    """区分 `session`(セッショントークンが必要なエンドポイント用)。AuthContext を返す。"""
    if auth is None:
        raise ProblemException("unauthorized")
    if auth.kind == "ext":
        raise ProblemException("token_scope_exceeded")
    return auth


async def require_session_user(auth: AuthContextDep) -> User:
    """区分 `session`。拡張トークンでの呼び出しは 403 `token_scope_exceeded`、未認証は 401。"""
    if auth is None:
        raise ProblemException("unauthorized")
    if auth.kind == "ext":
        raise ProblemException("token_scope_exceeded")
    return auth.user


CurrentUser = Annotated[User, Depends(require_session_user)]
CurrentUserOrExt = Annotated[User, Depends(require_user_or_ext)]
SessionContext = Annotated[AuthContext, Depends(require_session_context)]
