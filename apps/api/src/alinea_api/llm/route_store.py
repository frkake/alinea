"""DB ベースのタスクルート解決(plans/04 §15)— 互換 re-export。

実装は Task 13 で共有層 :mod:`alinea_core.llm.runtime` へ移設した(apps 間 import を避け
worker と共用するため)。ここは既存 import(``from alinea_api.llm.route_store import
DbRouteStore`` など)を壊さないための薄い re-export に縮小する。``DbRouteStore`` は
共有層 ``LLMRouteStore`` の別名。
"""

from __future__ import annotations

from alinea_core.llm.runtime import ChainEntry, LLMRouteStore

# 旧名 → 共有層の実クラス。API 側の呼び出し互換のため維持する。
DbRouteStore = LLMRouteStore

__all__ = ["ChainEntry", "DbRouteStore"]
