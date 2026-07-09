"""py-core テスト用フィクスチャ。実 PostgreSQL(docker-compose)に対して実行する。

SQLite 代替は PGroonga 依存のため禁止(plans/00 §4.5)。マイグレーションは
apps/api/alembic で適用済みの前提(CI・ローカルとも upgrade head 済み)。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://alinea:alinea@localhost:5432/alinea",
)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(DATABASE_URL, poolclass=None)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()
