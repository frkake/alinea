"""訳読 / YAKUDOKU — LLM/画像プロバイダ抽象化層(plans/04)。

公開 API の再エクスポート。プロバイダ差異はこの層で完全に吸収し、呼び出し側は
タスク名と正規化リクエストのみを扱う。
"""

from __future__ import annotations

from yakudoku_llm.errors import (
    FALLBACK_ELIGIBLE,
    RETRYABLE,
    ErrorKind,
    ProviderChainExhausted,
    ProviderError,
    SchemaValidationFailed,
)
from yakudoku_llm.protocols import (
    ImageProvider,
    KeyStore,
    LLMProvider,
    MeterHook,
    ResolvedKey,
    UsageDraft,
)
from yakudoku_llm.registry import Capabilities, ModelInfo, ModelRegistry, TextPricing
from yakudoku_llm.router import ImageRouter, LLMRouter, RetryConfig
from yakudoku_llm.routing import TASKS, RouteResolver, RoutingConfig, TaskRoute
from yakudoku_llm.types import (
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
