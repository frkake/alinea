"""Alinea — LLM/画像プロバイダ抽象化層(plans/04)。

公開 API の再エクスポート。プロバイダ差異はこの層で完全に吸収し、呼び出し側は
タスク名と正規化リクエストのみを扱う。
"""

from __future__ import annotations

from alinea_llm.errors import (
    FALLBACK_ELIGIBLE,
    RETRYABLE,
    ErrorKind,
    ProviderChainExhausted,
    ProviderError,
    SchemaValidationFailed,
)
from alinea_llm.protocols import (
    ImageProvider,
    KeyStore,
    LLMProvider,
    MeterHook,
    ResolvedKey,
    UsageDraft,
)
from alinea_llm.registry import Capabilities, ModelInfo, ModelRegistry, TextPricing
from alinea_llm.router import ImageRouter, LLMRouter, RetryConfig
from alinea_llm.routing import TASKS, RouteResolver, RoutingConfig, TaskRoute
from alinea_llm.types import (
    ContentPart,
    Effort,
    ImageRequest,
    ImageResult,
    JsonSchemaSpec,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    StopReason,
    StreamEvent,
    Usage,
)

__all__ = [
    "FALLBACK_ELIGIBLE",
    "RETRYABLE",
    "TASKS",
    "Capabilities",
    "ContentPart",
    "Effort",
    "ErrorKind",
    "ImageProvider",
    "ImageRequest",
    "ImageResult",
    "ImageRouter",
    "JsonSchemaSpec",
    "KeyStore",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMRouter",
    "Message",
    "MeterHook",
    "ModelInfo",
    "ModelRegistry",
    "ProviderChainExhausted",
    "ProviderError",
    "ResolvedKey",
    "RetryConfig",
    "Role",
    "RouteResolver",
    "RoutingConfig",
    "SchemaValidationFailed",
    "StopReason",
    "StreamEvent",
    "TaskRoute",
    "TextPricing",
    "Usage",
    "UsageDraft",
]
