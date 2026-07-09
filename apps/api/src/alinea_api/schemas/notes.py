"""notes エンドポイントの DTO(plans/03 §9・§10.5)。

- ``Note.source`` はチャット昇格(「↑ メモに保存」)の出自を示す(chat_message_id のみ)。
  DB は ``notes.source_chat_message_id``(bigint FK)で保持する。
- ``anchors`` は plans/03 §1.7 の ``AnchorRef``(``display`` はサーバー導出・保存しない。
  chat / annotations スキーマの同型を再利用する)。
- ``NoteCreate.source_message_id`` 指定時、``anchors`` 省略ならサーバーがメッセージの
  根拠アンカー(evidence_anchors)を複写する(§9)。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from alinea_api.schemas.chat import Anchor, AnchorRef


class NoteSource(BaseModel):
    """チャット昇格の出自(plans/03 §9)。"""

    chat_message_id: str


class Note(BaseModel):
    """plans/03 §9 Note(逐語)。"""

    id: str
    content_md: str
    source: NoteSource | None = None
    anchors: list[AnchorRef] = Field(default_factory=list)
    created_at: str
    updated_at: str


class NoteListResponse(BaseModel):
    """GET レスポンス(更新降順・ページングなし。§9)。"""

    items: list[Note]


class NoteCreate(BaseModel):
    """POST /api/library-items/{id}/notes(plans/03 §9)。"""

    content_md: str
    source_message_id: str | None = None
    anchors: list[Anchor] | None = None


class NotePatch(BaseModel):
    """PATCH /api/notes/{note_id}(plans/03 §9)。"""

    content_md: str


class SummarizeToNoteResponse(BaseModel):
    """POST .../summarize-to-note レスポンス(plans/03 §10.5)。"""

    note: Note


__all__ = [
    "Note",
    "NoteCreate",
    "NoteListResponse",
    "NotePatch",
    "NoteSource",
    "SummarizeToNoteResponse",
]
