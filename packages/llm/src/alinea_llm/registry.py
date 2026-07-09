"""モデルレジストリ(plans/04 §7)。

役割はシード。実行時の正は DB(llm_models)。価格計算は §7.2 の確定式。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from alinea_llm.types import Usage


class TextPricing(BaseModel):
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None


class Capabilities(BaseModel):
    streaming: bool = False
    structured_native: bool = False
    vision: bool = False
    prompt_cache: Literal["implicit", "explicit", "none"] = "none"
    effort: bool = False


class ProviderInfo(BaseModel):
    sdk: str
    base_url: str | None = None
    env_key: str
    modalities: list[str]


class ModelInfo(BaseModel):
    id: str
    provider: str
    modality: Literal["text", "image"] = "text"
    display_name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    pricing: TextPricing | None = None
    pricing_per_image: dict[str, float] | None = None
    capabilities: Capabilities | None = None
    enabled: bool = True


class ModelRegistry:
    def __init__(
        self,
        models: Iterable[ModelInfo],
        providers: dict[str, ProviderInfo] | None = None,
        version: str = "",
    ) -> None:
        self._models: dict[str, ModelInfo] = {m.id: m for m in models}
        self.providers: dict[str, ProviderInfo] = providers or {}
        self.version = version

    @classmethod
    def from_yaml(cls, path: str | Path) -> ModelRegistry:
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        providers = {
            name: ProviderInfo(**spec) for name, spec in (data.get("providers") or {}).items()
        }
        models: list[ModelInfo] = []
        for raw in data.get("text_models") or []:
            models.append(ModelInfo(modality="text", **raw))
        for raw in data.get("image_models") or []:
            models.append(ModelInfo(modality="image", **raw))
        return cls(models, providers=providers, version=str(data.get("version", "")))

    def get(self, model_id: str) -> ModelInfo:
        return self._models[model_id]  # 不明 ID は KeyError

    def all_models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def text_cost_usd(self, model_id: str, usage: Usage) -> float:
        p = self.get(model_id).pricing
        if p is None:
            return 0.0
        cached_rate = (
            p.cached_input_per_mtok if p.cached_input_per_mtok is not None else p.input_per_mtok
        )
        write_rate = (
            p.cache_write_per_mtok if p.cache_write_per_mtok is not None else p.input_per_mtok
        )
        usd = (
            usage.input_tokens * p.input_per_mtok
            + usage.cached_input_tokens * cached_rate
            + usage.cache_write_input_tokens * write_rate
            + usage.output_tokens * p.output_per_mtok
        ) / 1_000_000
        return round(usd, 8)

    def image_cost_usd(self, model_id: str, quality: str) -> float:
        prices = self.get(model_id).pricing_per_image
        if not prices:
            return 0.0
        return prices.get(quality, next(iter(prices.values())))
