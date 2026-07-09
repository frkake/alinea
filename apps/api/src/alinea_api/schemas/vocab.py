"""vocab エンドポイントの DTO(plans/03 §11・§1.6・§1.7、docs/11)。

- ``VocabKind`` / ``ReviewResult`` は plans/03 §1.6 の列挙(逐語)。
- ``anchor`` は plans/03 §1.7 の ``Anchor`` / ``AnchorRef``(``display`` はサーバー導出・
  保存しない。chat スキーマの同型を再利用する)。
- DB ``generation_status``(pending/complete/failed)と API ``generation``
  (pending/done/failed)は名称が異なる(§11.1)。ルータ層で写像する。
- ``VocabEntryDetail.ai.context_meaning`` は DB ``meaning_short``/``meaning_long`` の 2 列を
  1 オブジェクトにまとめたもの(plans/03 §11.3)。未生成(両列とも空文字)は null。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from alinea_api.schemas.chat import Anchor, AnchorRef

VocabKind = Literal["word", "collocation", "idiom"]
GenerationState = Literal["pending", "done", "failed"]
ReviewResult = Literal["again", "good"]

# plans/07 §7.2 の生成フィールド(kind 含む 9 種)。DB カラム名と 1:1。
GENERATION_FIELDS: tuple[str, ...] = (
    "kind",
    "pos_label",
    "ipa",
    "meaning_short",
    "meaning_long",
    "interpretation",
    "etymology",
    "mnemonic",
    "related_forms",
)


class VocabHighlight(BaseModel):
    start: int
    end: int


class VocabSource(BaseModel):
    """出典表示(plans/03 §11.1。「Rectified Flow · §2.1」)。"""

    library_item_id: str
    paper_title: str
    display: str


class VocabEntrySummary(BaseModel):
    """plans/03 §11.1 VocabEntrySummary。"""

    id: str
    kind: VocabKind
    term: str
    meaning_short: str | None = None
    source: VocabSource
    added_at: str
    generation: GenerationState


class VocabMeaning(BaseModel):
    short: str
    long: str


class VocabAi(BaseModel):
    """plans/03 §11.3 の ``ai`` オブジェクト。"""

    context_meaning: VocabMeaning | None = None
    interpretation: str | None = None
    etymology: str | None = None
    mnemonic: str | None = None
    related_expressions: str | None = None
    edited_fields: list[str] = Field(default_factory=list)
    generation_error: str | None = None


class VocabReviewHistoryEntry(BaseModel):
    result: ReviewResult
    at: str


class VocabSrs(BaseModel):
    """plans/03 §11.3 の ``srs`` オブジェクト(docs/11 §7.1)。"""

    stage: Literal[1, 2, 3, 4, 5]
    next_review_at: str | None  # null = 習得済み
    review_count: int
    history: list[VocabReviewHistoryEntry]


class VocabEntryDetail(VocabEntrySummary):
    """plans/03 §11.3 VocabEntryDetail。"""

    pos_label: str | None = None
    ipa: str | None = None
    anchor: AnchorRef
    context_sentence: str
    highlight: VocabHighlight
    ai: VocabAi
    srs: VocabSrs


class VocabCounts(BaseModel):
    """plans/03 §11.1 counts(フィルタに関わらない語彙帳全体の総数)。"""

    all: int
    word: int
    collocation: int
    idiom: int
    due: int


class VocabListResponse(BaseModel):
    items: list[VocabEntrySummary]
    next_cursor: str | None
    total: int
    counts: VocabCounts


class VocabCreate(BaseModel):
    """POST /api/vocab(plans/03 §11.2「語彙に追加」)。"""

    library_item_id: str
    term: str
    anchor: Anchor  # side="source" 必須
    context_sentence: str
    highlight: VocabHighlight


class VocabCreateResponse(BaseModel):
    entry: VocabEntryDetail
    generation_job_id: str


class VocabDuplicateExisting(BaseModel):
    vocab_id: str


class VocabPatchAi(BaseModel):
    """plans/03 §11.4 ``ai`` 部分(指定フィールドのみ編集)。"""

    context_meaning: VocabMeaning | None = None
    interpretation: str | None = None
    etymology: str | None = None
    mnemonic: str | None = None
    related_expressions: str | None = None


class VocabPatch(BaseModel):
    """PATCH /api/vocab/{vocab_id}(plans/03 §11.4)。"""

    kind: VocabKind | None = None
    term: str | None = None
    pos_label: str | None = None
    ipa: str | None = None
    ai: VocabPatchAi | None = None


class VocabRegenerateRequest(BaseModel):
    """POST /api/vocab/{vocab_id}/regenerate(plans/03 §11.6)。省略時は未編集フィールド全部。"""

    fields: list[str] | None = None


class VocabRegenerateResponse(BaseModel):
    job_id: str


class VocabReviewQueueResponse(BaseModel):
    """GET /api/vocab/review-queue(plans/03 §11.7)。"""

    items: list[VocabEntryDetail]
    total: int


class VocabReviewRequest(BaseModel):
    """POST /api/vocab/{vocab_id}/review(plans/03 §11.8)。"""

    result: ReviewResult


class VocabReviewResponse(BaseModel):
    srs: VocabSrs
    next_review_display: str


__all__ = [
    "GENERATION_FIELDS",
    "GenerationState",
    "ReviewResult",
    "VocabAi",
    "VocabCounts",
    "VocabCreate",
    "VocabCreateResponse",
    "VocabDuplicateExisting",
    "VocabEntryDetail",
    "VocabEntrySummary",
    "VocabHighlight",
    "VocabKind",
    "VocabListResponse",
    "VocabMeaning",
    "VocabPatch",
    "VocabPatchAi",
    "VocabRegenerateRequest",
    "VocabRegenerateResponse",
    "VocabReviewHistoryEntry",
    "VocabReviewQueueResponse",
    "VocabReviewRequest",
    "VocabReviewResponse",
    "VocabSource",
    "VocabSrs",
]
