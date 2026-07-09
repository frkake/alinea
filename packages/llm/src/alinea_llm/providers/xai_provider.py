"""xAI アダプタ(OpenAI 互換。plans/04 §6.5)。

対象モデル: grok-4.3。structured outputs 対応。grok-4 系は reasoning_effort 非対応のため
effort パラメータは送らない。v1 の既定テキストルーティングには含めない(画像+BYOK 検証用)。
"""

from __future__ import annotations

from typing import Any

from alinea_llm.providers._common import base_url_override
from alinea_llm.providers.openai_compat import OpenAICompatProvider
from alinea_llm.types import LLMRequest

_DEFAULT_BASE_URL = "https://api.x.ai/v1"


class XAIProvider(OpenAICompatProvider):
    name = "xai"
    supports_native_json_schema = True  # structured outputs 対応(公式 docs)

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        url = base_url or base_url_override("xai", _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL
        super().__init__(api_key, base_url=url)

    def _format_kwargs(self, req: LLMRequest) -> dict[str, Any]:
        if req.json_schema:
            return {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": req.json_schema.name,
                        "schema": req.json_schema.json_schema,
                        "strict": req.json_schema.strict,
                    },
                }
            }
        return {}
