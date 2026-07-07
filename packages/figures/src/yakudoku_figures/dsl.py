"""全体概要図 DSL(plans/07 §5.1、plans/03 §20.1 ``OverviewFigureDsl`` が正)。

``OverviewFigureDsl`` は永続化・SVG レンダリングの入力形(``footer`` を含み ``evidence`` を
含まない — plans/03 §20.1 の TS 型と同一)。``OverviewFigureDslGenerated`` は LLM 構造化出力の
契約形(``evidence`` を含み ``footer`` を含まない — サーバーが検証後に付与する。plans/07 §5.1)。

このモジュールは pydantic のみに依存し、決定的である(乱数・時刻を含まない)。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CardRole = Literal["problem", "proposal", "result"]
CardTone = Literal["neutral", "accent", "green"]

#: cards[i].role / cards[i].tone の固定順(plans/07 §5.1)。
ROLE_ORDER: tuple[CardRole, CardRole, CardRole] = ("problem", "proposal", "result")
TONE_ORDER: tuple[CardTone, CardTone, CardTone] = ("neutral", "accent", "green")

#: connectors の固定形(plans/07 §5.1)。
FIXED_CONNECTORS: tuple[tuple[int, int], ...] = ((0, 1), (1, 2))

_EVIDENCE_PATTERN = re.compile(r"^(blk|sec)-[A-Za-z0-9-]+$")

OVERVIEW_FIGURE_DSL_SCHEMA_NAME = "overview_figure_dsl_v1"


class Card(BaseModel):
    """課題 / 提案 / 結果のいずれか 1 枚(plans/07 §5.1)。"""

    model_config = ConfigDict(extra="forbid")

    role: CardRole
    label: str = Field(max_length=24)
    heading: str = Field(max_length=36)
    body: str = Field(max_length=80)
    tone: CardTone


class Connector(BaseModel):
    """カード間の矢印(index 参照。plans/07 §5.1)。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: int = Field(alias="from")
    to: int


class OverviewFigureFooter(BaseModel):
    """サーバーが検証後に付与するフッタ(plans/07 §5.1)。モデル出力には含めない。"""

    model_config = ConfigDict(extra="forbid")

    generated_by: str
    date: str


def _check_fixed_shape(cards: list[Card], connectors: list[Connector]) -> None:
    roles = tuple(c.role for c in cards)
    if roles != ROLE_ORDER:
        raise ValueError(f"cards の role 順が不正です(期待={ROLE_ORDER!r}, 実際={roles!r})")
    tones = tuple(c.tone for c in cards)
    if tones != TONE_ORDER:
        raise ValueError(f"cards の tone 順が不正です(期待={TONE_ORDER!r}, 実際={tones!r})")
    conns = tuple((c.from_, c.to) for c in connectors)
    if conns != FIXED_CONNECTORS:
        raise ValueError(
            f"connectors が固定形と一致しません(期待={FIXED_CONNECTORS!r}, 実際={conns!r})"
        )


class OverviewFigureDsl(BaseModel):
    """永続化・SVG レンダリング入力形(``overview_figures.dsl`` / plans/03 §20.1)。"""

    model_config = ConfigDict(extra="forbid")

    layout: Literal["flow-3"] = "flow-3"
    cards: list[Card] = Field(min_length=3, max_length=3)
    connectors: list[Connector] = Field(min_length=2, max_length=2)
    footer: OverviewFigureFooter

    @model_validator(mode="after")
    def _validate_fixed_shape(self) -> OverviewFigureDsl:
        _check_fixed_shape(self.cards, self.connectors)
        return self


class OverviewFigureDslGenerated(BaseModel):
    """LLM 構造化出力の契約形(``overview_figure_dsl_v1``。plans/07 §5.1)。"""

    model_config = ConfigDict(extra="forbid")

    layout: Literal["flow-3"] = "flow-3"
    cards: list[Card] = Field(min_length=3, max_length=3)
    connectors: list[Connector] = Field(min_length=2, max_length=2)
    evidence: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _validate_fixed_shape(self) -> OverviewFigureDslGenerated:
        _check_fixed_shape(self.cards, self.connectors)
        for item in self.evidence:
            if not _EVIDENCE_PATTERN.match(item):
                raise ValueError(f"evidence の形式が不正です: {item!r}")
        return self

    def to_render_dsl(self, *, generated_by: str, date: str) -> OverviewFigureDsl:
        """footer を付与し ``evidence`` を落として永続化形に変換する(plans/07 §5.1)。"""
        return OverviewFigureDsl(
            layout=self.layout,
            cards=list(self.cards),
            connectors=list(self.connectors),
            footer=OverviewFigureFooter(generated_by=generated_by, date=date),
        )


#: LLM 構造化出力用 JSON Schema(逐語。plans/07 §5.1)。
OVERVIEW_FIGURE_DSL_JSON_SCHEMA: dict[str, Any] = {
    "$id": "https://yakudoku.app/schemas/overview_figure_dsl_v1.json",
    "type": "object",
    "additionalProperties": False,
    "required": ["layout", "cards", "connectors"],
    "properties": {
        "layout": {"const": "flow-3"},
        "cards": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["role", "label", "heading", "body", "tone"],
                "properties": {
                    "role": {"enum": ["problem", "proposal", "result"]},
                    "label": {"type": "string", "maxLength": 24},
                    "heading": {"type": "string", "maxLength": 36},
                    "body": {"type": "string", "maxLength": 80},
                    "tone": {"enum": ["neutral", "accent", "green"]},
                },
            },
        },
        "connectors": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["from", "to"],
                "properties": {"from": {"enum": [0, 1]}, "to": {"enum": [1, 2]}},
            },
        },
        "evidence": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "pattern": "^(blk|sec)-[A-Za-z0-9-]+$"},
        },
    },
}


__all__ = [
    "FIXED_CONNECTORS",
    "OVERVIEW_FIGURE_DSL_JSON_SCHEMA",
    "OVERVIEW_FIGURE_DSL_SCHEMA_NAME",
    "ROLE_ORDER",
    "TONE_ORDER",
    "Card",
    "CardRole",
    "CardTone",
    "Connector",
    "OverviewFigureDsl",
    "OverviewFigureDslGenerated",
    "OverviewFigureFooter",
]
