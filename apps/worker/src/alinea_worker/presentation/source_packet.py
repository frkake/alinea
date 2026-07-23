"""Grounded source packet for paper->PPTX planning (Task 29, Step 2).

The source packet is the *only* paper-derived material the planning LLM sees.
It is assembled purely from a ``Paper`` and one ``DocumentRevision`` (plus the
set of figure asset keys that were already fetched during ingest). It contains:

- the bibliography (title / authors / venue / year / arXiv id / license),
- the structured body as revision-anchored text blocks,
- section headings,
- equations (LaTeX kept verbatim as editable text),
- figure/table captions and numbers, and
- a flag per figure for whether its raster asset was already fetched.

It deliberately excludes notes, annotations, highlights, chat, generated
articles, translations, and any API key. Because the builder only ever receives
``paper`` + ``revision``, those private objects cannot leak into the packet even
in principle -- the privacy boundary is structural, not a filter.

Budgets (design §4): whole packet <= 120,000 chars, single block <= 12,000
chars, figures <= 20. Sections are prioritised abstract -> introduction ->
method -> results -> limitations -> conclusion so the most load-bearing
material survives truncation.
"""

from __future__ import annotations

from typing import Any, Protocol

from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.document.plaintext import block_to_plain, inline_to_plain
from pydantic import BaseModel, Field

# Budgets (design doc §4 / brief Step 2).
MAX_PACKET_CHARS = 120_000
MAX_BLOCK_CHARS = 12_000
MAX_FIGURES = 20

# Section-title priority (lowercased, order matters). Unknown sections keep
# document order but rank after every prioritised section.
_SECTION_PRIORITY = (
    "abstract",
    "introduction",
    "intro",
    "method",
    "methods",
    "approach",
    "result",
    "results",
    "experiment",
    "experiments",
    "limitation",
    "limitations",
    "conclusion",
    "conclusions",
    "discussion",
)


class RevisionLike(Protocol):
    """Minimal shape the builder needs from a ``DocumentRevision``."""

    id: str
    content: dict[str, Any]


class PacketBlock(BaseModel):
    """One revision-anchored source block (paper body / equation)."""

    anchor: str
    block_id: str
    section_id: str
    kind: str
    text: str


class PacketSection(BaseModel):
    """A section heading anchor + title (revision-fixed)."""

    anchor: str
    section_id: str
    number: str
    title: str


class PacketFigure(BaseModel):
    """A figure/table represented by its number + caption (+ asset presence)."""

    figure_id: str
    block_id: str
    kind: str  # "figure" | "table"
    number: str
    caption: str
    has_asset: bool


class SourcePacket(BaseModel):
    """Paper-only material for the planning LLM. No private data fields exist."""

    revision_id: str
    bibliography: str
    sections: list[PacketSection] = Field(default_factory=list)
    blocks: list[PacketBlock] = Field(default_factory=list)
    figures: list[PacketFigure] = Field(default_factory=list)

    # -- anchor helpers ----------------------------------------------------- #
    @property
    def anchor_ids(self) -> list[str]:
        return [s.anchor for s in self.sections] + [b.anchor for b in self.blocks]

    @property
    def figure_ids(self) -> list[str]:
        return [f.figure_id for f in self.figures]

    def anchor_for(self, block_or_section_id: str) -> str:
        return f"{self.revision_id}:{block_or_section_id}"

    def has_anchor(self, anchor: str) -> bool:
        return anchor in set(self.anchor_ids)

    def has_figure(self, figure_id: str) -> bool:
        return figure_id in set(self.figure_ids)


def _bibliography(paper: Any) -> str:
    authors = ", ".join(
        str(a.get("name", "")) if isinstance(a, dict) else str(a)
        for a in (paper.authors or [])
    ).strip(", ")
    year = paper.published_on.year if getattr(paper, "published_on", None) else "年不明"
    venue = paper.venue or "(venue 不明)"
    arxiv = paper.arxiv_id or "(arXiv ID 不明)"
    return (
        f"タイトル: {paper.title}\n"
        f"著者: {authors or '著者不明'}\n"
        f"venue: {venue} ({year})\n"
        f"arXiv: {arxiv}\n"
        f"ライセンス: {paper.license}"
    )


def _section_rank(section: Section) -> int:
    title = (section.heading.title or "").strip().lower()
    for rank, keyword in enumerate(_SECTION_PRIORITY):
        if keyword in title:
            return rank
    return len(_SECTION_PRIORITY)


def _block_text(block: Block) -> str:
    if block.type == "equation":
        latex = (block.latex or "").strip()
        return latex
    return block_to_plain(block)


def _figure_caption(block: Block) -> str:
    return inline_to_plain(block.caption)


def build_source_packet(
    *,
    paper: Any,
    revision: RevisionLike,
    fetched_figure_keys: set[str] | None = None,
) -> SourcePacket:
    """Assemble the grounded, paper-only source packet.

    ``fetched_figure_keys`` is the set of figure ``asset_key`` values whose bytes
    were actually fetched during ingest; a figure whose key is absent (or which
    has no ``asset_key`` at all) is kept as number + caption only so planning can
    still reference it as a text figure.
    """

    fetched = fetched_figure_keys or set()
    revision_id = str(revision.id)
    content = DocumentContent.model_validate(revision.content)

    # Order sections by priority, then original document order (stable sort).
    ordered_sections = sorted(
        enumerate(content.sections), key=lambda item: (_section_rank(item[1]), item[0])
    )

    sections: list[PacketSection] = []
    figures: list[PacketFigure] = []
    # Collect prose/equation blocks in priority order for budget-aware selection.
    candidate_blocks: list[PacketBlock] = []

    def _anchor(local_id: str) -> str:
        return f"{revision_id}:{local_id}"

    def _walk(section: Section) -> None:
        sections.append(
            PacketSection(
                anchor=_anchor(section.id),
                section_id=section.id,
                number=section.heading.number or "",
                title=section.heading.title or "",
            )
        )
        for block in section.blocks:
            if block.type in ("figure", "table"):
                figures.append(
                    PacketFigure(
                        figure_id=_anchor(block.id),
                        block_id=block.id,
                        kind=block.type,
                        number=(block.number or "").strip(),
                        caption=_figure_caption(block),
                        has_asset=bool(block.asset_key and block.asset_key in fetched),
                    )
                )
                continue
            if block.type == "reference_entry":
                continue
            text = _block_text(block)
            if not text:
                continue
            if len(text) > MAX_BLOCK_CHARS:
                text = text[:MAX_BLOCK_CHARS]
            candidate_blocks.append(
                PacketBlock(
                    anchor=_anchor(block.id),
                    block_id=block.id,
                    section_id=section.id,
                    kind=block.type,
                    text=text,
                )
            )
        for sub in section.sections:
            _walk(sub)

    for _index, section in ordered_sections:
        _walk(section)

    # Enforce the whole-packet character budget by dropping the lowest-priority
    # (latest, least-central) blocks first. Selection preserves priority order.
    selected: list[PacketBlock] = []
    used = 0
    for block in candidate_blocks:
        cost = len(block.text)
        if selected and used + cost > MAX_PACKET_CHARS:
            continue
        selected.append(block)
        used += cost
        if used >= MAX_PACKET_CHARS:
            break

    # Re-order the surviving blocks back into document order for a coherent read.
    document_order = {b.id: i for i, (_s, b) in enumerate(content.iter_blocks())}
    selected.sort(key=lambda b: document_order.get(b.block_id, 0))

    figures = figures[:MAX_FIGURES]

    return SourcePacket(
        revision_id=revision_id,
        bibliography=_bibliography(paper),
        sections=sections,
        blocks=selected,
        figures=figures,
    )


__all__ = [
    "MAX_BLOCK_CHARS",
    "MAX_FIGURES",
    "MAX_PACKET_CHARS",
    "PacketBlock",
    "PacketFigure",
    "PacketSection",
    "RevisionLike",
    "SourcePacket",
    "build_source_packet",
]
