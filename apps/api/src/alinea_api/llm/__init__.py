"""API 層の LLM 統合(plans/04 §9〜§15、plans/07 §9)。

DB ベースのルート解決(DbRouteStore)・BYOK キーストア(DbKeyStore)・使用量計測
(DbMeterHook)・ユーザー文脈のルータ構築(build_router_for_user)とクォータ判定(check_quota)を
提供する。抽象化層本体は packages/llm(ドメイン DB 非依存)。

Task 13 以降、DB ルート解決 / BYOK 解決 / 計測 / ルータ構築の実装本体は共有層
``alinea_core.llm.runtime`` へ移設した(worker と共用)。``DbRouteStore`` / ``DbMeterHook`` は
それぞれ ``LLMRouteStore`` / ``LLMMeterHook`` の別名、``DbKeyStore`` は ``ApiSettings`` を受ける
薄いアダプタ、``build_router_for_user`` は ``build_user_router`` への委譲ラッパ。クォータ判定
(429 送出)は API 固有なので ``deps`` に残す。
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
