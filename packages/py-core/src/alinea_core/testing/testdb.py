"""pytest-xdist worker ごとにテスト用 PostgreSQL データベースを分離する補助。

**なぜ必要か**: api / worker のテストは実 PostgreSQL を共有していた。``db_session`` は
rollback するだけで、API 経由(別セッション)で commit された行は残り続ける。これが
クロステスト汚染と「実行順で結果が変わる」不安定さの根本原因だった。

**方式**: セッション(= pytest-xdist の worker)ごとに ``<base>_test_<worker>`` という
専用データベースを **作成してマイグレーションを適用**する。0002 のシード(llm_models /
llm_task_routes / quota_limits)はマイグレーションが再投入するため、シードを消さずに
毎回きれいな DB を得られる(``TRUNCATE ... CASCADE`` はシード参照データを破壊するため使わない)。

**pgvector 非同梱環境の扱い(重要・偽装しない)**: マイグレーション 0016 は
``CREATE EXTENSION vector`` と埋め込みテーブル(1536 次元 + HNSW)を作る。開発 DB イメージが
``vector`` 拡張を持たない環境では 0016 の DDL は適用できない。この場合は

  1. 0015 まで通常どおり ``upgrade``、
  2. 0016 を ``stamp``(= バージョン表だけ進め、vector 依存 DDL は **作らない**)、
  3. 0017〜head を ``upgrade``(いずれも pgvector 非依存)

という経路で head へ到達させる。埋め込みテーブルは **意図的に作らない**(silently pass では
なく ``pgvector_enabled()==False`` として公開する)。実 pgvector を要するテストは
``requires_pgvector`` で理由付き skip する。既存の埋め込みテスト群は in-memory ストア/
InMemorySemanticIndex を使うため、この経路でも決定的に回る。

``vector`` 拡張が利用可能な環境(本番同等イメージ)では素直に ``upgrade head`` する。
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

DEFAULT_DATABASE_URL = "postgresql+asyncpg://alinea:alinea@localhost:5432/alinea"

# pgvector 依存の分岐点。0015 まで通常適用 → 0016 を stamp → 0017 以降を適用する。
_PGVECTOR_REVISION = "0016_semantic_embeddings"
_PGVECTOR_PARENT = "0015_article_publications"

# マイグレーション適用中に PostgreSQL 管理接続で使う保守用データベース。
_MAINTENANCE_DB = "postgres"

_WORKER_RE = re.compile(r"[^a-zA-Z0-9_]")


@dataclass(frozen=True)
class TestDbState:
    """アクティブなテスト DB の状態(セッション単位のシングルトン)。"""

    base_url: str
    """元の DATABASE_URL(teardown で復元する)。"""
    worker_id: str
    db_name: str
    async_url: str
    """このセッションが使う ``postgresql+asyncpg://.../<db_name>``。"""
    pgvector: bool
    """埋め込みテーブル(vector 拡張)が実体化しているか。False = stamp 経路。"""


_STATE: TestDbState | None = None


def worker_id() -> str:
    """pytest-xdist の worker id(``gw0`` 等)。単一プロセス実行では ``master``。"""
    raw = os.environ.get("PYTEST_XDIST_WORKER", "master")
    sanitized = _WORKER_RE.sub("_", raw)
    return sanitized or "master"


def _base_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _asyncpg_dsn(async_url: str, database: str) -> str:
    """asyncpg.connect 用の同期 DSN(``+asyncpg`` を外し database を差し替える)。"""
    url = make_url(async_url).set(database=database)
    user = url.username or ""
    password = f":{url.password}" if url.password else ""
    auth = f"{user}{password}@" if user else ""
    host = url.host or "localhost"
    port = f":{url.port}" if url.port else ""
    return f"postgresql://{auth}{host}{port}/{database}"


async def _admin_recreate(async_url: str, db_name: str) -> None:
    conn = await asyncpg.connect(_asyncpg_dsn(async_url, _MAINTENANCE_DB))
    try:
        # WITH (FORCE): 残存接続を切断してから作り直す(PG13+)。
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


async def _admin_drop(async_url: str, db_name: str) -> None:
    conn = await asyncpg.connect(_asyncpg_dsn(async_url, _MAINTENANCE_DB))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _vector_available(async_url: str, db_name: str) -> bool:
    conn = await asyncpg.connect(_asyncpg_dsn(async_url, db_name))
    try:
        row = await conn.fetchval(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
        )
        return bool(row)
    finally:
        await conn.close()


@lru_cache(maxsize=1)
def _apps_api_dir() -> Path:
    """``apps/api``(alembic.ini / alembic/ を含む)を上方向に探索して返す。"""
    override = os.environ.get("ALINEA_APPS_API_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for directory in here.parents:
        candidate = directory / "apps" / "api" / "alembic.ini"
        if candidate.is_file():
            return candidate.parent
    raise RuntimeError("apps/api/alembic.ini が見つからない(ALINEA_APPS_API_DIR で指定可)")


def _alembic_config(async_url: str) -> Config:
    api_dir = _apps_api_dir()
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", async_url)
    # prepend_sys_path の分割方式を明示し、alembic の legacy 分割 DeprecationWarning を避ける。
    cfg.set_main_option("path_separator", "os")
    return cfg


def _clear_settings_caches() -> None:
    """DATABASE_URL 変更を settings キャッシュへ反映する(env → 型付き設定)。"""
    from alinea_core.settings import get_settings

    get_settings.cache_clear()
    try:  # api がインストールされていれば api settings も
        from alinea_api.settings import get_api_settings
    except ImportError:  # pragma: no cover - worker 単独実行時など api 不在
        return
    get_api_settings.cache_clear()


def _run_migrations(async_url: str, pgvector: bool) -> None:
    # env.py は get_settings().database_url を正とするため env を先に差し替える。
    os.environ["DATABASE_URL"] = async_url
    _clear_settings_caches()
    cfg = _alembic_config(async_url)
    if pgvector:
        command.upgrade(cfg, "head")
        return
    # pgvector 非同梱: 0015 まで適用 → 0016 を stamp(vector DDL は作らない)→ head。
    command.upgrade(cfg, _PGVECTOR_PARENT)
    command.stamp(cfg, _PGVECTOR_REVISION)
    command.upgrade(cfg, "head")


def setup_test_database() -> TestDbState:
    """worker 専用 DB を作成しマイグレーションを適用する(冪等・セッション単位)。"""
    global _STATE
    if _STATE is not None:
        return _STATE

    base = _base_url()
    url = make_url(base)
    base_db = url.database or "alinea"
    wid = worker_id()
    db_name = f"{base_db}_test_{wid}"

    asyncio.run(_admin_recreate(base, db_name))
    pgvector = asyncio.run(_vector_available(base, db_name))
    # render_as_string(hide_password=False): 既定の str(URL) はパスワードを *** に伏せるため。
    async_url = url.set(database=db_name).render_as_string(hide_password=False)

    _run_migrations(async_url, pgvector)

    _STATE = TestDbState(
        base_url=base,
        worker_id=wid,
        db_name=db_name,
        async_url=async_url,
        pgvector=pgvector,
    )
    return _STATE


def teardown_test_database() -> None:
    """worker 専用 DB を drop し、DATABASE_URL を復元する(冪等)。"""
    global _STATE
    if _STATE is None:
        return
    state = _STATE
    _STATE = None
    os.environ["DATABASE_URL"] = state.base_url
    _clear_settings_caches()
    asyncio.run(_admin_drop(state.base_url, state.db_name))


def database_url() -> str:
    """アクティブなテスト DB の async URL(未初期化なら env / 既定)。"""
    if _STATE is not None:
        return _STATE.async_url
    return _base_url()


def pgvector_enabled() -> bool:
    """埋め込みテーブル(vector 拡張)が実体化しているか。"""
    return bool(_STATE and _STATE.pgvector)


def state() -> TestDbState | None:
    return _STATE
