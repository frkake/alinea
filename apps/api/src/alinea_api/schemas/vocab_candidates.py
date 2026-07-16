"""AI 単語抽出(S7)エンドポイントの DTO。

docs/superpowers/specs/2026-07-16-ai-word-extraction-design.md。

- 候補は ``vocab_candidates`` の pending 行。accept で本物の ``vocab_entries`` を作る。
- ``anchor`` / ``source`` は vocab スキーマと同型(サーバー導出の ``display`` を伴う)。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from alinea_api.schemas.chat import AnchorRef
from alinea_api.schemas.vocab import VocabHighlight, VocabSource

VocabKind = Literal["word", "collocation", "idiom"]


class VocabCandidateOut(BaseModel):
    id: str
    term: str
    kind: VocabKind
    reason: str | None = None
    context_sentence: str
    highlight: VocabHighlight
    anchor: AnchorRef
    source: VocabSource
    created_at: str


class VocabCandidateListResponse(BaseModel):
    items: list[VocabCandidateOut]
    count: int


class VocabExtractResponse(BaseModel):
    job_id: str


class VocabCandidateAcceptResponse(BaseModel):
    vocab_id: str
    generation_job_id: str | None = None
    already_existed: bool = False
