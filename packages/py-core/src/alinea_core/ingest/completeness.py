"""Pure completeness checks for structured ingest candidates."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.translation.pipeline import TRANSLATABLE_BLOCK_TYPES

_PARAGRAPH_TYPES = frozenset({"paragraph", "list", "quote", "theorem"})


def _is_bare_pdf_reference(plain: str, manifest_files: Collection[str]) -> bool:
    """Return whether an entire visible block unambiguously names a manifest PDF."""
    candidate = plain.strip()
    if not candidate:
        return False

    manifest_pdf_paths = {
        str(PurePosixPath(name)) for name in manifest_files if name.lower().endswith(".pdf")
    }
    manifest_pdf_basenames = {PurePosixPath(name).name for name in manifest_pdf_paths}
    normalized_candidate = str(PurePosixPath(candidate))

    if normalized_candidate in manifest_pdf_paths or normalized_candidate in manifest_pdf_basenames:
        return True
    return not any(char.isspace() for char in candidate) and (
        PurePosixPath(normalized_candidate).name in manifest_pdf_basenames
    )


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
    if unresolved_figures < 0:
        raise ValueError("unresolved_figures must be non-negative")

    blocks = [block for _section, block in content.iter_blocks()]
    visible_blocks = []
    for block in blocks:
        if block.type not in TRANSLATABLE_BLOCK_TYPES:
            continue
        plain = block_to_plain(block)
        if plain:
            visible_blocks.append((block, plain))
    visible = "\n".join(plain for _block, plain in visible_blocks)
    stripped_pdf_text = pdf_text.strip()
    paragraph_count = sum(block.type in _PARAGRAPH_TYPES for block in blocks)
    visible_paragraph_count = sum(
        block.type in _PARAGRAPH_TYPES for block, _plain in visible_blocks
    )
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
    elif isinstance(binary_files, Mapping):
        binary_files = binary_files.keys()
    elif not isinstance(binary_files, list | tuple | set | frozenset):
        binary_files = ()
    manifest_files = tuple(name for name in binary_files if isinstance(name, str))

    if 0 < len(visible_blocks) <= 3 and all(
        _is_bare_pdf_reference(plain, manifest_files) for _block, plain in visible_blocks
    ):
        return report(False, "embedded_pdf_wrapper")
    if unresolved_figures > 0:
        return report(False, "figure_asset_unresolved")
    if len(stripped_pdf_text) >= 1_000 and len(visible) * 100 < len(stripped_pdf_text) * 35:
        return report(False, "document_incomplete")

    accepted = bool(visible) and (
        visible_paragraph_count >= 2
        or any(block.type == "heading" for block, _plain in visible_blocks)
    )
    return report(accepted, None if accepted else "document_incomplete")
