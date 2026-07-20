"""BYOK キーストア(plans/04 §11)— 互換アダプタ。

実装本体は Task 13 で共有層 :mod:`alinea_core.llm.runtime`(:class:`LLMKeyStore`)へ移設した
(apps 間 import を避け worker と共用するため)。ここは既存 import(``from
alinea_api.llm.key_store import DbKeyStore``)と ``DbKeyStore(session, ApiSettings)`` の
呼び出し規約を保つための薄いアダプタに縮小する:

- ``LLMKeyStore`` は ``LLMRuntimeConfig`` を取るが、API 呼び出しは ``ApiSettings`` を渡す。
  ここで ``ApiSettings`` → ``LLMRuntimeConfig`` に詰め替えてから基底に委譲する。
- ``settings`` 省略時は ``get_api_settings()`` を使う(従来どおり)。
"""

from __future__ import annotations

from alinea_core.llm.runtime import LLMKeyStore, LLMRuntimeConfig
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.settings import ApiSettings, get_api_settings


def config_from_settings(settings: ApiSettings) -> LLMRuntimeConfig:
    """``ApiSettings`` を共有層の ``LLMRuntimeConfig`` に詰め替える(運営キー・暗号化秘密・TTL)。"""
    return LLMRuntimeConfig(
        operator_api_keys=dict(settings.operator_api_keys),
        key_encryption_secret=settings.alinea_key_encryption_secret,
        route_cache_ttl_s=settings.alinea_llm_route_cache_ttl_s,
    )


class DbKeyStore(LLMKeyStore):
    """``ApiSettings`` を受ける互換ラッパ(実装は共有層 :class:`LLMKeyStore`)。"""

    def __init__(self, session: AsyncSession, settings: ApiSettings | None = None) -> None:
        super().__init__(session, config_from_settings(settings or get_api_settings()))


__all__ = ["DbKeyStore", "config_from_settings"]
