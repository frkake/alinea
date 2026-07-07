"""apps/api テスト用フィクスチャ。

- DB は実 PostgreSQL(docker-compose)。SQLite 代替禁止(PGroonga 依存)。
- FastAPI は httpx.AsyncClient + ASGITransport の in-process で叩く。
- テストデータは uuid でユニーク化し、残っても他テストを壊さない。
- アプリが持つ lru_cache 済みの Engine / Redis はイベントループに束縛されるため、
  各テスト前後でキャッシュをクリア・破棄して「別ループ」エラーを防ぐ。
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 編集可能インストールの補助(pth が無い環境でも import 可能にする)。
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ローカル HTTP はプロキシを迂回する(企業プロキシ環境)。
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://yakudoku:yakudoku@localhost:5432/yakudoku",
)
MAILPIT_API = os.environ.get("MAILPIT_API", "http://localhost:8025")


@pytest.fixture(autouse=True, scope="session")
def _clear_rate_limit_windows() -> Iterator[None]:
    """前回実行が残したレート制限ウィンドウを掃除する(短時間の再実行が 429 化するのを防ぐ)。"""
    import redis as redis_sync

    r = redis_sync.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    try:
        for key in r.scan_iter("rl:*"):
            r.delete(key)
    finally:
        r.close()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _reset_shared_clients() -> AsyncIterator[None]:
    """各テストで Engine / Redis / sessionmaker をこのループに作り直す。"""
    from yakudoku_api.redis_client import get_redis
    from yakudoku_core.db.session import get_engine, get_sessionmaker

    get_redis.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    try:
        await get_redis().aclose()
    finally:
        await get_engine().dispose()
        get_redis.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(DATABASE_URL, poolclass=None)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Any]:
    import redis.asyncio as redis
    from yakudoku_api.settings import get_api_settings

    client: Any = redis.Redis.from_url(get_api_settings().redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from yakudoku_api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def bare_client() -> AsyncIterator[AsyncClient]:
    """クッキーを持たないクライアント(Bearer 認証・認証スイープ用)。"""
    from yakudoku_api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        yield ac


@pytest.fixture
def unique_email() -> str:
    return f"u-{uuid.uuid4().hex}@example.com"


class MailpitClient:
    """Mailpit HTTP API からメールリンクのトークンを抽出する(plans/12 §)。"""

    _LINK_RE = re.compile(r"email/verify\?token=([A-Za-z0-9_\-]+)")

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def latest_link_token(self, to_email: str) -> str | None:
        async with httpx.AsyncClient(trust_env=False, timeout=10.0) as hc:
            listing = await hc.get(f"{self.base_url}/api/v1/messages", params={"limit": 50})
            listing.raise_for_status()
            messages = listing.json().get("messages", [])
            for message in messages:
                recipients = [addr.get("Address", "").lower() for addr in message.get("To", [])]
                if to_email.lower() not in recipients:
                    continue
                detail = await hc.get(f"{self.base_url}/api/v1/message/{message['ID']}")
                detail.raise_for_status()
                body = detail.json()
                text = (body.get("Text") or "") + (body.get("HTML") or "")
                match = self._LINK_RE.search(text)
                if match:
                    return match.group(1)
        return None


@pytest.fixture
def mailpit() -> MailpitClient:
    return MailpitClient(MAILPIT_API)


@pytest.fixture
def factories() -> Any:
    """共有 async ファクトリ(plans/12 §2.3・factories.py)。

    使い方: `await factories.make_library_item(db_session, status="reading")`。
    ファクトリは flush まで(commit しない)。API(別セッション)から見せるテストは
    構築後に `await db_session.commit()` すること。後始末は purge_user(users カスケード)。
    """
    import factories as _factories

    return _factories
