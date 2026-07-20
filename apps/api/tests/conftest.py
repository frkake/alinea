"""apps/api テスト用フィクスチャ。

- DB は実 PostgreSQL(docker-compose)。SQLite 代替禁止(PGroonga 依存)。
- **分離(Task 32)**: pytest-xdist の worker ごとに専用テスト DB を作成し、マイグレーションを
  適用して(0002 のシードを保持したまま)用意する。suite 終了時に drop する。これにより
  API 経由の commit が残らず、seed テストと通常テストを実行順で入れ替えても同じ結果になる。
  仕組みは ``alinea_core.testing.testdb`` を参照(pgvector 非同梱環境の扱いを含む)。
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

from alinea_core.testing import testdb  # noqa: E402  (env 設定後に import する)

MAILPIT_API = os.environ.get("MAILPIT_API", "http://localhost:8025")


def _database_url() -> str:
    """アクティブなテスト DB の URL(worker 分離済み)。"""
    return testdb.database_url()


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_database() -> Iterator[None]:
    """worker 専用のテスト DB を作成・マイグレーションし、suite 終了時に drop する。

    DATABASE_URL を差し替えるため、以降の Engine / settings は分離 DB を指す。
    """
    testdb.setup_test_database()
    try:
        yield
    finally:
        testdb.teardown_test_database()


@pytest.fixture(scope="session")
def pgvector_available() -> bool:
    """埋め込みテーブル(vector 拡張)が実体化しているか(env 依存)。"""
    return testdb.pgvector_enabled()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """``@pytest.mark.requires_pgvector`` を pgvector 非同梱環境で理由付き skip する。"""
    if testdb.pgvector_enabled():
        return
    skip = pytest.mark.skip(
        reason="pgvector(vector 拡張)非同梱の DB。埋め込みテーブルは stamp 経路で未作成。"
    )
    for item in items:
        if item.get_closest_marker("requires_pgvector") is not None:
            item.add_marker(skip)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_pgvector: 実 pgvector(vector 拡張)を要する。非同梱環境では skip。",
    )


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
    from alinea_api.redis_client import get_redis
    from alinea_core.db.session import get_engine, get_sessionmaker

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
    engine = create_async_engine(_database_url(), poolclass=None)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Any]:
    import redis.asyncio as redis
    from alinea_api.settings import get_api_settings

    client: Any = redis.Redis.from_url(get_api_settings().redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from alinea_api.main import app

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
    from alinea_api.main import app

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
