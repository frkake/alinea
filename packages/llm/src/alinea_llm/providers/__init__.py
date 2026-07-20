"""プロバイダファクトリ(plans/04 §2)。

provider 名 → コンストラクタの表。api-key/base_url を受けてアダプタを生成する。
DeepSeek / xAI は OpenAI 互換 base_url を内部で既定設定する。
"""

from __future__ import annotations

from collections.abc import Callable

from alinea_llm.protocols import EmbeddingProvider, ImageProvider, LLMProvider
from alinea_llm.providers.anthropic_provider import AnthropicProvider
from alinea_llm.providers.deepseek_provider import DeepSeekProvider
from alinea_llm.providers.google_provider import GoogleProvider
from alinea_llm.providers.images.google_image import GoogleImageProvider
from alinea_llm.providers.images.openai_image import OpenAIImageProvider
from alinea_llm.providers.images.xai_image import XAIImageProvider
from alinea_llm.providers.openai_embeddings import OpenAIEmbeddingProvider
from alinea_llm.providers.openai_provider import OpenAIProvider
from alinea_llm.providers.xai_provider import XAIProvider

ProviderFactory = Callable[..., LLMProvider]
ImageProviderFactory = Callable[..., ImageProvider]
EmbeddingProviderFactory = Callable[..., EmbeddingProvider]

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

# 埋め込みプロバイダ(S12)。現状は OpenAI のみ(Google 実装は将来)。
EMBEDDING_PROVIDER_FACTORIES: dict[str, EmbeddingProviderFactory] = {
    "openai": OpenAIEmbeddingProvider,
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


def build_embedding_provider(
    provider: str, api_key: str, base_url: str | None = None
) -> EmbeddingProvider:
    try:
        factory = EMBEDDING_PROVIDER_FACTORIES[provider]
    except KeyError:
        raise ValueError(f"unknown embedding provider: {provider}") from None
    return factory(api_key, base_url)


__all__ = [
    "EMBEDDING_PROVIDER_FACTORIES",
    "IMAGE_PROVIDER_FACTORIES",
    "PROVIDER_FACTORIES",
    "AnthropicProvider",
    "DeepSeekProvider",
    "EmbeddingProviderFactory",
    "GoogleImageProvider",
    "GoogleProvider",
    "ImageProviderFactory",
    "OpenAIEmbeddingProvider",
    "OpenAIImageProvider",
    "OpenAIProvider",
    "ProviderFactory",
    "XAIImageProvider",
    "XAIProvider",
    "build_embedding_provider",
    "build_image_provider",
    "build_provider",
]
