"""Source-packet privacy boundary + grounding tests (Task 29, Step 1-2).

The source packet is the ONLY paper-derived material handed to the planning
LLM. It must contain bibliography, structured body, section headings,
equations, figure/table captions, and (metadata for) already-fetched figure
assets -- and NOTHING else. Notes, annotations, highlights, chat, article,
translation, and BYOK keys must never appear in the serialized packet.

These tests build the packet purely (no DB, no network) from an in-memory
``Paper`` + ``DocumentContent`` so the boundary is proven structurally: the
builder is only ever given paper + revision, so private objects created
alongside them can never leak into ``packet.model_dump_json()``.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

from alinea_core.db.models import Annotation, ChatMessage, Note, Paper, TranslationUnit
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_worker.presentation.source_packet import (
    MAX_BLOCK_CHARS,
    MAX_FIGURES,
    MAX_PACKET_CHARS,
    RevisionLike,
    SourcePacket,
    build_source_packet,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "presentation" / "paper_document.json"


def _uid() -> str:
    return str(uuid.uuid4())


def _paper() -> Paper:
    return Paper(
        id=_uid(),
        arxiv_id="2209.03003",
        title="Rectified Flow",
        authors=[{"name": "Xingchao Liu"}, {"name": "Qiang Liu"}],
        venue="ICLR 2023",
        published_on=dt.date(2022, 9, 7),
        license="cc-by-4.0",
        visibility="public",
    )


def _fixture_content() -> DocumentContent:
    return DocumentContent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _revision(content: DocumentContent) -> RevisionLike:
    # Lightweight stand-in for DocumentRevision (build_source_packet only reads
    # ``id`` and ``content``); avoids a DB round-trip for the pure packet tests.
    from typing import cast
    from types import SimpleNamespace

    return cast(RevisionLike, SimpleNamespace(id=_uid(), content=content.model_dump()))


# --------------------------------------------------------------------------- #
# Step 2: grounded packet contents
# --------------------------------------------------------------------------- #
def test_packet_includes_bibliography_body_headings_equations_captions() -> None:
    paper = _paper()
    revision = _revision(_fixture_content())

    packet = build_source_packet(paper=paper, revision=revision)

    assert isinstance(packet, SourcePacket)
    assert packet.revision_id == revision.id
    # Bibliography.
    assert "Rectified Flow" in packet.bibliography
    assert "Xingchao Liu" in packet.bibliography
    assert "ICLR 2023" in packet.bibliography
    # Structured body (source text).
    body = " ".join(b.text for b in packet.blocks)
    assert "straight-line transport" in body
    assert "reflow procedure" in body
    # Section headings.
    titles = {s.title for s in packet.sections}
    assert {"Introduction", "Method", "Results", "Limitations", "Conclusion"} <= titles
    # Equation LaTeX is carried as a block.
    assert any(b.kind == "equation" and "v(z_t, t)" in b.text for b in packet.blocks)
    # Figure/table captions with numbers.
    captions = {f.caption for f in packet.figures}
    assert any("Straightened trajectories" in c for c in captions)
    assert any("Comparison of FID" in c for c in captions)


def test_packet_anchors_are_revision_fixed_and_resolvable() -> None:
    paper = _paper()
    revision = _revision(_fixture_content())

    packet = build_source_packet(paper=paper, revision=revision)

    # Every block/section anchor is namespaced by the revision id.
    for anchor in packet.anchor_ids:
        assert anchor.startswith(f"{revision.id}:")
    # Figure ids are revision-fixed too and disjoint-resolvable.
    for figure_id in packet.figure_ids:
        assert figure_id.startswith(f"{revision.id}:")
    assert packet.anchor_for("blk-2-1") == f"{revision.id}:blk-2-1"


def test_packet_figure_without_bytes_keeps_number_and_caption() -> None:
    paper = _paper()
    revision = _revision(_fixture_content())

    # Only figure 1's asset was fetched; figure 2 and the table have no bytes.
    packet = build_source_packet(
        paper=paper,
        revision=revision,
        fetched_figure_keys={"figures/paper/rev/blk-fig-1.png"},
    )

    by_id = {f.block_id: f for f in packet.figures}
    assert by_id["blk-fig-1"].has_asset is True
    assert by_id["blk-fig-1"].number == "1"
    # Figure 2 had no asset_key/bytes: kept as number + caption only.
    assert by_id["blk-fig-2"].has_asset is False
    assert by_id["blk-fig-2"].caption == "Qualitative single-step samples."
    # Table 1 (no bytes) is still represented with its caption.
    assert by_id["blk-tab-1"].has_asset is False
    assert "Comparison of FID" in by_id["blk-tab-1"].caption


# --------------------------------------------------------------------------- #
# Step 2: budgets
# --------------------------------------------------------------------------- #
def test_packet_truncates_oversized_single_block() -> None:
    paper = _paper()
    huge = "本文" * (MAX_BLOCK_CHARS)  # far exceeds the per-block cap
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[Block(id="blk-1", type="paragraph", inlines=[Inline(t="text", v=huge)])],
            )
        ],
    )
    revision = _revision(content)

    packet = build_source_packet(paper=paper, revision=revision)

    assert len(packet.blocks) == 1
    assert len(packet.blocks[0].text) <= MAX_BLOCK_CHARS


def test_packet_enforces_total_char_budget() -> None:
    paper = _paper()
    # Many medium blocks whose combined length far exceeds the packet budget.
    block_text = "x" * 4000
    blocks = [
        Block(id=f"blk-{i}", type="paragraph", inlines=[Inline(t="text", v=block_text)])
        for i in range(80)
    ]
    content = DocumentContent(
        quality_level="A",
        sections=[Section(id="sec-1", heading=SectionHeading(title="Body"), blocks=blocks)],
    )
    revision = _revision(content)

    packet = build_source_packet(paper=paper, revision=revision)

    total = sum(len(b.text) for b in packet.blocks)
    assert total <= MAX_PACKET_CHARS
    assert len(packet.blocks) < 80  # some blocks were dropped to fit the budget


def test_packet_caps_figure_count() -> None:
    paper = _paper()
    figures = [
        Block(
            id=f"blk-fig-{i}",
            type="figure",
            number=str(i),
            caption=[Inline(t="text", v=f"Figure {i} caption")],
        )
        for i in range(1, 40)
    ]
    content = DocumentContent(
        quality_level="A",
        sections=[Section(id="sec-1", heading=SectionHeading(title="Figures"), blocks=figures)],
    )
    revision = _revision(content)

    packet = build_source_packet(paper=paper, revision=revision)

    assert len(packet.figures) <= MAX_FIGURES


# --------------------------------------------------------------------------- #
# Step 1: privacy boundary — sentinels never appear in the serialized packet
# --------------------------------------------------------------------------- #
def test_packet_never_contains_private_sentinels() -> None:
    paper = _paper()
    revision = _revision(_fixture_content())

    note_secret = f"NOTE-SENTINEL-{uuid.uuid4().hex}"
    annotation_secret = f"ANNOTATION-SENTINEL-{uuid.uuid4().hex}"
    chat_secret = f"CHAT-SENTINEL-{uuid.uuid4().hex}"
    translation_secret = f"TRANSLATION-SENTINEL-{uuid.uuid4().hex}"
    api_key = f"sk-BYOK-SENTINEL-{uuid.uuid4().hex}"

    # These private objects live *alongside* the paper but are never handed to
    # ``build_source_packet``; the packet is built solely from paper + revision.
    _private = [
        Note(id=_uid(), library_item_id=_uid(), title="secret", body_md=note_secret),
        Annotation(
            id=_uid(),
            library_item_id=_uid(),
            kind="comment",
            body=annotation_secret,
            anchor={"block_id": "blk-1-1", "quote": annotation_secret},
        ),
        ChatMessage(thread_id=_uid(), role="user", content={"segments": [{"md": chat_secret}]}),
        TranslationUnit(
            set_id=_uid(),
            block_id="blk-1-1",
            source_hash="h",
            content_ja={"kind": "text"},
            text_ja=translation_secret,
        ),
    ]

    packet = build_source_packet(paper=paper, revision=revision)
    serialized = packet.model_dump_json()

    for secret in (note_secret, annotation_secret, chat_secret, translation_secret, api_key):
        assert secret not in serialized


def test_packet_declared_fields_are_only_paper_material() -> None:
    # The packet's public schema must not declare any private-data field.
    fields = set(SourcePacket.model_fields)
    forbidden = {"notes", "annotations", "highlights", "chat", "article", "translation", "api_key"}
    assert fields.isdisjoint(forbidden)
