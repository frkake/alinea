"""chat スキーマ(plans/03 §10・§1.7)。逐語の型に一致させる。

- `ChatThread` / `ChatMessage` / `MessageBlock` / `QuickAction` は plans/03 §10.2 の TS 型と同型。
- `Anchor` / `AnchorRef` は plans/03 §1.7(`display` はサーバー導出・保存しない)。
- P1 忠実性: aside ブロックの `label`("論文外の知識"/"推測")と `evidence` 根拠チップ、
  assistant ロールが「AI生成」明示のためのメタデータ(docs/05 §6・plans/03 §10)。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from alinea_api.errors import Problem

# plans/03 §10.2 QuickAction(常設5 + 入力候補2 + 導線3)。
QuickAction = Literal[
    "summary_3line",
    "beginner_explain",
    "contributions_limits",
    "experiment_setup",
    "implementation_points",
    "detailed_summary",
    "explain_equation",
    "explain_figure",
    "expert_summary",
    "related_work_position",
]

AnchorSide = Literal["source", "translation"]


class Anchor(BaseModel):
    """共通位置参照(plans/03 §1.7 / plans/02 §3.1 AnchorJson)。"""

    revision_id: str
    block_id: str
    start: int | None = None
    end: int | None = None
    quote: str | None = None
    side: AnchorSide = "source"


class AnchorRef(Anchor):
    """サーバー導出の短縮表記 `display` を伴う Anchor(plans/03 §1.7)。"""

    display: str


class EvidenceRef(BaseModel):
    """markdown ブロック内インライン根拠(`[[ev:n]]` トークンに対応)。"""

    ref: int
    display: str
    anchor: AnchorRef


class MarkdownBlock(BaseModel):
    type: Literal["markdown"] = "markdown"
    text: str  # インライン根拠は "[[ev:1]]" トークン
    evidence: list[EvidenceRef] = Field(default_factory=list)


class AsideBlock(BaseModel):
    type: Literal["aside"] = "aside"
    label: Literal["outside_knowledge", "speculation"]
    text: str


MessageBlock = MarkdownBlock | AsideBlock


class ChatThread(BaseModel):
    id: str
    title: str
    is_main: bool
    message_count: int
    last_message_at: str | None = None


class ChatThreadListResponse(BaseModel):
    items: list[ChatThread]


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    blocks: list[MessageBlock]
    context_anchors: list[AnchorRef] = Field(default_factory=list)
    quick_action: QuickAction | None = None
    status: Literal["complete", "error"]  # 失敗回答も残す(P3)
    error: Problem | None = None
    created_at: str


class ChatMessageListResponse(BaseModel):
    items: list[ChatMessage]
    next_cursor: str | None = None


class ThreadCreateRequest(BaseModel):
    title: str


class ThreadPatchRequest(BaseModel):
    title: str


class SendMessageRequest(BaseModel):
    content: str = ""  # quick_action 指定時は空文字可
    context_anchors: list[Anchor] = Field(default_factory=list)
    quick_action: QuickAction | None = None


class RegenerateRequest(BaseModel):
    content: str | None = None  # 省略=同一質問


__all__ = [
    "Anchor",
    "AnchorRef",
    "AsideBlock",
    "ChatMessage",
    "ChatMessageListResponse",
    "ChatThread",
    "ChatThreadListResponse",
    "EvidenceRef",
    "MarkdownBlock",
    "MessageBlock",
    "QuickAction",
    "RegenerateRequest",
    "SendMessageRequest",
    "ThreadCreateRequest",
    "ThreadPatchRequest",
]
