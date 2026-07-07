"""figures スキーマ(plans/03 §20、全体概要図・解説図)。

``OverviewFigureRef``(§20.1)は ``overview_figures`` の現行版を表す DTO。``dsl`` は
``yakudoku_figures.overview_svg.render_overview_svg`` にそのまま渡せる形(plans/07 §5.1)。
``evidence``(``{display, anchor}[]``)は DB ``evidence_anchors`` 列(``{ref,display,anchor}[]``
— :func:`yakudoku_core.article.build_evidence_wire` の出力)から ``ref`` を落として写す。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CardRole = Literal["problem", "proposal", "result"]
CardTone = Literal["neutral", "accent", "green"]


class AnchorRefOut(BaseModel):
    revision_id: str
    block_id: str
    start: int | None = None
    end: int | None = None
    quote: str | None = None
    side: str = "source"
    display: str


class OverviewFigureEvidenceItemOut(BaseModel):
    display: str
    anchor: AnchorRefOut


class OverviewFigureCardOut(BaseModel):
    role: CardRole
    label: str
    heading: str
    body: str
    tone: CardTone


class OverviewFigureConnectorOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: int = Field(alias="from")
    to: int


class OverviewFigureFooterOut(BaseModel):
    generated_by: str
    date: str


class OverviewFigureDslOut(BaseModel):
    layout: Literal["flow-3"] = "flow-3"
    cards: list[OverviewFigureCardOut]
    connectors: list[OverviewFigureConnectorOut]
    footer: OverviewFigureFooterOut


class OverviewFigureRefOut(BaseModel):
    id: str
    version: int
    generated_at: str
    svg_url: str
    raster_url: str | None
    evidence: list[OverviewFigureEvidenceItemOut]
    dsl: OverviewFigureDslOut


class OverviewFigureVersionItemOut(BaseModel):
    version: int
    generated_at: str


class OverviewFigureGetOut(OverviewFigureRefOut):
    versions: list[OverviewFigureVersionItemOut]


class OverviewFigureRewriteRequest(BaseModel):
    instruction: str | None = None


class FigureJobResponse(BaseModel):
    job_id: str


class ExplainerFigureRegenerateRequest(BaseModel):
    instruction: str | None = None


__all__ = [
    "AnchorRefOut",
    "CardRole",
    "CardTone",
    "ExplainerFigureRegenerateRequest",
    "FigureJobResponse",
    "OverviewFigureCardOut",
    "OverviewFigureConnectorOut",
    "OverviewFigureDslOut",
    "OverviewFigureEvidenceItemOut",
    "OverviewFigureFooterOut",
    "OverviewFigureGetOut",
    "OverviewFigureRefOut",
    "OverviewFigureRewriteRequest",
    "OverviewFigureVersionItemOut",
]
