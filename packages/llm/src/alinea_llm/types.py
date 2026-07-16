"""共通型定義(plans/04 §3)。

呼び出し側はタスク名と正規化リクエストのみを扱い、各社 SDK の方言を知らない。
決定: temperature / top_p / top_k は型に存在しない(§3 の決定。全タスクは effort
とプロンプトで制御する)。
"""

from __future__ import annotations

import base64
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant"]


class ContentPart(BaseModel):
    """メッセージ本文の1パート。テキストまたは画像(docs/05 §4)。"""

    model_config = ConfigDict(frozen=True)

    type: Literal["text", "image"] = "text"
    text: str | None = None
    image_b64: str | None = None
    image_media_type: str | None = None  # "image/png" | "image/jpeg" | "image/webp"
    cache_hint: bool = False  # True: このパートまでをプロンプトキャッシュ境界とする(§13)

    @classmethod
    def from_image_bytes(cls, data: bytes, media_type: str) -> ContentPart:
        return cls(
            type="image",
            image_b64=base64.b64encode(data).decode(),
            image_media_type=media_type,
        )

    @classmethod
    def from_text(cls, text: str, *, cache_hint: bool = False) -> ContentPart:
        return cls(type="text", text=text, cache_hint=cache_hint)


class Message(BaseModel):
    role: Role
    parts: list[ContentPart]


class JsonSchemaSpec(BaseModel):
    """generate_structured 用の JSON Schema 指定(draft 2020-12)。"""

    name: str
    json_schema: dict[str, Any]
    strict: bool = True


Effort = Literal["none", "low", "medium", "high"]


class LLMRequest(BaseModel):
    model: str  # llm_models.id(例 "claude-opus-4-8")。Router が設定
    system: list[ContentPart] = Field(default_factory=list)
    messages: list[Message]
    max_output_tokens: int = 4096
    effort: Effort = "none"  # プロバイダ別マッピングは §6.7
    stop_sequences: list[str] = Field(default_factory=list)
    json_schema: JsonSchemaSpec | None = None  # generate_structured のときのみ必須
    prompt_cache_key: str | None = None  # OpenAI prompt_cache_key(§13)
    timeout_s: float = 120.0
    metadata: dict[str, str] = Field(default_factory=dict)  # {"task": ..., "trace_id": ...}


class Usage(BaseModel):
    """トークン計測の正規化形。input_tokens はキャッシュ分を含まない(§10.2)。"""

    input_tokens: int = 0  # 非キャッシュ入力
    cached_input_tokens: int = 0  # キャッシュ読取入力(hit)
    cache_write_input_tokens: int = 0  # キャッシュ書込入力(Anthropic のみ非0)
    output_tokens: int = 0


StopReason = Literal["end", "max_tokens", "stop_sequence", "content_filter"]


class LLMResponse(BaseModel):
    text: str  # 全文テキスト(structured の場合は JSON 文字列)
    parsed: dict[str, Any] | None = None  # generate_structured の検証済み JSON
    usage: Usage = Field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    stop_reason: StopReason = "end"
    request_id: str | None = None
    latency_ms: int = 0
    fallback_rank: int = 0  # 0=primary, 1=第1フォールバック, …(Router が設定。M0 計画)


class StreamEvent(BaseModel):
    """全プロバイダ共通のストリームイベント(§12 で SSE に橋渡し)。"""

    type: Literal["start", "text_delta", "usage", "end", "error"]
    delta: str | None = None  # text_delta のみ
    usage: Usage | None = None  # usage / end
    response: LLMResponse | None = None  # end のみ(全文含む)
    error_kind: str | None = None  # error のみ(ErrorKind 値)
    error_message: str | None = None


ImageSize = Literal["1024x1024", "1536x1024", "1024x1536"]
ImageQuality = Literal["standard", "high"]


class ImageRequest(BaseModel):
    model: str
    prompt: str  # 共通プリアンブル込みの最終プロンプト(docs/07 §1.3)
    size: ImageSize = "1536x1024"
    quality: ImageQuality = "standard"
    timeout_s: float = 120.0
    metadata: dict[str, str] = Field(default_factory=dict)


class ImageResult(BaseModel):
    image_bytes: bytes  # PNG 正規化済み
    media_type: Literal["image/png"] = "image/png"
    provider: str = ""
    model: str = ""
    revised_prompt: str | None = None
    cost_usd: float = 0.0
    request_id: str | None = None
    latency_ms: int = 0
    fallback_rank: int = 0


class EmbeddingRequest(BaseModel):
    """埋め込み生成リクエスト(S12 セマンティック検索。docs/10 §5)。

    テキスト生成(LLMRequest)とは独立した最小の型。``inputs`` は 1 回でまとめて埋め込む
    バッチ。``dimensions`` は OpenAI 系の次元短縮(未指定はモデル既定)。
    """

    model: str
    inputs: list[str]
    dimensions: int | None = None
    timeout_s: float = 60.0
    metadata: dict[str, str] = Field(default_factory=dict)


class EmbeddingResult(BaseModel):
    """埋め込み生成結果。``vectors`` は ``inputs`` と同順・同数、各ベクトルは ``dim`` 次元。"""

    vectors: list[list[float]]
    dim: int = 0
    provider: str = ""
    model: str = ""
    usage: Usage = Field(default_factory=Usage)
    request_id: str | None = None
    latency_ms: int = 0
    fallback_rank: int = 0
