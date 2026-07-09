"""API 層の LLM 統合(plans/04 §9〜§15、plans/07 §9)。

DB ベースのルート解決(DbRouteStore)・BYOK キーストア(DbKeyStore)・使用量計測
(DbMeterHook)・ユーザー文脈のルータ構築とクォータ判定(deps)を提供する。
抽象化層本体は packages/llm(ドメイン DB 非依存)。
"""

from __future__ import annotations

from alinea_api.llm.deps import (
    ProviderFactory,
    build_router_for_user,
    check_quota,
    quota_usage,
)
from alinea_api.llm.key_store import DbKeyStore
from alinea_api.llm.meter import DbMeterHook
from alinea_api.llm.route_store import ChainEntry, DbRouteStore

__all__ = [
    "ChainEntry",
    "DbKeyStore",
    "DbMeterHook",
    "DbRouteStore",
    "ProviderFactory",
    "build_router_for_user",
    "check_quota",
    "quota_usage",
]
