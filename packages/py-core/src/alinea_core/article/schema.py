"""記事構造 JSON スキーマ(plans/07 §4.3・§4.8、モデル出力の完全形)。

``ARTICLE_V1_JSON_SCHEMA`` / ``ARTICLE_BLOCK_V1_JSON_SCHEMA`` は
:class:`alinea_llm.types.JsonSchemaSpec` にそのまま渡す draft 2020-12 の JSON Schema。
``ArticleV1Model`` / ``ArticleBlockModel`` はモデル出力(検証済み JSON)を型付きで扱うための
Pydantic モデルで、JSON Schema 検証の後段(型アクセス用)として使う。

**モデル出力のフィールド名**(このモジュールの型)と**DB 保存形**(:mod:`alinea_core.article.
postprocess` が書く ``article_blocks.content``)は異なる(後者は
:func:`alinea_core.document.plaintext.article_block_to_plain` が要求するフラットな
キー名に合わせる)。相互変換は postprocess 側の責務。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# blocks.items.type の値域(plans/07 §4.3)。attribution はサーバーが自動挿入するため
# モデル出力には含まれない。
ArticleBlockType = Literal[
    "heading",
    "paragraph",
    "quote_source",
    "figure_embed",
    "explainer_figure",
    "discussion",
]

_EVIDENCE_PATTERN = r"^(blk|sec)-[A-Za-z0-9-]+$"

# §4.3 の blocks.items の JSON Schema(逐語)。article_v1 / article_block_v1(§4.8)で共有する。
ARTICLE_BLOCK_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "type",
        "heading",
        "markdown",
        "quote",
        "figure",
        "explainer",
        "discussion",
        "evidence",
    ],
    "properties": {
        "type": {
            "enum": [
                "heading",
                "paragraph",
                "quote_source",
                "figure_embed",
                "explainer_figure",
                "discussion",
            ]
        },
        "heading": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["level", "text"],
                    "properties": {
                        "level": {"enum": [2, 3]},
                        "text": {"type": "string", "maxLength": 60},
                    },
                },
                {"type": "null"},
            ]
        },
        "markdown": {"anyOf": [{"type": "string", "maxLength": 4000}, {"type": "null"}]},
        "quote": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["block_id", "text_en"],
                    "properties": {
                        "block_id": {"type": "string", "pattern": "^blk-"},
                        "text_en": {"type": "string", "maxLength": 400},
                    },
                },
                {"type": "null"},
            ]
        },
        "figure": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["block_id", "caption_ja"],
                    "properties": {
                        "block_id": {"type": "string", "pattern": "^blk-"},
                        "caption_ja": {"type": "string", "maxLength": 300},
                    },
                },
                {"type": "null"},
            ]
        },
        "explainer": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["slot", "image_brief_en", "caption_ja"],
                    "properties": {
                        "slot": {"enum": [0, 1]},
                        "image_brief_en": {"type": "string", "maxLength": 500},
                        "caption_ja": {"type": "string", "maxLength": 300},
                    },
                },
                {"type": "null"},
            ]
        },
        "discussion": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 6,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["text", "origin", "annotation_id"],
                                "properties": {
                                    "text": {"type": "string", "maxLength": 200},
                                    "origin": {"enum": ["ai", "user_highlight"]},
                                    "annotation_id": {
                                        "anyOf": [{"type": "string"}, {"type": "null"}]
                                    },
                                },
                            },
                        },
                    },
                },
                {"type": "null"},
            ]
        },
        "evidence": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "pattern": _EVIDENCE_PATTERN},
        },
    },
}

# §4.3 完全形(記事全体)。
ARTICLE_V1_JSON_SCHEMA: dict[str, Any] = {
    "$id": "https://alinea.app/schemas/article_v1.json",
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "blocks"],
    "properties": {
        "title": {"type": "string", "maxLength": 60},
        "blocks": {
            "type": "array",
            "minItems": 8,
            "maxItems": 60,
            "items": ARTICLE_BLOCK_ITEM_SCHEMA,
        },
    },
}

# §4.8 ブロック単体の再生成用スキーマ(name: "article_block_v1")。
ARTICLE_BLOCK_V1_JSON_SCHEMA: dict[str, Any] = ARTICLE_BLOCK_ITEM_SCHEMA

ARTICLE_V1_SCHEMA_NAME = "article_v1"
ARTICLE_BLOCK_V1_SCHEMA_NAME = "article_block_v1"

# type -> 対応する content フィールド名(§4.3「対応必須化」)。
TYPE_TO_FIELD: dict[str, str] = {
    "heading": "heading",
    "paragraph": "markdown",
    "quote_source": "quote",
    "figure_embed": "figure",
    "explainer_figure": "explainer",
    "discussion": "discussion",
}


class HeadingContent(BaseModel):
    level: Literal[2, 3]
    text: str


class QuoteContent(BaseModel):
    block_id: str
    text_en: str


class FigureContent(BaseModel):
    block_id: str
    caption_ja: str


class ExplainerContent(BaseModel):
    slot: Literal[0, 1]
    image_brief_en: str
    caption_ja: str


class DiscussionItem(BaseModel):
    text: str
    origin: Literal["ai", "user_highlight"]
    annotation_id: str | None = None


class DiscussionContent(BaseModel):
    items: list[DiscussionItem] = Field(default_factory=list)


class ArticleBlockModel(BaseModel):
    """モデル出力 1 ブロック(§4.3 blocks.items / §4.8 article_block_v1)。"""

    type: ArticleBlockType
    heading: HeadingContent | None = None
    markdown: str | None = None
    quote: QuoteContent | None = None
    figure: FigureContent | None = None
    explainer: ExplainerContent | None = None
    discussion: DiscussionContent | None = None
    evidence: list[str] = Field(default_factory=list)

    def has_required_field(self) -> bool:
        """type ⇄ content の対応が取れているか(§4.3)。"""
        field = TYPE_TO_FIELD.get(self.type)
        return field is not None and getattr(self, field) is not None


class ArticleV1Model(BaseModel):
    """モデル出力(記事全体。§4.3 article_v1)。"""

    title: str
    blocks: list[ArticleBlockModel] = Field(default_factory=list)


__all__ = [
    "ARTICLE_BLOCK_V1_JSON_SCHEMA",
    "ARTICLE_BLOCK_V1_SCHEMA_NAME",
    "ARTICLE_V1_JSON_SCHEMA",
    "ARTICLE_V1_SCHEMA_NAME",
    "TYPE_TO_FIELD",
    "ArticleBlockModel",
    "ArticleBlockType",
    "ArticleV1Model",
    "DiscussionContent",
    "DiscussionItem",
    "ExplainerContent",
    "FigureContent",
    "HeadingContent",
    "QuoteContent",
]
