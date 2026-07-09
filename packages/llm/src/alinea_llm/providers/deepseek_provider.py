"""DeepSeek アダプタ(OpenAI 互換。plans/04 §6.4)。

対象モデル: deepseek-v4-flash / deepseek-v4-pro。旧 deepseek-chat / deepseek-reasoner は
2026-07-24 廃止のため使用しない(models.yaml にも登録しない)。structured は JSON モード
互換戦略(§12)。
"""

from __future__ import annotations

from typing import Any

from alinea_llm.providers._common import base_url_override
from alinea_llm.providers.openai_compat import OpenAICompatProvider
from alinea_llm.types import LLMRequest

_DEFAULT_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(OpenAICompatProvider):
    name = "deepseek"
    supports_native_json_schema = False  # 厳密 json_schema 非対応 → §12 の JSON モード互換戦略

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        url = base_url or base_url_override("deepseek", _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL
        super().__init__(api_key, base_url=url)

    def _extra_body(self, req: LLMRequest) -> dict[str, Any]:
        # v4 系は thinking をリクエスト body で切替(§6.7)
        enabled = req.effort in ("medium", "high")
        return {"extra_body": {"thinking": {"type": "enabled" if enabled else "disabled"}}}

    def _format_kwargs(self, req: LLMRequest) -> dict[str, Any]:
        if req.json_schema:
            return {"response_format": {"type": "json_object"}}  # スキーマはプロンプト側(§12)
        return {}
