"""ワーカー実行時ブートストラップのテスト(M0-12/17/18 統合残件)。

- build_router: 運営キーの有無・チェーン絞り込み・キー無し→None。
- build_fake_router / on_startup: YAKUDOKU_FAKE_LLM=1 で router が構築される。
- make_publish: /api/events(apps/api/services/events.py・routers/jobs.py)の購読形式に
  一致した封筒が実 Redis に往復で届く。

DB は実 PostgreSQL(シード済み llm ルート)、Redis は実サービス(往復検証)。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from yakudoku_core.db.models import LibraryItem, Paper, User
from yakudoku_llm.router import LLMRouter
from yakudoku_llm.testing.fake_provider import FakeLLMProvider
from yakudoku_worker.bootstrap import (
    build_fake_router,
    build_router,
    channel_key,
    make_publish,
    on_shutdown,
    on_startup,
    operator_keys_from_env,
    stream_key,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://yakudoku:yakudoku@localhost:5432/yakudoku",
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL, poolclass=None)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[redis.Redis]:
    client: redis.Redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


async def _seed_library_item(maker: async_sessionmaker[AsyncSession]) -> tuple[str, str]:
    """User + Paper + LibraryItem を作成し commit して (user_id, library_item_id) を返す。"""
    async with maker() as session:
        user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
        session.add(user)
        await session.flush()
        paper = Paper(
            id=str(uuid.uuid4()),
            arxiv_id=f"2101.{uuid.uuid4().int % 100000:05d}",
            title="Bootstrap Test Paper",
            visibility="private",
            owner_user_id=user.id,
        )
        session.add(paper)
        await session.flush()
        item = LibraryItem(
            id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="planned"
        )
        session.add(item)
        await session.commit()
        return user.id, item.id


# =========================================================================== #
# build_router
# =========================================================================== #


async def test_build_router_returns_none_without_operator_keys(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as session:
        router = await build_router(session, operator_keys={})
    assert router is None


async def test_build_router_filters_chain_to_providers_with_keys(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    calls: list[tuple[str, str]] = []

    def factory(provider: str, key: str) -> Any:
        calls.append((provider, key))
        return FakeLLMProvider(name=provider)

    # translation チェーン = [deepseek-v4-flash, gemini-3.5-flash, gpt-5.4-mini]。
    # deepseek だけキーあり → 1 エントリだけ構築される。
    async with maker() as session:
        router = await build_router(
            session,
            task="translation",
            operator_keys={"deepseek": "sk-deepseek"},
            provider_factory=factory,
        )
    assert isinstance(router, LLMRouter)
    assert calls == [("deepseek", "sk-deepseek")]


async def test_build_router_returns_none_when_key_provider_not_in_chain(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    calls: list[tuple[str, str]] = []

    def factory(provider: str, key: str) -> Any:
        calls.append((provider, key))
        return FakeLLMProvider(name=provider)

    # anthropic は translation チェーンに居ない → 有効チェーンが空 → None。
    async with maker() as session:
        router = await build_router(
            session,
            task="translation",
            operator_keys={"anthropic": "sk-anthropic"},
            provider_factory=factory,
        )
    assert router is None
    assert calls == []


def test_build_fake_router_is_usable() -> None:
    router = build_fake_router()
    assert isinstance(router, LLMRouter)


def test_operator_keys_from_env_reads_configured_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.setenv("OPENAI_API_KEY", "")  # 空は除外
    keys = operator_keys_from_env()
    assert keys == {"deepseek": "sk-deepseek"}


# =========================================================================== #
# make_publish — 実 Redis で往復し、jobs.py の購読形式に一致することを検証
# =========================================================================== #


async def test_publish_round_trips_in_events_channel_format(
    maker: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    user_id, library_item_id = await _seed_library_item(maker)
    publish = make_publish(maker, redis_client)

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_key(user_id))
    try:
        data = {
            "type": "translation.unit_completed",
            "library_item_id": library_item_id,
            "translation_set_id": str(uuid.uuid4()),
            "block_ids": ["blk-0001", "blk-0002"],
            "total_progress": 42,
        }
        await publish(data)

        received: str | None = None
        for _ in range(50):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if message is not None:
                received = message["data"]
                break
        assert received is not None, "no pub/sub message received"
    finally:
        await pubsub.unsubscribe(channel_key(user_id))
        await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py pubsub untyped

    # jobs.py._parse_envelope が読める封筒形式(dict + 'data' キー)であること。
    envelope = json.loads(received)
    assert isinstance(envelope, dict)
    assert "data" in envelope
    assert "id" in envelope
    assert envelope["event"] == "translation.unit_completed"
    assert envelope["data"]["library_item_id"] == library_item_id
    assert envelope["data"]["total_progress"] == 42

    # Last-Event-ID 再送用 Stream にも同形式で積まれていること。
    entries = await redis_client.xrange(stream_key(user_id))
    assert entries
    _entry_id, fields = entries[-1]
    assert fields["event"] == "translation.unit_completed"
    stream_data = json.loads(fields["data"])
    assert stream_data["library_item_id"] == library_item_id

    await redis_client.delete(stream_key(user_id))


async def test_publish_is_noop_without_resolvable_target(
    maker: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    publish = make_publish(maker, redis_client)
    # user_id も library_item_id も無い → 宛先解決不能 → 例外を投げずスキップ。
    await publish({"type": "translation.unit_completed", "total_progress": 10})


async def test_publish_resolves_user_from_explicit_user_id(
    maker: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    user_id = str(uuid.uuid4())
    publish = make_publish(maker, redis_client)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_key(user_id))
    try:
        await publish({"type": "job.progress", "user_id": user_id, "progress_pct": 5})
        received: str | None = None
        for _ in range(50):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if message is not None:
                received = message["data"]
                break
        assert received is not None
    finally:
        await pubsub.unsubscribe(channel_key(user_id))
        await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py pubsub untyped
    envelope = json.loads(received)
    assert envelope["event"] == "job.progress"
    assert envelope["data"]["user_id"] == user_id
    await redis_client.delete(stream_key(user_id))


# =========================================================================== #
# on_startup / on_shutdown — 実部品で ctx を構成できる(FakeLLM で router 構築)
# =========================================================================== #


async def test_on_startup_configures_ctx_with_fake_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YAKUDOKU_FAKE_LLM", "1")
    ctx: dict[str, Any] = {}
    await on_startup(ctx)
    try:
        assert isinstance(ctx["router"], LLMRouter)
        assert ctx["sessionmaker"] is not None
        assert ctx["redis"] is not None
        assert ctx["arq_pool"] is not None
        assert ctx["s3"] is not None
        assert ctx["arxiv_http"] is not None
        assert callable(ctx["publish"])
        assert ctx["settings"] is not None
    finally:
        await on_shutdown(ctx)


# =========================================================================== #
# run_job — router 未構成なら翻訳ジョブは P3 準拠で可視的に失敗する
# =========================================================================== #


async def test_run_job_fails_visibly_when_router_missing() -> None:
    from yakudoku_core.db.session import get_sessionmaker
    from yakudoku_core.jobs.store import JobStore
    from yakudoku_worker.main import run_job

    maker = get_sessionmaker()
    async with maker() as session:
        store = JobStore(session)
        job_id = await store.enqueue(
            kind="translation",
            payload={"reason": "initial", "set_id": str(uuid.uuid4()), "section_id": "S1"},
            priority="bulk",
        )

    # ctx に router を入れない(= 運営キー未設定相当)。
    await run_job({}, job_id)

    async with maker() as session:
        store = JobStore(session)
        job = await store.get(job_id)
        assert job is not None
        # 黙って成功にせず、失敗(retry で queued へ戻る or failed)にする。
        assert job.status in ("queued", "failed")
        messages = [e.get("message", "") for e in job.log if isinstance(e, dict)]
        assert any("API キーが未設定" in m for m in messages)
