"""エラー分類(plans/04 §4)。

retryable(同一モデルで再試行)/ fallback(次モデルへ)/ fatal(チェーン中断)の
3 区分を ErrorKind で表し、frozenset で判定する。
"""

from __future__ import annotations

from enum import StrEnum


class ErrorKind(StrEnum):
    # --- retryable(同一モデルで指数バックオフ再試行) ---
    RATE_LIMIT = "rate_limit"  # 429
    OVERLOADED = "overloaded"  # Anthropic 529 / OpenAI 503 等
    SERVER = "server_error"  # 500/502/504
    TIMEOUT = "timeout"  # クライアント側タイムアウト
    CONNECTION = "connection"  # 接続失敗・切断
    # --- fallback(同一モデルで再試行せず次のモデルへ) ---
    CONTENT_FILTER = "content_filter"  # 安全フィルタで出力拒否/打ち切り
    SCHEMA_VALIDATION = "schema_validation"  # structured output 検証失敗(§12 の再試行後)
    MODEL_NOT_FOUND = "model_not_found"  # 404 モデル廃止・ID 誤り
    BILLING = "billing"  # 残高不足・課金無効(そのキーでは回復不能)
    # --- fatal(チェーン全体を中断し呼び出し元へ) ---
    AUTH = "auth"  # 401/403 キー無効(BYOK は §11.4 の特例)
    INVALID_REQUEST = "invalid_request"  # 400 リクエスト不正(実装バグ。再試行無意味)
    CONTEXT_LENGTH = "context_length"  # 入力超過(呼び出し元が文脈を縮める)


RETRYABLE: frozenset[ErrorKind] = frozenset(
    {
        ErrorKind.RATE_LIMIT,
        ErrorKind.OVERLOADED,
        ErrorKind.SERVER,
        ErrorKind.TIMEOUT,
        ErrorKind.CONNECTION,
    }
)
FALLBACK_ELIGIBLE: frozenset[ErrorKind] = RETRYABLE | frozenset(
    {
        ErrorKind.CONTENT_FILTER,
        ErrorKind.SCHEMA_VALIDATION,
        ErrorKind.MODEL_NOT_FOUND,
        ErrorKind.BILLING,
    }
)


class ProviderError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        provider: str,
        model: str,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"[{provider}/{model}] {kind}: {message}")
        self.kind = kind
        self.provider = provider
        self.model = model
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after_s = retry_after_s  # 429 の Retry-After(あれば)

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    @property
    def fallback_eligible(self) -> bool:
        return self.kind in FALLBACK_ELIGIBLE


class ProviderChainExhausted(Exception):  # noqa: N818 (plans/04 §4 の逐語名)
    """チェーン内の全モデルが失敗。ジョブ層のリトライ(docs/09 §2)へ委譲する。"""

    def __init__(self, task: str, errors: list[ProviderError]) -> None:
        detail = ""
        if errors:
            last = errors[-1]
            detail = f"; last_error={last.provider}/{last.model} {last.kind}"
        super().__init__(f"all providers failed for task={task}{detail}")
        self.task = task
        self.errors = errors


class SchemaValidationFailed(Exception):  # noqa: N818 (plans/04 §12 の逐語名)
    """structured output の JSON Schema / Pydantic 検証に失敗(§12)。"""
