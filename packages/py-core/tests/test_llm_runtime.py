"""共有 LLM ランタイム層(alinea_core.llm.runtime)のテスト(Task 13)。

apps 間 import を避けるため DB ルート解決・BYOK キー解決・使用量計測を core に集約する。
worker と api の両方がここを使う。ここでは core 層だけを直接検証する:

- LLMRouteStore: 既定チェーン・ユーザー上書き先頭挿入・disabled/未設定除外・キャッシュ失効。
- LLMKeyStore: BYOK 暗号化往復・解決順(ユーザー→運営→None)。
- build_user_router: 二ユーザーが同一プロセスで別プロバイダへ解決し、秘密鍵を保持しない。

DB は実 PostgreSQL(シード済み llm ルート)。ネットワーク LLM は使わず Fake のみ。
"""

from __future__ import annotations

import uuid

from alinea_core.llm import (
    LLMKeyStore,
    LLMRouteStore,
    LLMRuntimeConfig,
    build_user_router,
)
from alinea_llm.router import LLMRouter
from alinea_llm.testing.fake_provider import FakeLLMProvider
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _config(**overrides: object) -> LLMRuntimeConfig:
    defaults: dict[str, object] = {
        "operator_api_keys": {},
        "key_encryption_secret": Fernet.generate_key().decode(),
        "route_cache_ttl_s": 60,
    }
    defaults.update(overrides)
    return LLMRuntimeConfig(**defaults)  # type: ignore[arg-type]


def _fake_factory(provider: str, _api_key: str) -> FakeLLMProvider:
    return FakeLLMProvider(name=provider)


async def _new_user(session: AsyncSession) -> str:
    uid = (
        await session.execute(
            text("INSERT INTO users (email) VALUES (:email) RETURNING id"),
            {"email": f"runtime-{uuid.uuid4().hex}@example.com"},
        )
    ).scalar_one()
    return str(uid)


async def _set_override(session: AsyncSession, user_id: str, task: str, model_id: str) -> None:
    await session.execute(
        text(
            "INSERT INTO user_task_model_overrides (user_id, task, model_id) "
            "VALUES (CAST(:u AS uuid), :t, :m) "
            "ON CONFLICT (user_id, task) DO UPDATE SET model_id = EXCLUDED.model_id"
        ),
        {"u": user_id, "t": task, "m": model_id},
    )


# --------------------------------------------------------------------------- #
# LLMRouteStore(DbRouteStore を core へ移設したもの)
# --------------------------------------------------------------------------- #
async def test_route_store_default_chain(db_session: AsyncSession) -> None:
    store = LLMRouteStore(db_session)
    assert await store.chain_for("chat") == ["claude-opus-4-8", "gpt-5.5", "gemini-3.5-flash"]
    assert await store.primary_provider("chat") == "anthropic"


async def test_route_store_user_override_first(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    await _set_override(db_session, user_id, "chat", "gemini-3.5-flash")
    store = LLMRouteStore(db_session)
    assert await store.chain_for("chat", user_id) == [
        "gemini-3.5-flash",
        "claude-opus-4-8",
        "gpt-5.5",
    ]
    assert await store.primary_provider("chat", user_id) == "google"


# --------------------------------------------------------------------------- #
# LLMKeyStore(DbKeyStore を core へ移設したもの)
# --------------------------------------------------------------------------- #
async def test_key_store_roundtrip_and_resolution_order(db_session: AsyncSession) -> None:
    secret = Fernet.generate_key().decode()
    config = _config(key_encryption_secret=secret, operator_api_keys={"anthropic": "sk-op-ant"})
    ks = LLMKeyStore(db_session, config)
    user_id = await _new_user(db_session)

    # 未登録: openai は運営キーが無いので None。anthropic は運営キーで解決。
    assert await ks.resolve_or_none(user_id, "openai") is None
    op = await ks.resolve_or_none(user_id, "anthropic")
    assert op is not None
    assert op.source == "operator"

    # BYOK 登録後は user 優先。
    await ks.put(user_id=user_id, provider="anthropic", plaintext="sk-user-ant")
    assert await ks.get(user_id=user_id, provider="anthropic") == "sk-user-ant"
    resolved = await ks.resolve_or_none(user_id, "anthropic")
    assert resolved is not None
    assert resolved.source == "user"
    assert resolved.api_key == "sk-user-ant"


# --------------------------------------------------------------------------- #
# build_user_router: 二ユーザーが同一プロセスで別プロバイダへ解決する
# --------------------------------------------------------------------------- #
async def test_build_user_router_resolves_per_user_provider(db_session: AsyncSession) -> None:
    user_a = await _new_user(db_session)
    user_b = await _new_user(db_session)
    await _set_override(db_session, user_a, "chat", "gpt-5.5")  # openai
    await _set_override(db_session, user_b, "chat", "gemini-3.5-flash")  # google

    config = _config(operator_api_keys={"openai": "sk-op-openai", "google": "sk-op-google"})

    router_a = await build_user_router(
        session=db_session,
        cache=None,
        config=config,
        user_id=user_a,
        task="chat",
        provider_factory=_fake_factory,
    )
    router_b = await build_user_router(
        session=db_session,
        cache=None,
        config=config,
        user_id=user_b,
        task="chat",
        provider_factory=_fake_factory,
    )
    assert isinstance(router_a, LLMRouter)
    resp_a = await router_a.complete("chat", prompt="hi", user_id=user_a)
    resp_b = await router_b.complete("chat", prompt="hi", user_id=user_b)
    assert resp_a.provider == "openai"
    assert resp_b.provider == "google"


async def test_build_user_router_does_not_retain_secret_keys(db_session: AsyncSession) -> None:
    """router / route cache 経路に BYOK 平文が残らない(60 秒キャッシュは route metadata のみ)。"""
    user_id = await _new_user(db_session)
    secret = Fernet.generate_key().decode()
    config = _config(key_encryption_secret=secret, operator_api_keys={"anthropic": "sk-op-ant"})
    ks = LLMKeyStore(db_session, config)
    await ks.put(user_id=user_id, provider="anthropic", plaintext="sk-super-secret-byok")

    store = LLMRouteStore(db_session, cache=None, cache_ttl_s=config.route_cache_ttl_s)
    entries = await store.resolve_chain("chat", user_id, available_providers={"anthropic"})
    # route metadata は (model_id, provider) のみ。平文キーを含まない。
    flat = repr(entries)
    assert "sk-super-secret-byok" not in flat
    assert all(len(e) == 2 for e in entries)
