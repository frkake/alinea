"""settings エンドポイントの DTO と検証(plans/03 §17・plans/04 §11)。

- ``FullSettings`` は §17.1 の全キーを型・値域つきで表現する(不正値は Pydantic の
  ValidationError → ルータで 422 ``validation_error`` に変換)。
- ``DEFAULTS`` は §17.1 の既定値の逐語。GET/PATCH とも「既定値を含む完全形」を返すため、
  保存済みのユーザー上書き(``users.settings`` JSONB)へ deep merge して用いる。
- BYOK は平文再表示なし(§17.3)。キー一覧・クォータのレスポンス型を定義する。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# provider 値域(§17.1 / §17.3)。
TextProvider = Literal["openai", "anthropic", "google", "deepseek"]
ImageProvider = Literal["openai", "google", "xai"]
KeyProvider = Literal["openai", "anthropic", "google", "deepseek", "xai"]


def _stepped(value: float, lo: float, hi: float, step: float) -> bool:
    if value < lo or value > hi:
        return False
    n = (value - lo) / step
    return abs(n - round(n)) <= 1e-6


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DisplaySettings(_Strict):
    theme: Literal["light", "dark", "system"] = "system"
    accent: Literal["#3E5C76", "#4A6B57", "#6E5A7E", "#7A5C48"] = "#3E5C76"
    body_font: Literal["serif", "sans"] = "serif"
    font_size_px: float = 16.5
    line_height: float = 2.15
    content_width_px: int = 720

    @field_validator("font_size_px")
    @classmethod
    def _v_font(cls, v: float) -> float:
        if not _stepped(v, 14, 20, 0.5):
            raise ValueError("font_size_px は 14-20(0.5 刻み)")
        return v

    @field_validator("line_height")
    @classmethod
    def _v_line(cls, v: float) -> float:
        if not _stepped(v, 1.6, 2.4, 0.05):
            raise ValueError("line_height は 1.6-2.4(0.05 刻み)")
        return v

    @field_validator("content_width_px")
    @classmethod
    def _v_width(cls, v: int) -> int:
        if not _stepped(v, 600, 840, 20):
            raise ValueError("content_width_px は 600-840(20 刻み)")
        return v


class TranslationSettings(_Strict):
    default_style: Literal["natural", "literal"] = "natural"
    auto_translate_appendix: bool = True
    translate_table_cells: bool = True
    suggest_section_selection_over_30_pages: bool = False


class ReadingSettings(_Strict):
    track_reading_time: bool = True
    status_transition: Literal["auto", "suggest", "off"] = "suggest"


class ChatSettings(_Strict):
    include_annotations_and_notes: bool = True


class NotificationsSettings(_Strict):
    translation_complete: bool = True
    status_suggestion: bool = True
    deadline_reminder: bool = True


class ExtensionSettings(_Strict):
    arxiv_inline_button: bool = False


class RouteEntry(_Strict):
    provider: TextProvider
    model: str = Field(min_length=1)


class ImageRouteEntry(_Strict):
    provider: ImageProvider
    model: str = Field(min_length=1)


class LlmRouting(_Strict):
    translation: RouteEntry = RouteEntry(provider="deepseek", model="deepseek-v4-flash")
    retranslation: RouteEntry = RouteEntry(provider="anthropic", model="claude-opus-4-8")
    chat: RouteEntry = RouteEntry(provider="anthropic", model="claude-opus-4-8")
    summary: RouteEntry = RouteEntry(provider="anthropic", model="claude-opus-4-8")
    article: RouteEntry = RouteEntry(provider="anthropic", model="claude-opus-4-8")
    vocab: RouteEntry = RouteEntry(provider="anthropic", model="claude-haiku-4-5")
    figure_dsl: RouteEntry = RouteEntry(provider="anthropic", model="claude-opus-4-8")
    figure_image: ImageRouteEntry = ImageRouteEntry(
        provider="google", model="gemini-3.1-flash-image"
    )
    overview_figure_raster_mode: bool = False


class FullSettings(_Strict):
    display: DisplaySettings = DisplaySettings()
    translation: TranslationSettings = TranslationSettings()
    reading: ReadingSettings = ReadingSettings()
    chat: ChatSettings = ChatSettings()
    notifications: NotificationsSettings = NotificationsSettings()
    extension: ExtensionSettings = ExtensionSettings()
    llm_routing: LlmRouting = LlmRouting()


# §17.1 の既定値の逐語(deep merge のベース)。
DEFAULTS: dict[str, Any] = FullSettings().model_dump()


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """再帰 deep merge(dict 同士は再帰・それ以外は patch で置換)。base は変更しない。"""
    out = dict(base)
    for key, value in patch.items():
        cur = out.get(key)
        if isinstance(cur, dict) and isinstance(value, dict):
            out[key] = deep_merge(cur, value)
        else:
            out[key] = value
    return out


# --- BYOK / quota レスポンス型(§17.3 / §17.4) ----------------------------------


class ApiKeyItem(BaseModel):
    provider: str
    masked: str
    status: str
    last_tested_at: str | None = None
    created_at: str


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyItem]


class ApiKeyPutBody(BaseModel):
    api_key: Annotated[str, Field(min_length=1)]


class ApiKeyPutResponse(BaseModel):
    provider: str
    masked: str
    created_at: str


class QuotaCounter(BaseModel):
    used: int
    limit: int


class QuotaUsage(BaseModel):
    translation_papers: QuotaCounter
    chat_messages: QuotaCounter
    images: QuotaCounter
    article_generations: QuotaCounter
    vocab_generations: QuotaCounter


class ByokActive(BaseModel):
    text: bool
    image: bool


class QuotaResponse(BaseModel):
    period: str
    byok_active: ByokActive
    usage: QuotaUsage
