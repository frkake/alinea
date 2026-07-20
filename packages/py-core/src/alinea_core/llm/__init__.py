"""共有 LLM ランタイム層(apps/api・apps/worker が共用)。

DB ルート解決(:class:`LLMRouteStore`)・BYOK キー解決(:class:`LLMKeyStore`)・
使用量計測(:class:`LLMMeterHook`)・ユーザー文脈のルータ構築(:func:`build_user_router`)を
提供する。抽象化層本体は packages/llm(ドメイン DB 非依存)。
"""

from __future__ import annotations

from alinea_core.llm.runtime import (
    ChainEntry,
    LLMKeyStore,
    LLMMeterHook,
    LLMRouteStore,
    LLMRuntimeConfig,
    ProviderFactory,
    build_user_router,
    default_registry,
    route_cache_key,
)

__all__ = [
    "ChainEntry",
    "LLMKeyStore",
    "LLMMeterHook",
    "LLMRouteStore",
    "LLMRuntimeConfig",
    "ProviderFactory",
    "build_user_router",
    "default_registry",
    "route_cache_key",
]
