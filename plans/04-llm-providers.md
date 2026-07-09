# 04. LLM / 画像生成プロバイダ抽象化層 実装計画

> 対象読者と前提: 本書は apps/api(FastAPI)・apps/worker(arq)を実装するバックエンドエンジニア向け。docs/03(翻訳)・docs/05(チャット)・docs/07(概要図・記事)・docs/09 §3(コスト設計)・docs/11 §8(語彙生成)が要求する「用途別ルーティング+フォールバック+BYOK+計測」を満たす Python 抽象化層の完全設計である。モデル ID・価格は scratchpad/extract/_models.md(2026-07-06 調査)を確定値として採用した。技術スタックは spec-decisions C項(Python 3.12 / FastAPI / SQLAlchemy 2 / Pydantic v2 / arq / PostgreSQL 16 / Redis 7)を前提とする。

## 1. 設計原則と責務境界

1. **プロバイダ差異はこの層で完全に吸収する**(docs/09 §3.2)。呼び出し側(翻訳ワーカー・チャット API・記事生成ワーカー等)はタスク名と正規化リクエストだけを渡し、モデル ID・SDK・エラー形式・ストリーミング形式・structured output の方言を一切知らない。
2. **モデル ID をコードに書かない**(docs/09 §3.2)。モデルは `models.yaml`(シード)→ DB テーブル `llm_models` / `llm_task_routes`(実行時の正)で管理し、再デプロイなしで変更できる。
3. **黙って品質を落とさない**(P3)。フォールバック発生は `usage_records.fallback_rank` と処理ログ(2a「処理ログ」)に記録し、ユーザーが使用モデルを判別できる。
4. **DB 非依存のコアパッケージ**。抽象化層本体はドメイン DB を知らず、キー解決・計測記録は小さな Protocol(`KeyStore` / `MeterHook`)で apps/api / apps/worker から注入する。単体テストは Fake 実装で完結する。
5. すべて **async**(FastAPI / arq と同一イベントループで動く)。同期 API は提供しない。

## 2. パッケージ配置とファイル構成

決定: 抽象化層は共有 Python パッケージ **`packages/llm`(配布名 `alinea-llm`、import 名 `alinea_llm`)** に置き、`apps/api` と `apps/worker` の `pyproject.toml` から path 依存(`alinea-llm @ file://../../packages/llm`、uv workspace)で参照する。理由: 翻訳ジョブ(worker)とチャット(api)の両方が同一実装を使うため、どちらかのアプリ配下に置くと依存が逆転する。

```
packages/llm/
  pyproject.toml               # [project] name = "alinea-llm", requires-python = ">=3.12"
                               # 依存: pydantic>=2.7, openai>=2.0, anthropic>=0.60, google-genai>=1.20,
                               #       tiktoken>=0.9, pyyaml>=6.0, cryptography>=44.0, httpx>=0.27
  models.yaml                  # モデルレジストリのシード(§7)
  routing.yaml                 # タスクルーティングのシード(§8)
  src/alinea_llm/
    __init__.py                # 公開 API の再エクスポート
    types.py                   # 共通型(§3)
    errors.py                  # ProviderError と分類(§4)
    protocols.py               # LLMProvider / ImageProvider / KeyStore / MeterHook(§5)
    registry.py                # ModelRegistry: models.yaml ロード・能力フラグ・価格計算(§7)
    routing.py                 # RouteResolver: タスク→チェーン解決(§8)
    router.py                  # LLMRouter / ImageRouter: リトライ・フォールバック・計測(§9)
    structured.py              # structured output 互換戦略(§12)
    caching.py                 # プロンプトキャッシュヘルパ(§13)
    tokens.py                  # count_tokens のプロバイダ別実装補助(§14)
    providers/
      __init__.py              # PROVIDER_FACTORIES: dict[str, ProviderFactory]
      openai_provider.py       # OpenAIProvider(Responses API)
      anthropic_provider.py    # AnthropicProvider
      google_provider.py       # GoogleProvider(google-genai)
      openai_compat.py         # OpenAICompatProvider(DeepSeek / xAI 共通基底)
      deepseek_provider.py     # DeepSeekProvider
      xai_provider.py          # XAIProvider
      images/
        openai_image.py        # OpenAIImageProvider(gpt-image-2)
        google_image.py        # GoogleImageProvider(gemini-3.1-flash-image / gemini-3-pro-image)
        xai_image.py           # XAIImageProvider(grok-imagine-image / -quality)
    testing/
      fake_provider.py         # FakeLLMProvider / FakeImageProvider(pytest 用)
  tests/                       # 単体テスト(§17)
```

apps/api 側の関連ファイル(本計画で確定):

```
apps/api/app/llm/deps.py           # DI: LLMRouter 構築(DbKeyStore + DbMeterHook + Redis キャッシュ)
apps/api/app/llm/key_store.py      # DbKeyStore(byok_api_keys、Fernet 復号)
apps/api/app/llm/meter.py          # DbMeterHook(usage_records へ INSERT)
apps/api/app/llm/route_store.py    # DB 上のルーティング(llm_models / llm_task_routes / user_task_model_overrides)
apps/api/app/routers/llm_settings.py  # §11・§15 のエンドポイント
apps/api/app/models/llm.py         # SQLAlchemy モデル(§10・§11・§15 の DDL に対応)
```

## 3. 共通型定義(完全形)

`packages/llm/src/alinea_llm/types.py`:

```python
from __future__ import annotations

import base64
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ContentPart(BaseModel):
    """メッセージ本文の1パート。テキストまたは画像(図の説明 docs/05 §4)。"""
    model_config = ConfigDict(frozen=True)

    type: Literal["text", "image"] = "text"
    text: str | None = None
    image_b64: str | None = None            # base64(bytes は job ペイロードに載せない)
    image_media_type: str | None = None     # "image/png" | "image/jpeg" | "image/webp"
    cache_hint: bool = False                # True: このパートまでをプロンプトキャッシュ境界とする(§13)

    @classmethod
    def from_image_bytes(cls, data: bytes, media_type: str) -> "ContentPart":
        return cls(type="image", image_b64=base64.b64encode(data).decode(), image_media_type=media_type)


class Message(BaseModel):
    role: Role
    parts: list[ContentPart]


class JsonSchemaSpec(BaseModel):
    """generate_structured 用の JSON Schema 指定。"""
    name: str                                # 例 "overview_figure_dsl_v1"
    json_schema: dict[str, Any]              # JSON Schema(draft 2020-12)
    strict: bool = True


Effort = Literal["none", "low", "medium", "high"]


class LLMRequest(BaseModel):
    model: str                               # llm_models.id(例 "claude-opus-4-8")。Router が設定
    system: list[ContentPart] = Field(default_factory=list)
    messages: list[Message]
    max_output_tokens: int = 4096
    effort: Effort = "none"                  # プロバイダ別マッピングは §6.7 の表
    stop_sequences: list[str] = Field(default_factory=list)
    json_schema: JsonSchemaSpec | None = None  # generate_structured のときのみ必須
    prompt_cache_key: str | None = None      # OpenAI prompt_cache_key に渡す(§13)
    timeout_s: float = 120.0
    metadata: dict[str, str] = Field(default_factory=dict)  # {"task": ..., "trace_id": ...} ログ用
```

決定: **`temperature` / `top_p` / `top_k` フィールドは持たない**。理由: Anthropic 4.7 以降は送信すると 400、OpenAI gpt-5 系 reasoning モデルも非対応であり、本プロダクトの全タスクは effort とプロンプトで出力を制御する(docs/09 §3.3 の注意を型レベルで強制)。

```python
class Usage(BaseModel):
    """トークン計測の正規化形。input_tokens はキャッシュ分を含まない(§10.2)。"""
    input_tokens: int = 0                    # 非キャッシュ入力
    cached_input_tokens: int = 0             # キャッシュ読取入力(hit)
    cache_write_input_tokens: int = 0        # キャッシュ書込入力(Anthropic のみ非0)
    output_tokens: int = 0


StopReason = Literal["end", "max_tokens", "stop_sequence", "content_filter"]


class LLMResponse(BaseModel):
    text: str                                # 全文テキスト(structured の場合は JSON 文字列)
    parsed: dict[str, Any] | None = None     # generate_structured の検証済み JSON
    usage: Usage
    provider: str                            # "openai" | "anthropic" | "google" | "deepseek" | "xai"
    model: str
    stop_reason: StopReason
    request_id: str | None = None            # プロバイダ側リクエストID
    latency_ms: int = 0


class StreamEvent(BaseModel):
    """全プロバイダ共通のストリームイベント(§12 で SSE に橋渡し)。"""
    type: Literal["start", "text_delta", "usage", "end", "error"]
    delta: str | None = None                 # text_delta のみ
    usage: Usage | None = None               # usage / end
    response: LLMResponse | None = None      # end のみ(全文含む)
    error_kind: str | None = None            # error のみ(ErrorKind 値)
    error_message: str | None = None


ImageSize = Literal["1024x1024", "1536x1024", "1024x1536"]
ImageQuality = Literal["standard", "high"]


class ImageRequest(BaseModel):
    model: str
    prompt: str                              # 共通プリアンブル込みの最終プロンプト(docs/07 §1.3)
    size: ImageSize = "1536x1024"
    quality: ImageQuality = "standard"
    timeout_s: float = 120.0
    metadata: dict[str, str] = Field(default_factory=dict)


class ImageResult(BaseModel):
    image_bytes: bytes                       # PNG 正規化済み
    media_type: Literal["image/png"] = "image/png"
    provider: str
    model: str
    revised_prompt: str | None = None        # プロバイダが書き換えた場合の実効プロンプト
    cost_usd: float = 0.0                    # レジストリの per-image 価格から確定(§7.3)
    request_id: str | None = None
    latency_ms: int = 0
```

## 4. エラー分類(ProviderError)

`packages/llm/src/alinea_llm/errors.py`:

```python
from __future__ import annotations

from enum import StrEnum


class ErrorKind(StrEnum):
    # --- retryable(同一モデルで指数バックオフ再試行) ---
    RATE_LIMIT = "rate_limit"                # 429
    OVERLOADED = "overloaded"                # Anthropic 529 / OpenAI 503 等
    SERVER = "server_error"                  # 500/502/504
    TIMEOUT = "timeout"                      # クライアント側タイムアウト
    CONNECTION = "connection"                # 接続失敗・切断
    # --- fallback(同一モデルで再試行せず次のモデルへ) ---
    CONTENT_FILTER = "content_filter"        # 安全フィルタで出力拒否/打ち切り
    SCHEMA_VALIDATION = "schema_validation"  # structured output 検証失敗(§12 の再試行後)
    MODEL_NOT_FOUND = "model_not_found"      # 404 モデル廃止・ID 誤り
    BILLING = "billing"                      # 残高不足・課金無効(そのキーでは回復不能)
    # --- fatal(チェーン全体を中断し呼び出し元へ) ---
    AUTH = "auth"                            # 401/403 キー無効(BYOK は §11.4 の特例)
    INVALID_REQUEST = "invalid_request"      # 400 リクエスト不正(実装バグ。再試行無意味)
    CONTEXT_LENGTH = "context_length"        # 入力超過(呼び出し元が文脈を縮めて再構成する)


RETRYABLE: frozenset[ErrorKind] = frozenset({
    ErrorKind.RATE_LIMIT, ErrorKind.OVERLOADED, ErrorKind.SERVER,
    ErrorKind.TIMEOUT, ErrorKind.CONNECTION,
})
FALLBACK_ELIGIBLE: frozenset[ErrorKind] = RETRYABLE | frozenset({
    ErrorKind.CONTENT_FILTER, ErrorKind.SCHEMA_VALIDATION,
    ErrorKind.MODEL_NOT_FOUND, ErrorKind.BILLING,
})


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, provider: str, model: str, message: str,
                 *, status_code: int | None = None, request_id: str | None = None,
                 retry_after_s: float | None = None) -> None:
        super().__init__(f"[{provider}/{model}] {kind}: {message}")
        self.kind = kind
        self.provider = provider
        self.model = model
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after_s = retry_after_s   # 429 の Retry-After(あれば)

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    @property
    def fallback_eligible(self) -> bool:
        return self.kind in FALLBACK_ELIGIBLE


class ProviderChainExhausted(Exception):
    """チェーン内の全モデルが失敗。ジョブ層のリトライ(docs/09 §2)へ委譲する。"""
    def __init__(self, task: str, errors: list[ProviderError]) -> None:
        super().__init__(f"all providers failed for task={task}")
        self.task = task
        self.errors = errors
```

各社 SDK 例外 → `ErrorKind` のマッピング(各アダプタの `_classify()` で実装):

| プロバイダ | RATE_LIMIT | OVERLOADED | SERVER | AUTH | BILLING | CONTENT_FILTER | CONTEXT_LENGTH |
|---|---|---|---|---|---|---|---|
| OpenAI / DeepSeek / xAI(openai SDK) | `RateLimitError`(429) | 503 | `InternalServerError` | `AuthenticationError` `PermissionDeniedError` | 402、429 のうち `insufficient_quota` | `finish_reason == "content_filter"` / refusal | 400 のうち `context_length_exceeded` |
| Anthropic | `RateLimitError`(429) | 529 `overloaded_error` | `InternalServerError` | `AuthenticationError` | 400 `billing` 系 | `stop_reason == "refusal"` | 400 `prompt is too long` |
| Google(google-genai) | `ClientError` 429 `RESOURCE_EXHAUSTED` | — | `ServerError` 5xx | 401/403 | 429 のうちクォータ超過メッセージ | `finish_reason in (SAFETY, PROHIBITED_CONTENT)` | 400 `INVALID_ARGUMENT` のうちトークン超過 |

- `TIMEOUT` / `CONNECTION` は SDK の `APITimeoutError` / `APIConnectionError`(および google-genai の `httpx.TimeoutException` / `httpx.ConnectError`)から分類する。
- 分類不能な例外は `SERVER` に倒す(安全側=再試行される)。

## 5. Protocol 定義(完全形)

`packages/llm/src/alinea_llm/protocols.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from .types import ImageRequest, ImageResult, LLMRequest, LLMResponse, StreamEvent, Usage


@runtime_checkable
class LLMProvider(Protocol):
    """テキスト生成プロバイダ。実装: OpenAI / Anthropic / Google / DeepSeek / xAI。"""
    name: str  # "openai" | "anthropic" | "google" | "deepseek" | "xai"

    async def generate(self, req: LLMRequest) -> LLMResponse:
        """非ストリーミング1回生成。失敗は ProviderError を送出。"""
        ...

    def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        """正規化ストリーム(§12)。start → text_delta* → usage → end。エラーは error イベント。"""
        ...

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        """req.json_schema 必須。response.parsed に検証済み JSON を返す(§12 互換戦略)。"""
        ...

    async def count_tokens(self, req: LLMRequest) -> int:
        """入力トークン数の見積り(§14)。課金には使わない。"""
        ...


@runtime_checkable
class ImageProvider(Protocol):
    """ラスター画像生成プロバイダ。実装: OpenAI / Google / xAI(docs/07 §1.3)。"""
    name: str  # "openai" | "google" | "xai"

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        """1枚生成して PNG バイト列+メタを返す。失敗は ProviderError を送出。"""
        ...


class KeyStore(Protocol):
    """API キー解決。apps/api の DbKeyStore が実装(§11)。"""
    async def resolve(self, user_id: str | None, provider: str) -> "ResolvedKey": ...
    async def mark_invalid(self, user_id: str, provider: str) -> None: ...


class MeterHook(Protocol):
    """使用量計測。apps/api の DbMeterHook が実装(§10)。"""
    async def record(self, record: "UsageDraft") -> None: ...
```

`ResolvedKey` と `UsageDraft`(同ファイル内):

```python
from pydantic import BaseModel


class ResolvedKey(BaseModel):
    provider: str
    api_key: str
    source: str            # "user" | "operator"


class UsageDraft(BaseModel):
    user_id: str | None
    library_item_id: str | None = None
    job_id: str | None = None
    task: str
    provider: str
    model: str
    key_source: str        # "user" | "operator"
    usage: Usage | None = None
    image_count: int = 0
    cost_usd: float = 0.0
    status: str            # "ok" | "error"
    attempt: int = 1
    fallback_rank: int = 0 # 0=primary, 1=第1フォールバック, …
    error_kind: str | None = None
    latency_ms: int | None = None
    request_id: str | None = None
```

## 6. アダプタ実装(各社コードスケッチ)

共通方針: SDK クライアントは `(provider, sha256(api_key)[:16])` をキーに LRU(最大 32 エントリ)でプール。BYOK でユーザーごとにキーが変わっても接続を再利用する。

### 6.1 OpenAI(`openai` SDK・Responses API)

対象モデル: `gpt-5.5` / `gpt-5.4-mini`。画像は §6.6。Chat Completions は使わない(Responses API に統一。structured・ストリーミング・prompt_cache_key を1系統で扱うため)。

```python
# providers/openai_provider.py
import time
from collections.abc import AsyncIterator
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, APIStatusError

from ..errors import ErrorKind, ProviderError
from ..types import LLMRequest, LLMResponse, StreamEvent, Usage

_EFFORT = {"none": "minimal", "low": "low", "medium": "medium", "high": "high"}


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        # max_retries=0: リトライは Router(§9)が一元管理。SDK 内リトライと二重にしない

    def _kwargs(self, req: LLMRequest) -> dict:
        kw: dict = {
            "model": req.model,
            "instructions": "".join(p.text or "" for p in req.system),
            "input": _to_responses_input(req.messages),   # text/image パート→ input_text/input_image
            "max_output_tokens": req.max_output_tokens,
            "reasoning": {"effort": _EFFORT[req.effort]},
            "timeout": req.timeout_s,
        }
        if req.prompt_cache_key:
            kw["prompt_cache_key"] = req.prompt_cache_key
        if req.json_schema:
            kw["text"] = {"format": {
                "type": "json_schema",
                "name": req.json_schema.name,
                "schema": req.json_schema.json_schema,
                "strict": req.json_schema.strict,
            }}
        return kw

    async def generate(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        try:
            resp = await self._client.responses.create(**self._kwargs(req))
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            raise self._classify(e, req.model) from e
        cached = resp.usage.input_tokens_details.cached_tokens
        return LLMResponse(
            text=resp.output_text,
            usage=Usage(
                input_tokens=resp.usage.input_tokens - cached,
                cached_input_tokens=cached,
                output_tokens=resp.usage.output_tokens,
            ),
            provider=self.name, model=req.model,
            stop_reason=_stop_reason(resp),                 # incomplete→max_tokens / refusal→content_filter
            request_id=resp.id,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        assert req.json_schema is not None
        resp = await self.generate(req)                     # text.format=json_schema がネイティブ強制
        return _attach_parsed(resp, req)                    # structured.py の検証(§12)

    async def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="start")
        try:
            async with self._client.responses.stream(**self._kwargs(req)) as stream:
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        yield StreamEvent(type="text_delta", delta=event.delta)
                final = await stream.get_final_response()
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            err = self._classify(e, req.model)
            yield StreamEvent(type="error", error_kind=err.kind, error_message=err.message)
            return
        resp = _final_to_response(final, self.name, req.model)
        yield StreamEvent(type="usage", usage=resp.usage)
        yield StreamEvent(type="end", response=resp)
```

- `count_tokens`: OpenAI に公式カウント API はない → `tiktoken.get_encoding("o200k_base")` で見積り(§14)。
- refusal(structured 時の `refusal` 出力)・`finish_reason=content_filter` 相当は `CONTENT_FILTER` に分類。

### 6.2 Anthropic(`anthropic` SDK)

対象モデル: `claude-opus-4-8` / `claude-sonnet-5` / `claude-haiku-4-5`。**実装上の注意(必読)**:

1. **temperature / top_p / top_k は送信禁止**(4.7 以降 400 エラー)。§3 の決定によりリクエスト型に存在しないため構造的に防止される。
2. **アシスタントプレフィル不可**。`messages` 末尾に `role: assistant` を置いて JSON を誘導する手法は使えない。JSON 強制は **structured outputs(`output_config.format`)** で行う。
3. **thinking は `{"type": "adaptive"}` のみ**(budget_tokens 指定の manual thinking は使わない)。出力品質の強弱は **`output_config.effort`** で制御する。
4. **大きな `max_tokens` はストリーミング必須**(非ストリームは 10 分制限で切れる)。`max_output_tokens > 16384` の `generate()` 呼び出しは内部でストリーミング実行して全文を合成する。
5. **プロンプトキャッシュは明示指定**(`cache_control: {"type": "ephemeral"}`、TTL 5 分)。ブレークポイントは最大 4 個。最小キャッシュ長は opus/sonnet 系 1024 トークン、haiku 系 2048 トークン(未満は無効化されるだけで害はない)。
6. usage は `input_tokens`(非キャッシュ)/`cache_read_input_tokens`/`cache_creation_input_tokens` が別枠 → そのまま `Usage` の 3 フィールドに対応する。キャッシュ書込は入力単価の 1.25 倍で課金(§7 の価格計算に反映)。

```python
# providers/anthropic_provider.py
import time
from collections.abc import AsyncIterator
import anthropic

from ..errors import ErrorKind, ProviderError
from ..types import LLMRequest, LLMResponse, StreamEvent, Usage

_EFFORT = {"none": "low", "low": "low", "medium": "medium", "high": "high"}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)

    def _system_blocks(self, req: LLMRequest) -> list[dict]:
        blocks = []
        for p in req.system:
            b: dict = {"type": "text", "text": p.text or ""}
            if p.cache_hint:
                b["cache_control"] = {"type": "ephemeral"}   # §13 のキャッシュ境界
            blocks.append(b)
        return blocks

    def _kwargs(self, req: LLMRequest) -> dict:
        output_config: dict = {"effort": _EFFORT[req.effort]}
        if req.json_schema:
            output_config["format"] = {
                "type": "json_schema",
                "schema": req.json_schema.json_schema,
            }
        kw: dict = {
            "model": req.model,
            "system": self._system_blocks(req),
            "messages": _to_anthropic_messages(req.messages),  # 画像は base64 source ブロック
            "max_tokens": req.max_output_tokens,
            "output_config": output_config,
            "timeout": req.timeout_s,
        }
        if req.effort in ("medium", "high"):
            kw["thinking"] = {"type": "adaptive"}
        if req.stop_sequences:
            kw["stop_sequences"] = req.stop_sequences
        return kw

    async def generate(self, req: LLMRequest) -> LLMResponse:
        if req.max_output_tokens > 16384:
            return await _drain_stream(self.generate_stream(req))  # 注意4
        t0 = time.monotonic()
        try:
            msg = await self._client.messages.create(**self._kwargs(req))
        except anthropic.APIError as e:
            raise self._classify(e, req.model) from e
        return LLMResponse(
            text="".join(b.text for b in msg.content if b.type == "text"),
            usage=Usage(
                input_tokens=msg.usage.input_tokens,
                cached_input_tokens=msg.usage.cache_read_input_tokens or 0,
                cache_write_input_tokens=msg.usage.cache_creation_input_tokens or 0,
                output_tokens=msg.usage.output_tokens,
            ),
            provider=self.name, model=req.model,
            stop_reason=_STOP_MAP[msg.stop_reason],   # end_turn→end, max_tokens→max_tokens,
            request_id=msg._request_id,               # refusal→content_filter
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        assert req.json_schema is not None
        resp = await self.generate(req)               # output_config.format がネイティブ強制
        return _attach_parsed(resp, req)

    async def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="start")
        try:
            async with self._client.messages.stream(**self._kwargs(req)) as stream:
                async for text in stream.text_stream:
                    yield StreamEvent(type="text_delta", delta=text)
                final = await stream.get_final_message()
        except anthropic.APIError as e:
            err = self._classify(e, req.model)
            yield StreamEvent(type="error", error_kind=err.kind, error_message=err.message)
            return
        resp = _message_to_response(final, self.name, req.model)
        yield StreamEvent(type="usage", usage=resp.usage)
        yield StreamEvent(type="end", response=resp)

    async def count_tokens(self, req: LLMRequest) -> int:
        res = await self._client.messages.count_tokens(
            model=req.model,
            system=self._system_blocks(req),
            messages=_to_anthropic_messages(req.messages),
        )
        return res.input_tokens
```

### 6.3 Google(`google-genai` SDK)

対象モデル: `gemini-3.5-flash` / `gemini-3.1-pro-preview`。画像は §6.6。Gemini 3 系の思考制御は `thinking_level`(`"low"` / `"high"`)。

```python
# providers/google_provider.py
import time
from collections.abc import AsyncIterator
from google import genai
from google.genai import types as gt
from google.genai import errors as gerrors

_THINKING_LEVEL = {"none": "low", "low": "low", "medium": "high", "high": "high"}


class GoogleProvider:
    name = "google"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    def _config(self, req: LLMRequest) -> gt.GenerateContentConfig:
        cfg = gt.GenerateContentConfig(
            system_instruction="".join(p.text or "" for p in req.system),
            max_output_tokens=req.max_output_tokens,
            thinking_config=gt.ThinkingConfig(thinking_level=_THINKING_LEVEL[req.effort]),
            stop_sequences=req.stop_sequences or None,
            http_options=gt.HttpOptions(timeout=int(req.timeout_s * 1000)),
        )
        if req.json_schema:
            cfg.response_mime_type = "application/json"
            cfg.response_json_schema = req.json_schema.json_schema
        return cfg

    async def generate(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        try:
            resp = await self._client.aio.models.generate_content(
                model=req.model,
                contents=_to_gemini_contents(req.messages),  # 画像は gt.Part.from_bytes
                config=self._config(req),
            )
        except gerrors.APIError as e:
            raise self._classify(e, req.model) from e
        um = resp.usage_metadata
        cached = um.cached_content_token_count or 0
        return LLMResponse(
            text=resp.text or "",
            usage=Usage(
                input_tokens=(um.prompt_token_count or 0) - cached,
                cached_input_tokens=cached,
                output_tokens=(um.candidates_token_count or 0) + (um.thoughts_token_count or 0),
            ),
            provider=self.name, model=req.model,
            stop_reason=_finish_to_stop(resp),   # SAFETY/PROHIBITED_CONTENT → content_filter
            request_id=resp.response_id,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="start")
        agg: list[str] = []
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=req.model, contents=_to_gemini_contents(req.messages), config=self._config(req))
            last_chunk = None
            async for chunk in stream:
                last_chunk = chunk
                if chunk.text:
                    agg.append(chunk.text)
                    yield StreamEvent(type="text_delta", delta=chunk.text)
        except gerrors.APIError as e:
            err = self._classify(e, req.model)
            yield StreamEvent(type="error", error_kind=err.kind, error_message=err.message)
            return
        resp = _chunk_to_response(last_chunk, "".join(agg), self.name, req.model)
        yield StreamEvent(type="usage", usage=resp.usage)
        yield StreamEvent(type="end", response=resp)

    async def count_tokens(self, req: LLMRequest) -> int:
        res = await self._client.aio.models.count_tokens(
            model=req.model, contents=_to_gemini_contents(req.messages))
        return res.total_tokens
```

- 出力トークンには思考トークン(`thoughts_token_count`)を合算する(課金対象のため)。
- Gemini の暗黙キャッシュ(implicit caching)は自動 → `cached_content_token_count` を計測に反映するだけでよい。明示キャッシュ(CachedContent API)は v1 では使わない(決定。翻訳の共有プレフィックスは暗黙キャッシュで十分小さく、管理コストが上回るため)。

### 6.4 DeepSeek(OpenAI 互換)

対象モデル: `deepseek-v4-flash` / `deepseek-v4-pro`。base_url = `https://api.deepseek.com`。**旧 `deepseek-chat` / `deepseek-reasoner` は 2026-07-24 に廃止されるため一切使用しない**(models.yaml にも登録しない)。

DeepSeek と xAI は Chat Completions 互換の共通基底 `OpenAICompatProvider` を使う:

```python
# providers/openai_compat.py — DeepSeek / xAI 共通基底(Chat Completions)
class OpenAICompatProvider:
    name: str
    supports_native_json_schema: bool  # xai: True / deepseek: False

    def __init__(self, api_key: str, base_url: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    def _messages(self, req: LLMRequest) -> list[dict]:
        msgs = [{"role": "system", "content": "".join(p.text or "" for p in req.system)}]
        msgs += _to_chat_messages(req.messages)   # 画像パートは image_url(data URL)
        return msgs

    async def generate(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(
                model=req.model,
                messages=self._messages(req),
                max_tokens=req.max_output_tokens,
                stop=req.stop_sequences or None,
                timeout=req.timeout_s,
                **self._extra_body(req),
                **self._format_kwargs(req),
            )
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            raise self._classify(e, req.model) from e
        return _chat_to_response(resp, self.name, req.model, t0)

    # generate_stream: chat.completions.create(stream=True,
    #   stream_options={"include_usage": True}) を StreamEvent に変換(§12)

    async def count_tokens(self, req: LLMRequest) -> int:
        return int(estimate_tokens_o200k(req) * 1.1)   # tiktoken 見積り +10% マージン・切り捨て(§14)
```

```python
# providers/deepseek_provider.py
class DeepSeekProvider(OpenAICompatProvider):
    name = "deepseek"
    supports_native_json_schema = False   # 厳密 json_schema 非対応 → §12 の JSON モード互換戦略

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key, base_url="https://api.deepseek.com")

    def _extra_body(self, req: LLMRequest) -> dict:
        # v4 系は thinking をリクエスト body で切替(effort none/low=無効, medium/high=有効)
        if req.effort in ("medium", "high"):
            return {"extra_body": {"thinking": {"type": "enabled"}}}
        return {"extra_body": {"thinking": {"type": "disabled"}}}

    def _format_kwargs(self, req: LLMRequest) -> dict:
        if req.json_schema:
            return {"response_format": {"type": "json_object"}}   # スキーマはプロンプト側(§12)
        return {}
```

- usage の `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` を `cached_input_tokens` / `input_tokens` に対応させる(コンテキストキャッシュは自動)。

### 6.5 xAI(OpenAI 互換)

対象モデル: `grok-4.3`。base_url = `https://api.x.ai/v1`。

```python
# providers/xai_provider.py
class XAIProvider(OpenAICompatProvider):
    name = "xai"
    supports_native_json_schema = True    # structured outputs 対応(公式 docs)

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key, base_url="https://api.x.ai/v1")

    def _extra_body(self, req: LLMRequest) -> dict:
        return {}   # grok-4 系は reasoning_effort パラメータ非対応 → 送らない

    def _format_kwargs(self, req: LLMRequest) -> dict:
        if req.json_schema:
            return {"response_format": {"type": "json_schema", "json_schema": {
                "name": req.json_schema.name,
                "schema": req.json_schema.json_schema,
                "strict": req.json_schema.strict,
            }}}
        return {}
```

- 決定: xAI のストリーミングは対応として実装し、統合テスト(§17)のスモークで確認する(OpenAI 互換 API であり非対応の根拠がない)。
- v1 の既定ルーティング(§8)ではテキスト用途に xAI を含めない(画像専用)。BYOK ユーザーが設定で `grok-4.3` を選んだ場合のみ使われる。

### 6.6 画像アダプタ(ImageProvider 実装)

サイズ・品質のプロバイダ別マッピング(正規化表。呼び出し側は §3 の `ImageSize` / `ImageQuality` だけを知る):

| 抽象値 | OpenAI `gpt-image-2` | Google `gemini-3.1-flash-image` / `gemini-3-pro-image` | xAI `grok-imagine-image(-quality)` |
|---|---|---|---|
| size `1024x1024` | `size="1024x1024"` | `aspect_ratio="1:1"` | 指定不可(既定サイズで生成) |
| size `1536x1024` | `size="1536x1024"` | `aspect_ratio="16:9"` | 同上 |
| size `1024x1536` | `size="1024x1536"` | `aspect_ratio="9:16"` | 同上 |
| quality `standard` | `quality="medium"` | `image_size="1K"`(flash / pro 共通) | モデル `grok-imagine-image` |
| quality `high` | `quality="high"` | `image_size="2K"`(flash / pro 共通。quality でのモデル切替はしない) | モデル `grok-imagine-image-quality` |

- xAI は quality をモデル切替で表現するため、`ImageRequest.quality="high"` かつ `model="grok-imagine-image"` のとき、アダプタが `grok-imagine-image-quality` に置換して送信し、`ImageResult.model` には実際に使った ID を記録する。
- 返却画像はすべて **PNG に正規化**(Pillow で変換)して `ImageResult.image_bytes` に格納する。ExplainerFigure のオブジェクトストレージ保存(docs/01 §10)は呼び出し側の責務。

```python
# providers/images/openai_image.py
class OpenAIImageProvider:
    name = "openai"

    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, max_retries=0)

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        t0 = time.monotonic()
        try:
            resp = await self._client.images.generate(
                model=req.model,                       # "gpt-image-2"
                prompt=req.prompt,
                size=req.size,
                quality={"standard": "medium", "high": "high"}[req.quality],
                n=1, timeout=req.timeout_s,
            )
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            raise self._classify(e, req.model) from e
        img = base64.b64decode(resp.data[0].b64_json)
        return ImageResult(image_bytes=to_png(img), provider=self.name, model=req.model,
                           revised_prompt=resp.data[0].revised_prompt,
                           latency_ms=int((time.monotonic() - t0) * 1000))
```

```python
# providers/images/google_image.py
class GoogleImageProvider:
    name = "google"

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        cfg = gt.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=gt.ImageConfig(
                aspect_ratio=_ASPECT[req.size],
                image_size={"standard": "1K", "high": "2K"}[req.quality],
            ),
        )
        resp = await self._client.aio.models.generate_content(
            model=req.model,                           # "gemini-3.1-flash-image" | "gemini-3-pro-image"
            contents=req.prompt, config=cfg)
        part = next(p for p in resp.candidates[0].content.parts if p.inline_data)
        return ImageResult(image_bytes=to_png(part.inline_data.data),
                           provider=self.name, model=req.model, ...)
```

```python
# providers/images/xai_image.py — POST https://api.x.ai/v1/images/generations(openai SDK 互換)
class XAIImageProvider:
    name = "xai"

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        model = "grok-imagine-image-quality" if req.quality == "high" else "grok-imagine-image"
        resp = await self._client.images.generate(
            model=model, prompt=req.prompt, response_format="b64_json", n=1)
        return ImageResult(image_bytes=to_png(base64.b64decode(resp.data[0].b64_json)),
                           provider=self.name, model=model,
                           revised_prompt=getattr(resp.data[0], "revised_prompt", None), ...)
```

### 6.7 effort マッピング一覧(確定)

| 抽象 `effort` | OpenAI `reasoning.effort` | Anthropic `output_config.effort`(+`thinking`) | Google `thinking_level` | DeepSeek `thinking` | xAI |
|---|---|---|---|---|---|
| `none` | `"minimal"` | `"low"`(thinking なし) | `"low"` | `disabled` | (パラメータなし) |
| `low` | `"low"` | `"low"`(thinking なし) | `"low"` | `disabled` | 同上 |
| `medium` | `"medium"` | `"medium"` + `{"type":"adaptive"}` | `"high"` | `enabled` | 同上 |
| `high` | `"high"` | `"high"` + `{"type":"adaptive"}` | `"high"` | `enabled` | 同上 |

## 7. モデルレジストリ(models.yaml 完全形)

`packages/llm/models.yaml`。**役割はシード**であり、Alembic データマイグレーションで `llm_models` テーブルへ投入する(§15)。価格は 2026-07-06 時点(_models.md)。価格改定時は DB 行を更新する(YAML は次回マイグレーションで追随)。

```yaml
version: "2026-07-06"

providers:
  openai:
    sdk: openai
    base_url: null                      # SDK 既定(https://api.openai.com/v1)
    env_key: OPENAI_API_KEY
    modalities: [text, image]
  anthropic:
    sdk: anthropic
    base_url: null                      # SDK 既定(https://api.anthropic.com)
    env_key: ANTHROPIC_API_KEY
    modalities: [text]
  google:
    sdk: google-genai
    base_url: null                      # SDK 既定(generativelanguage.googleapis.com)
    env_key: GEMINI_API_KEY
    modalities: [text, image]
  deepseek:
    sdk: openai                         # OpenAI 互換
    base_url: "https://api.deepseek.com"
    env_key: DEEPSEEK_API_KEY
    modalities: [text]
  xai:
    sdk: openai                         # OpenAI 互換
    base_url: "https://api.x.ai/v1"
    env_key: XAI_API_KEY
    modalities: [text, image]

text_models:
  - id: gpt-5.5
    provider: openai
    display_name: "GPT-5.5"
    context_window: 1000000
    max_output_tokens: 128000
    pricing: {input_per_mtok: 5.00, cached_input_per_mtok: 0.50, output_per_mtok: 30.00}
    capabilities: {streaming: true, structured_native: true, vision: true,
                   prompt_cache: implicit, effort: true}
    enabled: true
  - id: gpt-5.4-mini
    provider: openai
    display_name: "GPT-5.4 mini"
    context_window: 400000
    max_output_tokens: 128000
    pricing: {input_per_mtok: 0.75, cached_input_per_mtok: 0.075, output_per_mtok: 4.50}
    capabilities: {streaming: true, structured_native: true, vision: true,
                   prompt_cache: implicit, effort: true}
    enabled: true
  - id: claude-opus-4-8
    provider: anthropic
    display_name: "Claude Opus 4.8"
    context_window: 1000000
    max_output_tokens: 128000
    pricing: {input_per_mtok: 5.00, cached_input_per_mtok: 0.50,
              cache_write_per_mtok: 6.25, output_per_mtok: 25.00}
    capabilities: {streaming: true, structured_native: true, vision: true,
                   prompt_cache: explicit, effort: true}
    enabled: true
  - id: claude-sonnet-5
    provider: anthropic
    display_name: "Claude Sonnet 5"
    context_window: 1000000
    max_output_tokens: 64000
    pricing: {input_per_mtok: 3.00, cached_input_per_mtok: 0.30,
              cache_write_per_mtok: 3.75, output_per_mtok: 15.00}
    capabilities: {streaming: true, structured_native: true, vision: true,
                   prompt_cache: explicit, effort: true}
    enabled: true
  - id: claude-haiku-4-5
    provider: anthropic
    display_name: "Claude Haiku 4.5"
    context_window: 200000
    max_output_tokens: 64000
    pricing: {input_per_mtok: 1.00, cached_input_per_mtok: 0.10,
              cache_write_per_mtok: 1.25, output_per_mtok: 5.00}
    capabilities: {streaming: true, structured_native: true, vision: true,
                   prompt_cache: explicit, effort: true}
    enabled: true
  - id: gemini-3.5-flash
    provider: google
    display_name: "Gemini 3.5 Flash"
    context_window: 1000000
    max_output_tokens: 65000
    pricing: {input_per_mtok: 1.50, cached_input_per_mtok: 0.375, output_per_mtok: 9.00}
    capabilities: {streaming: true, structured_native: true, vision: true,
                   prompt_cache: implicit, effort: true}
    enabled: true
  - id: gemini-3.1-pro-preview
    provider: google
    display_name: "Gemini 3.1 Pro (preview)"
    context_window: 1000000
    max_output_tokens: 64000
    pricing: {input_per_mtok: 2.00, cached_input_per_mtok: 0.50, output_per_mtok: 12.00}
    capabilities: {streaming: true, structured_native: true, vision: true,
                   prompt_cache: implicit, effort: true}
    enabled: true
  - id: deepseek-v4-flash
    provider: deepseek
    display_name: "DeepSeek V4 Flash"
    context_window: 1000000
    max_output_tokens: 384000
    pricing: {input_per_mtok: 0.14, cached_input_per_mtok: 0.0028, output_per_mtok: 0.28}
    capabilities: {streaming: true, structured_native: false, vision: false,
                   prompt_cache: implicit, effort: true}
    enabled: true
  - id: deepseek-v4-pro
    provider: deepseek
    display_name: "DeepSeek V4 Pro"
    context_window: 1000000
    max_output_tokens: 384000
    pricing: {input_per_mtok: 1.74, cached_input_per_mtok: 0.0145, output_per_mtok: 3.48}
    capabilities: {streaming: true, structured_native: false, vision: false,
                   prompt_cache: implicit, effort: true}
    enabled: true
  - id: grok-4.3
    provider: xai
    display_name: "Grok 4.3"
    context_window: 1000000
    max_output_tokens: 64000
    pricing: {input_per_mtok: 1.25, cached_input_per_mtok: 1.25, output_per_mtok: 2.50}
    capabilities: {streaming: true, structured_native: true, vision: true,
                   prompt_cache: none, effort: false}
    enabled: true

image_models:
  - id: gemini-3.1-flash-image
    provider: google
    display_name: "Gemini 3.1 Flash Image (Nano Banana 2)"
    pricing_per_image: {standard: 0.067, high: 0.134}    # 1K / 2K
    enabled: true
  - id: gemini-3-pro-image
    provider: google
    display_name: "Gemini 3 Pro Image (Nano Banana Pro)"
    pricing_per_image: {standard: 0.134, high: 0.24}     # 1K/2K / 4K
    enabled: true
  - id: gpt-image-2
    provider: openai
    display_name: "GPT Image 2"
    pricing_per_image: {standard: 0.053, high: 0.211}    # medium / high(1024x1024 目安)
    enabled: true
  - id: grok-imagine-image
    provider: xai
    display_name: "Grok Imagine"
    pricing_per_image: {standard: 0.02, high: 0.05}      # high は -quality に自動置換(§6.6)
    enabled: true
```

登録禁止(コメントとして YAML に残す): `deepseek-chat` / `deepseek-reasoner`(2026-07-24 廃止)、`imagen-4.0-generate-001`(2026-08-17 停止予定)、`gpt-image-1.5`(gpt-image-2 に一本化)。

### 7.1 ModelRegistry(registry.py)

```python
class ModelInfo(BaseModel):
    id: str; provider: str; display_name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    pricing: TextPricing | None = None
    pricing_per_image: dict[str, float] | None = None
    capabilities: Capabilities | None = None
    enabled: bool = True


class ModelRegistry:
    @classmethod
    def from_yaml(cls, path: Path) -> "ModelRegistry": ...
    @classmethod
    def from_rows(cls, rows: Sequence[LlmModelRow]) -> "ModelRegistry": ...   # DB が正(§15)
    def get(self, model_id: str) -> ModelInfo: ...          # 不明 ID は KeyError
    def text_cost_usd(self, model_id: str, usage: Usage) -> float: ...
    def image_cost_usd(self, model_id: str, quality: str) -> float: ...
```

### 7.2 価格計算(確定式)

```python
def text_cost_usd(self, model_id: str, usage: Usage) -> float:
    p = self.get(model_id).pricing
    cached_rate = p.cached_input_per_mtok if p.cached_input_per_mtok is not None else p.input_per_mtok
    write_rate = p.cache_write_per_mtok if p.cache_write_per_mtok is not None else p.input_per_mtok
    usd = (
        usage.input_tokens * p.input_per_mtok
        + usage.cached_input_tokens * cached_rate
        + usage.cache_write_input_tokens * write_rate
        + usage.output_tokens * p.output_per_mtok
    ) / 1_000_000
    return round(usd, 8)
```

### 7.3 画像価格

`image_cost_usd(model_id, quality)` は `pricing_per_image[quality]` を返す(gpt-image-2 のみトークン課金だが、per-image の代表値で計上する。決定: 課金 API のレスポンス usage から精算するのは gpt-image-2 だけ例外処理が増えるため、v1 は全画像モデルを per-image 固定額で統一計上し、月次で実請求と突合する)。

## 8. タスクルーティング(routing.yaml 完全形)

タスクは **8 種で固定**(DB の CHECK 制約にも使う): `translation` / `retranslation_escalation` / `chat` / `summary` / `article` / `overview_figure_dsl` / `vocab` / `explainer_image`。

- `summary` は取り込み時の ✦3行要約(docs/02)と詳細要約(docs/05 §7)の両方が使う。
- `overview_figure_dsl` は全体概要図の構造化 JSON DSL 生成(docs/07 §1.2。SVG レンダリング自体は LLM を使わない)。
- **概要図のラスター生成モード(docs/07 §1.1)は `explainer_image` のルーティングを共用する**(決定。docs/01 §10 が「同じ provider/prompt/version を記録する」と定めており、独立タスクにする理由がないため)。
- 用語自動抽出(docs/03 §7 の論文ローカル用語集)は `summary` タスクに相乗りせず `vocab` でもなく、取り込みパイプライン(plans/05)側で `summary` チェーンを使う(決定: 取り込み時に 1 回だけ走る構造化抽出であり、要約と同じ上位モデル品質が必要なため)。

`packages/llm/routing.yaml`:

```yaml
version: "2026-07-06"

retry_defaults:                       # §9 のモデル内リトライ規則
  max_attempts: 3                     # 初回 + リトライ2回
  backoff_base_s: 1.0                 # 待機 = base * factor^(attempt-1) + jitter
  backoff_factor: 4.0                 # → 1s, 4s(+ 0〜1s の full jitter)
  jitter_s: 1.0
  respect_retry_after: true           # 429 の Retry-After を優先(上限 retry_after_cap_s)
  retry_after_cap_s: 30

tasks:
  translation:                        # docs/03 §11: 安価な大量処理向け
    chain: [deepseek-v4-flash, gemini-3.5-flash, gpt-5.4-mini]
    effort: none
    max_output_tokens: 4096           # ブロック単位翻訳(docs/03 §3)
    timeout_s: 120
    structured: true                  # プレースホルダ検証しやすい JSON 出力(plans/06 で定義)
  retranslation_escalation:           # docs/03 §9: 上位モデルへエスカレーション
    chain: [claude-sonnet-5, gpt-5.5, gemini-3.1-pro-preview]
    effort: high
    max_output_tokens: 4096
    timeout_s: 180
    structured: true
  chat:                               # docs/05。ストリーミング必須(初回トークン p50 5秒)
    chain: [claude-opus-4-8, gpt-5.5, gemini-3.5-flash]
    effort: medium
    max_output_tokens: 8192
    timeout_s: 120
    streaming: true
  summary:                            # ✦3行要約(p50 20秒)・詳細要約
    chain: [claude-opus-4-8, gpt-5.5, gemini-3.5-flash]
    effort: low
    max_output_tokens: 2048
    timeout_s: 60
    structured: true                  # スキーマは呼び出し側定義: 3行要約+提案タグ {"summary_lines": [...], "suggested_tags": [...]} = plans/05、詳細要約等 = plans/07
  article:                            # docs/07 §2。記事全体の生成・再生成(p50 30秒)
    chain: [claude-opus-4-8, gpt-5.5]
    effort: high
    max_output_tokens: 32000
    timeout_s: 300
    structured: true                  # Article ブロック構造 JSON(plans/07 で schema 定義)
  overview_figure_dsl:                # docs/07 §1.2。3カードフロー図 DSL
    chain: [claude-opus-4-8, gpt-5.5]
    effort: high
    max_output_tokens: 4096
    timeout_s: 120
    structured: true                  # OverviewFigure DSL schema(plans/07)
  vocab:                              # docs/11 §8: 小型・低価格・低レイテンシ(p50 3秒)
    chain: [claude-haiku-4-5, gpt-5.4-mini, gemini-3.5-flash]
    effort: none
    max_output_tokens: 2048
    timeout_s: 30
    structured: true                  # VocabEntry.ai フィールド一式の JSON(plans/07)
  explainer_image:                    # docs/07 §1.3 + 概要図ラスターモード
    chain: [gemini-3.1-flash-image, grok-imagine-image, gpt-image-2]
    size: "1536x1024"
    quality: standard
    timeout_s: 120
```

既定チェーンの根拠は spec-decisions G項・docs/09 §3.4 の案を確定値化したもの。`retranslation_escalation` は G項に案がないため本計画で確定: **primary `claude-sonnet-5`**。理由: 翻訳忠実性の要求(docs/03 §1)に対し opus は過剰単価で、sonnet($3/$15)が 1M コンテキストと structured outputs を備え、`deepseek-v4-flash` より確実に上位のため。

routing.yaml も **シード**であり、実行時の正は `llm_task_routes` / ユーザー上書き(§15)。

## 9. 実行エンジン(LLMRouter)— リトライ・フォールバック規則

### 9.1 規則(確定値)

1. **モデル内リトライ**: `ErrorKind ∈ RETRYABLE` のとき同一モデルで最大 **3 試行**(初回+2 リトライ)。待機は `1.0s × 4^(n-1) + jitter(0〜1.0s)` = 約 1s → 4s。`RATE_LIMIT` で `Retry-After` があればそれを優先(上限 30s)。
2. **フォールバック**: 3 試行で回復しない retryable エラー、または `CONTENT_FILTER` / `SCHEMA_VALIDATION` / `MODEL_NOT_FOUND` / `BILLING`(リトライせず即)で **チェーンの次モデルへ**。`AUTH`(運営キー)と `INVALID_REQUEST` と `CONTEXT_LENGTH` はフォールバックせずチェーン中断(実装・設定の欠陥であり別モデルで隠蔽しない。P3)。ただし `AUTH` がユーザーキー由来の場合は §11.4 の特例。
3. **チェーン露出**: 全滅時は `ProviderChainExhausted` を送出。ジョブ系タスク(translation / retranslation_escalation / summary / article / overview_figure_dsl / vocab / explainer_image)は arq ジョブの指数バックオフ再試行 3 回(docs/09 §2)→ 以後は手動再試行(決定: vocab は plans/07 の `jobs.kind='vocab'` としてジョブ実行されるためジョブ系に含める)。対話系(chat)は即エラー表示(docs/09 §2「チャットはエラーを明示する」)。
4. **フォールバックの記録**: 発生ごとに (a) `usage_records` に `status='error'` の行(失敗側)+成功時に `fallback_rank>0` の行、(b) ジョブの処理ログ(2a)に `{"event": "provider_fallback", "from": "deepseek-v4-flash", "to": "gemini-3.5-flash", "error_kind": "rate_limit"}` を追記。
5. **ストリーミング中のフォールバックは開始前のみ**。最初の `text_delta` を消費者に流した後のエラーはフォールバックせず `error` イベントで終了する(部分出力の二重生成・課金二重化を防ぐ)。チャット UI は「再生成」導線(docs/05 §6)で回復する。
6. **content_filter の縮退**: 次プロバイダへは 1 回だけ移る(モデルごとにフィルタ基準が違うため)。全滅時の扱いはタスク固有: translation → ブロックを原文のまま表示+`quality_flags: provider_refusal`(docs/03 §4 と同じ縮退)、chat → エラーメッセージ、article/figure → ジョブ失敗として処理ログに明示。
7. **ユーザー明示選択時のチェーン**(§15): ユーザーがタスクのモデルを上書きしている場合、チェーンは `[ユーザー選択モデル] + 既定チェーン(選択モデルを除く)` とする。選択モデルが落ちても処理は継続し、ログで判別できる。

### 9.2 router.py(コードスケッチ)

```python
class LLMRouter:
    def __init__(self, registry: ModelRegistry, routes: RouteResolver,
                 key_store: KeyStore, meter: MeterHook, clock=time.monotonic) -> None: ...

    async def run(
        self,
        task: str,                                    # 8 タスクのいずれか
        build: Callable[[str], LLMRequest],           # model_id を受けて LLMRequest を構築
        *,
        user_id: str | None,
        library_item_id: str | None = None,
        job_id: str | None = None,
        mode: Literal["generate", "structured"] = "generate",
    ) -> LLMResponse:
        chain = await self._routes.chain_for(task, user_id)      # ユーザー上書き反映(§15)
        errors: list[ProviderError] = []
        for rank, model_id in enumerate(chain):
            info = self._registry.get(model_id)
            key = await self._key_store.resolve(user_id, info.provider)
            provider = self._provider_for(info.provider, key.api_key)
            for attempt in range(1, self._retry.max_attempts + 1):
                try:
                    fn = provider.generate_structured if mode == "structured" else provider.generate
                    resp = await fn(build(model_id))
                except ProviderError as e:
                    await self._meter.record(_error_draft(task, e, key, attempt, rank, ...))
                    if e.kind == ErrorKind.AUTH and key.source == "user":
                        key = await self._demote_user_key(user_id, info.provider)  # §11.4
                        provider = self._provider_for(info.provider, key.api_key)
                        continue
                    if e.retryable and attempt < self._retry.max_attempts:
                        await asyncio.sleep(self._backoff(e, attempt))
                        continue
                    errors.append(e)
                    if not e.fallback_eligible:
                        raise ProviderChainExhausted(task, errors) from e
                    break                                        # 次モデルへ
                else:
                    cost = self._registry.text_cost_usd(model_id, resp.usage)
                    await self._meter.record(_ok_draft(task, resp, key, attempt, rank, cost, ...))
                    return resp
        raise ProviderChainExhausted(task, errors)

    def run_stream(self, task, build, *, user_id, ...) -> AsyncIterator[StreamEvent]:
        """chat 用。start 前のエラーのみフォールバック(規則5)。end 時に計測記録。"""
        ...


class ImageRouter:
    async def run(self, task: str, prompt: str, *, user_id, ...) -> ImageResult:
        """explainer_image チェーンを LLMRouter と同一規則で実行。cost は §7.3 の per-image。"""
        ...
```

### 9.3 呼び出し例(翻訳ワーカー)

```python
# apps/worker/src/alinea_worker/tasks/translate_blocks.py(plans/06 側の管轄。呼び出し形だけ規定)
resp = await llm_router.run(
    "translation",
    build=lambda model_id: build_translation_request(model_id, block, ctx, glossary_snapshot),
    user_id=owner_user_id, library_item_id=item_id, job_id=job.id, mode="structured",
)
```

## 10. usage_records(計測記録)

### 10.1 DDL(完全形)

```sql
CREATE TABLE usage_records (
    id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id                  UUID REFERENCES users(id) ON DELETE CASCADE,
    library_item_id          UUID REFERENCES library_items(id) ON DELETE SET NULL,
    job_id                   UUID,
    task                     TEXT NOT NULL CHECK (task IN (
                               'translation', 'retranslation_escalation', 'chat', 'summary',
                               'article', 'overview_figure_dsl', 'vocab', 'explainer_image',
                               'key_test')),
    provider                 TEXT NOT NULL CHECK (provider IN
                               ('openai', 'anthropic', 'google', 'deepseek', 'xai')),
    model                    TEXT NOT NULL,
    key_source               TEXT NOT NULL CHECK (key_source IN ('operator', 'user')),
    input_tokens             INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_write_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens            INTEGER NOT NULL DEFAULT 0,
    image_count              INTEGER NOT NULL DEFAULT 0,
    cost_usd                 NUMERIC(12, 8) NOT NULL DEFAULT 0,
    status                   TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    attempt                  INTEGER NOT NULL DEFAULT 1,
    fallback_rank            INTEGER NOT NULL DEFAULT 0,
    error_kind               TEXT,
    latency_ms               INTEGER,
    request_id               TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_usage_records_user_month ON usage_records (user_id, created_at);
CREATE INDEX idx_usage_records_task ON usage_records (task, created_at);
```

- `key_test` は §11.5 の接続テストの記録用タスク値(8 タスク+1)。
- `user_id` は NULL 可(運営バッチ・共有翻訳キャッシュの再生成など所有者不在の実行)。共有翻訳(docs/03 §8 の `scope: shared`)の翻訳実行は **トリガーしたユーザーの user_id** で記録する(クォータ帰属も同じ。2 人目以降はキャッシュヒットで LLM を呼ばないため記録なし)。

### 10.2 記録規則

- 1 回の API 呼び出し(試行)= 1 行。成功行は `status='ok'` + usage + cost、失敗行は `status='error'` + `error_kind`(usage は取得できた場合のみ)。
- `Usage.input_tokens` は**非キャッシュ入力のみ**。各アダプタが §6 の対応で正規化する(OpenAI: `input_tokens - cached_tokens`、Anthropic: `input_tokens` そのまま、Google: `prompt_token_count - cached_content_token_count`、DeepSeek: `prompt_cache_miss_tokens`)。
- `cost_usd` は §7.2 の式で**記録時に確定**(モデル価格の後日変更に影響されない)。
- 月次クォータ(docs/09 §3.5: 全文翻訳本数・チャットメッセージ数・画像生成枚数)の消費判定は `key_source='operator'` の行のみを集計する。クォータ上限テーブル(`quota_limits`)と集計規則は plans/07 §9、超過時挙動(429 `quota_exceeded` / `waiting_quota`)は plans/03 §17.4 の管轄。本層は `MeterHook.record()` を呼ぶだけで、クォータの事前チェックは呼び出し側(ジョブ投入時・チャット送信時)が行う。
- 設定画面のクォータ残量表示(docs/09 §3.5)用に集計ビューを用意する:

```sql
CREATE VIEW monthly_usage AS
SELECT user_id,
       date_trunc('month', created_at) AS month,
       task,
       count(*) FILTER (WHERE status = 'ok')                AS ok_calls,
       sum(image_count)                                     AS images,
       sum(cost_usd)                                        AS cost_usd
FROM usage_records
WHERE key_source = 'operator'
GROUP BY user_id, date_trunc('month', created_at), task;
```

## 11. BYOK(ユーザー API キー)

### 11.1 キー解決順(確定)

`KeyStore.resolve(user_id, provider)`:

1. `byok_api_keys`(plans/02 §4.2)に該当ユーザー×プロバイダの行があり `status != 'invalid'` → **ユーザーキー**(`source="user"`。クォータ非消費)。
2. なければ**運営キー**(環境変数 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` / `XAI_API_KEY`。`source="operator"`。クォータ消費)。
3. 運営キーも未設定のプロバイダ → `ProviderError(kind=AUTH)` 相当でそのモデルをチェーンからスキップ(起動時に警告ログ)。

### 11.2 暗号化保存

- **Fernet(cryptography)+マスタキー**(決定: 暗号化方式は本書を正とする。plans/02 §4.2・plans/03 §17.3 の「AES-256-GCM」「`BYOK_ENCRYPTION_KEY`」「`API_KEY_ENC_KEY`」の記述は本書に追随して更新する — 基盤への追加要求)。マスタキーは環境変数 `ALINEA_KEY_ENCRYPTION_SECRET`(Fernet 標準の 44 文字 urlsafe base64)。ローテーション用にカンマ区切り複数指定を許し `MultiFernet` で復号(先頭キーで暗号化)。
- 平文キーは DB・ログ・例外メッセージに残さない。表示は `key_hint`(末尾 4 文字)のみ。**再表示不可・再入力のみ**(docs/09 §4 の決定)。
- テーブルは **plans/02 §4.2 の `byok_api_keys` を正**とする(plans/07 §12-⚠2 の統一方針に従い、本書旧称 `user_provider_keys` は廃止)。本層の要件の 2 列は **plans/02 §4.2 の CREATE TABLE に反映済み**(Fernet 化コメントも反映済み):

```sql
ALTER TABLE byok_api_keys
    ADD COLUMN status         TEXT NOT NULL DEFAULT 'untested'
                 CHECK (status IN ('untested', 'valid', 'invalid')),
    ADD COLUMN last_tested_at TIMESTAMPTZ;
-- encrypted_key BYTEA には Fernet トークンを格納する(上記決定)
```

### 11.3 API(設定 4f「アカウント」カテゴリ)

パスは plans/03 §17.3 を正とする(`/api` プレフィックス。本書旧記載の `/api/v1/settings/provider-keys` 系は廃止):

| メソッド・パス | 動作 |
|---|---|
| `GET /api/settings/api-keys` | 登録済みキー一覧(`{provider, masked, status, last_tested_at, created_at}[]`。`masked` = `"sk-…" + key_hint`。平文なし。`status` / `last_tested_at` は plans/03 §17.3 への追加フィールド — 基盤への追加要求) |
| `PUT /api/settings/api-keys/{provider}` | body `{"api_key": "sk-..."}`。暗号化保存(upsert)+`status='untested'` |
| `DELETE /api/settings/api-keys/{provider}` | **204**。キー削除(以後は運営キー+クォータ消費に戻る) |
| `POST /api/settings/api-keys/{provider}/test` | §11.5 の接続テスト。結果 `{"ok": bool, "latency_ms": int, "model": str, "error_kind": str \| null}`(plans/03 §17.3 への追加エンドポイント — 基盤への追加要求) |

### 11.4 実行中のユーザーキー失効(特例)

ユーザーキーで `AUTH` エラーが出た場合: (1) `status='invalid'` に更新、(2) 通知はしない(通知 3 種の枠外。設定画面のキー行に「無効」表示)、(3) **同一リクエストを運営キーで即時再実行**(クォータ消費に切替)、(4) 処理ログに `{"event": "byok_key_invalid", "provider": ...}` を記録。理由: 読書・翻訳を止めない(P3)+挙動はログと設定画面で透明。

### 11.5 接続テスト

プロバイダごとに**最安テキストモデルで `max_output_tokens=16` の 1 回生成**(プロンプト: `"Reply with OK."`)を実行する: openai=`gpt-5.4-mini`、anthropic=`claude-haiku-4-5`、google=`gemini-3.5-flash`、deepseek=`deepseek-v4-flash`、xai=`grok-4.3`。成功で `status='valid'`。結果は `usage_records` に `task='key_test'` / `key_source='user'` で記録する(コスト透明性)。

## 12. ストリーミング正規化と structured output 互換戦略

### 12.1 ストリーミング正規化

全アダプタは §3 の `StreamEvent` 列に正規化する。順序契約: `start` → `text_delta`(0 回以上)→ `usage` → `end`(正常時)/ 任意時点で `error`(以後イベントなし)。プロバイダ固有イベント(Anthropic の `thinking_delta`、OpenAI の reasoning サマリ等)は**破棄**する(v1 の UI に思考表示は存在しない)。

チャット API(`POST /api/chat/threads/{thread_id}/messages`、plans/03 §10.3 管轄)の SSE 契約は **plans/03 §10.3 が正**(`start` / `delta` / `evidence` / `done` / `error`)。apps/api のチャットハンドラが StreamEvent を次の対応で変換する(1:1 ではない):

| StreamEvent | SSE(plans/03 §10.3) |
|---|---|
| `start` | `event: start`(message_id 等は API 層が付与) |
| `text_delta` | `[[ev:n]]` 検証・ブロック分割後に `event: delta`(+初出時 `event: evidence`) |
| `usage` | SSE に流さない(MeterHook 記録のみ) |
| `end` | `event: done` |
| `error` | `event: error`(problem+json 形式へ変換) |

### 12.2 structured output 互換戦略(確定)

| プロバイダ | 方式 |
|---|---|
| OpenAI | ネイティブ: Responses API `text.format = {type: "json_schema", strict: true}` |
| Anthropic | ネイティブ: `output_config.format = {type: "json_schema", schema}`(プレフィル代替。注意 §6.2-2) |
| Google | ネイティブ: `response_mime_type="application/json"` + `response_json_schema` |
| xAI | ネイティブ: `response_format = {type: "json_schema", ...}` |
| DeepSeek | **JSON モード互換戦略**: `response_format={"type": "json_object"}` + スキーマをプロンプト末尾に注入 + Pydantic 検証 + 再試行 |

互換戦略の実装(`structured.py`):

```python
SCHEMA_PROMPT = (
    "\n\n出力は次の JSON Schema に厳密に従う JSON オブジェクトのみとする。"
    "説明文・コードフェンスを含めない。\nSchema:\n{schema_json}"
)

async def structured_with_json_mode(provider, req: LLMRequest) -> LLMResponse:
    """structured_native=False のプロバイダ用。検証失敗は最大2回、エラーを添えて再生成。"""
    work = _inject_schema_prompt(req)                       # SCHEMA_PROMPT を最終 user パートに追記
    last_err: str | None = None
    for attempt in range(3):                                # 初回 + 再試行2回
        if last_err:
            work = _append_fix_instruction(work, last_err)  # 「前回出力は検証に失敗: {err}。修正して再出力」
        resp = await provider.generate(work)
        try:
            resp.parsed = _validate(resp.text, req.json_schema)   # json.loads + jsonschema 検証
            return resp
        except SchemaValidationFailed as e:
            last_err = str(e)[:500]
    raise ProviderError(ErrorKind.SCHEMA_VALIDATION, provider.name, req.model,
                        f"structured output validation failed: {last_err}")
```

- ネイティブ対応プロバイダでも `_validate()` は必ず通す(`_attach_parsed()`)。ネイティブ強制の検証失敗(理論上稀)は再試行 1 回 → `SCHEMA_VALIDATION` でフォールバック。
- 検証は `jsonschema`(draft 2020-12)+呼び出し側の Pydantic モデル `model_validate(resp.parsed)` の二段。JSON Schema は Pydantic モデルから `model_json_schema()` で生成し、`JsonSchemaSpec` に詰める(スキーマの二重管理をしない)。

## 13. 翻訳プロンプトのプロンプトキャッシュ設計

翻訳(tasks: translation / retranslation_escalation)は物量が最大であり(docs/09 §3.1)、プロンプトを**キャッシュ可能な安定プレフィックス順**に構成する:

| 順序 | 内容 | 変化の単位 | 概算トークン |
|---|---|---|---|
| system[0] | 静的プリアンブル: 翻訳規則(docs/03 §1)・文体規定(§5)・プレースホルダ規約(§4)・対訳例集 | リリース単位で固定 | 3,000 |
| system[1] | 論文スコープ文脈: 用語スナップショット(glossary_snapshot)+セクション見出しツリー | TranslationSet 単位 | 1,000〜4,000 |
| messages[0](user) | ブロック文脈: 前後ブロック+セクション見出し+対象ブロック(プレースホルダ化済み) | ブロック単位 | 500〜2,000 |

プロバイダ別の適用:

- **Anthropic**: `system[0].cache_hint = True` と `system[1].cache_hint = True` の 2 ブレークポイント(§6.2 の `cache_control: ephemeral`、TTL 5 分)。**同一 TranslationSet のブロック翻訳は 1 つの arq ジョブ内で直列実行**し(plans/06 のジョブ設計に要件として渡す)、呼び出し間隔を TTL 5 分未満に保つ。キャッシュ書込 1.25 倍は §7.2 で課金計上。haiku 系の最小 2048 トークン未満は自動的に非キャッシュになるだけで無害。
- **OpenAI**: 自動プレフィックスキャッシュ。ルーティング精度向上のため `LLMRequest.prompt_cache_key = f"tr:{revision_id}:{style}:{glossary_snapshot_id}"` を設定する(§6.1)。
- **DeepSeek**: コンテキストキャッシュ自動(hit $0.0028/1M が既定チェーンの最重要コスト要素)。プレフィックス順を守る以外の操作は不要。
- **Google**: 暗黙キャッシュ自動。明示 CachedContent は使わない(§6.3 の決定)。
- **xAI**: prompt_cache: none(§7)。キャッシュ前提の最適化はしない。

チャット(task: chat)も同じ原則を適用する: system[0]=チャット指示(根拠チップ規約 docs/05 §5)を `cache_hint=True`、system[1]=論文文脈(構造化ドキュメント要約+関連セクション全文)を `cache_hint=True`、会話履歴を messages に置く。会話継続中の 2 呼び出し目以降で論文文脈キャッシュが効く。

## 14. count_tokens(文脈予算の見積り)

用途はチャットの文脈構築(docs/05 §3「コンテキスト長を超える場合」の判定)と 30 ページ超判定の補助のみ。**課金計算には使わない**(課金は API レスポンスの usage が正)。

| プロバイダ | 実装 |
|---|---|
| Anthropic | 公式 `client.messages.count_tokens`(正確) |
| Google | 公式 `client.aio.models.count_tokens`(正確) |
| OpenAI | `tiktoken` `o200k_base` でローカル見積り |
| DeepSeek / xAI | `tiktoken` `o200k_base` 見積り × 1.1(安全マージン) |

画像パートは 1 枚 = 1,600 トークンの固定値で加算する(見積り用途の保守値)。文脈予算は `context_window - max_output_tokens - 2,048(安全帯)` を上限とする。

## 15. ルーティング設定テーブルと解決順

docs/09 §3.2「設定テーブルで管理・再デプロイなしで変更」を満たすため、YAML はシード、**DB が実行時の正**とする。

```sql
CREATE TABLE llm_models (               -- models.yaml text_models/image_models のミラー
    id            TEXT PRIMARY KEY,     -- 例 'claude-opus-4-8'
    provider      TEXT NOT NULL,
    modality      TEXT NOT NULL CHECK (modality IN ('text', 'image')),
    display_name  TEXT NOT NULL,
    spec          JSONB NOT NULL,       -- context_window / pricing / capabilities(§7 と同形)
    enabled       BOOLEAN NOT NULL DEFAULT true,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE llm_task_routes (          -- routing.yaml tasks のミラー(運営既定)
    task            TEXT PRIMARY KEY CHECK (task IN (
                      'translation', 'retranslation_escalation', 'chat', 'summary',
                      'article', 'overview_figure_dsl', 'vocab', 'explainer_image')),
    chain           TEXT[] NOT NULL,    -- 先頭が primary。全要素は llm_models.id
    params          JSONB NOT NULL,     -- effort / max_output_tokens / timeout_s / size / quality
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_task_model_overrides (  -- 設定 4f のユーザー選択
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task        TEXT NOT NULL,
    model_id    TEXT NOT NULL REFERENCES llm_models(id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, task)
);
```

- シード投入: Alembic データマイグレーションが `models.yaml` / `routing.yaml` を読んで upsert(`updated_at` が YAML の version より新しい行は上書きしない=運営の手動変更を保護)。
- 解決順(`RouteResolver.chain_for(task, user_id)`): `user_task_model_overrides`(あれば先頭に挿入)→ `llm_task_routes.chain` → `llm_models.enabled=false` と運営キー未設定プロバイダのモデルを除外。結果を **Redis に 60 秒キャッシュ**(キー `llm:route:{task}`、ユーザー上書きは `llm:route:{task}:{user_id}`)。
- ユーザーが選択できるモデルはタスクの modality と必要 capability(chat→streaming、structured タスク→常に許可 = 互換戦略があるため)を満たす `enabled` モデルのみ。UI 提供 API は **plans/03 §17.1〜17.2 を正**とする(本書旧記載の `/api/v1/llm/models`・`/api/v1/settings/llm-routing` は廃止):
  - 選択可能モデル一覧は `GET /api/settings` の付帯フィールド `available_models`(plans/03 §17.1)で配信する。
  - ユーザー上書きは `PATCH /api/settings` の `llm_routing`(plans/03 §17.2。値 `null` で既定に戻す)。書き込み先は `user_task_model_overrides`(`model_id` のみ保存。provider は `llm_models` から導出)。
  - `llm_routing` の設定キー → 本書タスク名の対応(確定): `translation`→`translation` / `retranslation`→`retranslation_escalation` / `chat`→`chat` / `summary`→`summary` / `article`→`article` / `vocab`→`vocab` / `figure_dsl`→`overview_figure_dsl` / `figure_image`→`explainer_image`。
- 設定 UI 上の配置は docs/09 §7.1 に従う: 翻訳モデル選択=「翻訳」カテゴリ、チャット/要約/記事/語彙/画像=「チャット」カテゴリ、BYOK キー=「アカウント」カテゴリ(8 カテゴリ構成は変えない)。

## 16. 環境変数一覧(本層が参照するもの)

| 変数 | 用途 | 例 |
|---|---|---|
| `OPENAI_API_KEY` | 運営キー(テキスト+画像) | `sk-...` |
| `ANTHROPIC_API_KEY` | 運営キー | `sk-ant-...` |
| `GEMINI_API_KEY` | 運営キー(google-genai が既定で参照する変数名に合わせる) | `AIza...` |
| `DEEPSEEK_API_KEY` | 運営キー | `sk-...` |
| `XAI_API_KEY` | 運営キー(テキストは既定チェーン外、画像+BYOK 検証用) | `xai-...` |
| `ALINEA_KEY_ENCRYPTION_SECRET` | BYOK Fernet マスタキー(カンマ区切りでローテーション) | 44 文字 base64 |
| `ALINEA_LLM_ROUTE_CACHE_TTL_S` | ルート解決の Redis キャッシュ TTL(既定 `60`) | `60` |

## 17. テスト計画(pytest)

`packages/llm/tests/`:

1. **エラー分類マトリクス**(`test_error_classification.py`): 各 SDK 例外モック → `ErrorKind` の全対応(§4 の表を網羅)。
2. **フォールバック規則**(`test_router_fallback.py`): FakeLLMProvider で (a) retryable 3 試行→次モデル、(b) `CONTENT_FILTER` 即フォールバック、(c) `INVALID_REQUEST` で即中断、(d) チェーン全滅で `ProviderChainExhausted`、(e) `fallback_rank` と usage 記録行数の検証、(f) Retry-After 尊重(上限 30s)。
3. **ストリーミング契約**(`test_stream_contract.py`): 正規イベント順序(start→delta*→usage→end)、delta 送出後エラーでフォールバックしないこと。
4. **structured 互換戦略**(`test_structured.py`): JSON モード再試行(2 回で成功/3 回失敗→`SCHEMA_VALIDATION`)、修正指示の注入、ネイティブ側の `_validate` 通過。
5. **価格計算**(`test_pricing.py`): §7.2 の式をモデル×キャッシュ有無で検証(例: `deepseek-v4-flash` input 10,000 / cached 90,000 / output 5,000 → $0.00305)。Anthropic の cache_write 1.25 倍。
6. **BYOK**(`test_key_store.py`): Fernet 往復・MultiFernet ローテーション・`resolve` の user→operator 順・AUTH 時の invalid 化と運営キー再実行(§11.4)。
7. **ルート解決**(`test_routing.py`): ユーザー上書きの先頭挿入・disabled モデル除外・キー未設定プロバイダ除外。
8. **統合スモーク**(`tests/integration/`、`RUN_LLM_SMOKE=1` でのみ実行): 各プロバイダ実キーで 16 トークン生成+structured 1 件+画像 1 枚(CI では実行しない。リリース前チェックリストで手動実行)。

受け入れ基準(docs/09 §8 のうち本層の担当分):

- [ ] 用途別ルーティングが DB テーブルで管理され、モデル ID を再デプロイなしで変更できる
- [ ] 第 1 プロバイダ停止時にフォールバック連鎖で処理が継続し、処理ログと usage_records から使用モデルが判別できる
- [ ] BYOK キー設定時はクォータを消費せず(`key_source='user'`)、キーは Fernet 暗号化保存・末尾 4 文字のみ表示・再表示不可
- [ ] `deepseek-chat` / `deepseek-reasoner` / `imagen-4.0` 系がコード・YAML・DB のどこにも存在しない
- [ ] Anthropic アダプタが temperature 系パラメータを一切送信しない(リクエスト型に存在しない)
- [ ] 全プロバイダの structured 出力が同一の Pydantic 検証を通過して `parsed` に入る

## 18. v2 に明示的に送る項目

- v2: Google 明示キャッシュ(CachedContent API)によるチャット長文脈の恒久キャッシュ。
- v2: gpt-image-2 のトークン実課金精算(v1 は per-image 固定額計上。§7.3)。
- v2: 運営管理画面からの `llm_models` / `llm_task_routes` 編集 UI(v1 は SQL / Alembic で運用)。
- v2: プロバイダ側バッチ API(OpenAI Batch 等)による翻訳の非同期割引実行。
