"""annotations エンドポイントの DTO(plans/03 §8・§1.7)。

- 公開 API の ``kind`` は ``highlight | bookmark`` の 2 値(§8.1)。「コメント」は
  ``highlight`` + ``comment`` で表現する。DB(0001)は ``kind IN
  ('highlight','comment','bookmark')`` の 3 値で保持するため、ルータ層で相互写像する
  (``highlight`` + comment → DB ``comment``、出力は再び ``highlight`` + comment)。
- ``anchor`` は plans/03 §1.7 の ``Anchor`` / ``AnchorRef``(``display`` はサーバー導出・
  保存しない)。chat スキーマの同型を再利用する。
- ID は既存実装(library/jobs)に合わせ生 UUID 文字列で返す。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from alinea_api.schemas.chat import Anchor, AnchorRef

AnnColor = Literal["important", "question", "idea", "term"]
AnnKind = Literal["highlight", "bookmark"]


class Annotation(BaseModel):
    """plans/03 §8.1 Annotation。"""

    id: str
    kind: AnnKind
    color: AnnColor | None = None  # bookmark は null
    anchor: AnchorRef  # bookmark はセクション参照(start/end=null)
    comment: str | None = None  # 「コメント」= highlight + comment
    placed: bool  # false=リアンカー失敗(未配置)
    created_at: str
    updated_at: str


class AnnotationCounts(BaseModel):
    """plans/03 §8.1 counts(フィルタに関わらず論文全体の総数)。"""

    all: int
    important: int
    question: int
    idea: int
    term: int
    with_comment: int
    unplaced: int


class AnnotationListResponse(BaseModel):
    items: list[Annotation]
    counts: AnnotationCounts


class AnnotationCreate(BaseModel):
    """POST /api/library-items/{id}/annotations(plans/03 §8.2)。"""

    kind: AnnKind
    color: AnnColor | None = None  # highlight は必須(欠落は 422)
    anchor: Anchor
    comment: str | None = None


class AnnotationPatch(BaseModel):
    """PATCH /api/annotations/{annotation_id}(plans/03 §8.3)。

    ``model_fields_set`` で「未指定」と「明示 null」を区別する。comment に null/空文字を
    与えると「コメント」を解除して純ハイライトに戻す(§5.5・§5.6)。
    """

    color: AnnColor | None = None
    comment: str | None = None


__all__ = [
    "AnnColor",
    "Annotation",
    "AnnotationCounts",
    "AnnotationCreate",
    "AnnotationKind",
    "AnnotationListResponse",
    "AnnotationPatch",
]

AnnotationKind = AnnKind
