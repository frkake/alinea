"""プロバイダファクトリ(plans/04 §2)。

provider 名 → コンストラクタの表。api-key/base_url を受けてアダプタを生成する。
DeepSeek / xAI は OpenAI 互換 base_url を内部で既定設定する。
"""

from __future__ import annotations

from collections.abc import Callable

from yakudoku_llm.protocols import ImageProvider, LLMProvider
from yakudoku_llm.providers.anthropic_provider import AnthropicProvider
from yakudoku_llm.providers.deepseek_provider import DeepSeekProvider
from yakudoku_llm.providers.google_provider import GoogleProvider
from yakudoku_llm.providers.images.google_image import GoogleImageProvider
from yakudoku_llm.providers.images.openai_image import OpenAIImageProvider
from yakudoku_llm.providers.images.xai_image import XAIImageProvider
from yakudoku_llm.providers.openai_provider import OpenAIProvider
from yakudoku_llm.providers.xai_provider import XAIProvider

ProviderFactory = Callable[..., LLMProvider]
ImageProviderFactory = Callable[..., ImageProvider]

PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "deepseek": DeepSeekProvider,
    "xai": XAIProvider,
}

IMAGE_PROVIDER_FACTORIES: dict[str, ImageProviderFactory] = {
    "openai": OpenAIImageProvider,
    "google": GoogleImageProvider,
    "xai": XAIImageProvider,
}


def build_provider(provider: str, api_key: str, base_url: str | None = None) -> LLMProvider:
    try:
        factory = PROVIDER_FACTORIES[provider]
    except KeyError:
        raise ValueError(f"unknown text provider: {provider}") from None
    return factory(api_key, base_url)


def build_image_provider(provider: str, api_key: str, base_url: str | None = None) -> ImageProvider:
    try:
        factory = IMAGE_PROVIDER_FACTORIES[provider]
    except KeyError:
        raise ValueError(f"unknown image provider: {provider}") from None
    return factory(api_key, base_url)


__all__ = [
    "IMAGE_PROVIDER_FACTORIES",
    "PROVIDER_FACTORIES",
    "AnthropicProvider",
    "DeepSeekProvider",
    "GoogleImageProvider",
    "GoogleProvider",
    "ImageProviderFactory",
    "OpenAIImageProvider",
    "OpenAIProvider",
    "ProviderFactory",
    "XAIImageProvider",
    "XAIProvider",
    "build_image_provider",
    "build_provider",
]
