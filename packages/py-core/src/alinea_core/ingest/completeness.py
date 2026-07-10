"""Pure completeness checks for structured ingest candidates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.translation.pipeline import TRANSLATABLE_BLOCK_TYPES

_PARAGRAPH_TYPES = frozenset({"paragraph", "list", "quote", "theorem"})


@dataclass(frozen=True)
class DocumentCompleteness:
    accepted: bool
    code: str | None
    source_chars: int
    structured_chars: int
    paragraph_count: int
    figure_count: int
    unresolved_figures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_document_completeness(
    content: DocumentContent,
    *,
    pdf_text: str,
    source_manifest: Mapping[str, Any],
    unresolved_figures: int = 0,
) -> DocumentCompleteness:
    """Assess whether a structured candidate contains a complete, usable document."""
    blocks = [block for _section, block in content.iter_blocks()]
    visible = "\n".join(
        block_to_plain(block) for block in blocks if block.type in TRANSLATABLE_BLOCK_TYPES
    ).strip()
    stripped_pdf_text = pdf_text.strip()
    paragraph_count = sum(block.type in _PARAGRAPH_TYPES for block in blocks)
    figure_count = sum(block.type == "figure" for block in blocks)

    def report(accepted: bool, code: str | None) -> DocumentCompleteness:
        return DocumentCompleteness(
            accepted=accepted,
            code=code,
            source_chars=len(pdf_text),
            structured_chars=len(visible),
            paragraph_count=paragraph_count,
            figure_count=figure_count,
            unresolved_figures=unresolved_figures,
        )

    binary_files = source_manifest.get("binary_files", ())
    if isinstance(binary_files, str):
        binary_files = (binary_files,)
    if not isinstance(binary_files, list | tuple | set | frozenset):
        binary_files = ()
    binary_pdfs = {
        Path(name).name
        for name in binary_files
        if isinstance(name, str) and name.lower().endswith(".pdf")
    }

    if len(blocks) <= 3 and visible in binary_pdfs:
        return report(False, "embedded_pdf_wrapper")
    if unresolved_figures > 0:
        return report(False, "figure_asset_unresolved")
    if len(stripped_pdf_text) >= 1_000 and len(visible) * 100 < len(stripped_pdf_text) * 35:
        return report(False, "document_incomplete")

    accepted = bool(visible) and (
        paragraph_count >= 2 or any(block.type == "heading" for block in blocks)
    )
    return report(accepted, None if accepted else "document_incomplete")
