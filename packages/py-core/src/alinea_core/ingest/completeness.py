"""Pure completeness checks for structured ingest candidates."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.translation.pipeline import TRANSLATABLE_BLOCK_TYPES

_PARAGRAPH_TYPES = frozenset({"paragraph", "list", "quote", "theorem"})
_SCHEME_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _is_local_archive_relative_path(value: str) -> bool:
    """Return whether a path stays in the logical, relative archive namespace."""
    candidate = value.strip()
    return (
        bool(candidate)
        and "\\" not in value
        and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        and not PurePosixPath(candidate).is_absolute()
        and _SCHEME_PREFIX_RE.match(candidate) is None
    )


def _is_bare_pdf_reference(plain: str, manifest_files: Collection[str]) -> bool:
    """Return whether an entire visible block unambiguously names a manifest PDF."""
    candidate = plain.strip()
    if not _is_local_archive_relative_path(plain):
        return False

    manifest_pdf_paths = {
        str(PurePosixPath(name))
        for name in manifest_files
        if name.lower().endswith(".pdf") and _is_local_archive_relative_path(name)
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
    """構造化候補の完全性評価。

    ``code`` は ``accepted`` が ``False`` のときは不採用理由
    (``embedded_pdf_wrapper`` / ``document_incomplete``)、``True`` のときは
    ``None`` または情報コード ``figure_assets_degraded``
    (``unresolved_figures`` 件の図アセットが未解決のまま採用された、という
    診断情報)のいずれかを表す。図アセットの未解決は候補・文書全体を不採用に
    しない(P3: 黙って壊れない) — 未解決分はブロック単位で縮退させる。
    """

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
    source_char_count: int | None = None,
    source_manifest: Mapping[str, Any],
    unresolved_figures: int = 0,
) -> DocumentCompleteness:
    """Assess whether a structured candidate contains a complete, usable document."""
    if unresolved_figures < 0:
        raise ValueError("unresolved_figures must be non-negative")
    if source_char_count is not None and (
        type(source_char_count) is not int or source_char_count < 0
    ):
        raise ValueError("source_char_count must be a non-negative integer")

    blocks = [block for _section, block in content.iter_blocks()]
    visible_blocks = []
    recovered_plain: list[str] = []
    for block in blocks:
        plain = block_to_plain(block)
        if not plain:
            continue
        # Completeness measures source recovery, not translation eligibility.
        # References, equations, code, and algorithms intentionally remain outside
        # the translation plan, but still preserve visible source text and must not
        # make an otherwise complete document look truncated.
        recovered_plain.append(plain)
        if block.type in TRANSLATABLE_BLOCK_TYPES:
            visible_blocks.append((block, plain))
    visible = "\n".join(plain for _block, plain in visible_blocks)
    recovered = "\n".join(recovered_plain)
    stripped_pdf_text = pdf_text.strip()
    reported_source_chars = source_char_count if source_char_count is not None else len(pdf_text)
    coverage_source_chars = (
        source_char_count if source_char_count is not None else len(stripped_pdf_text)
    )
    paragraph_count = sum(block.type in _PARAGRAPH_TYPES for block in blocks)
    visible_paragraph_count = sum(
        block.type in _PARAGRAPH_TYPES for block, _plain in visible_blocks
    )
    figure_count = sum(block.type == "figure" for block in blocks)

    def report(accepted: bool, code: str | None) -> DocumentCompleteness:
        return DocumentCompleteness(
            accepted=accepted,
            code=code,
            source_chars=reported_source_chars,
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
    if coverage_source_chars >= 1_000 and len(recovered) * 100 < coverage_source_chars * 35:
        return report(False, "document_incomplete")

    accepted = bool(visible) and (
        visible_paragraph_count >= 2
        or any(block.type == "heading" for block, _plain in visible_blocks)
    )
    if not accepted:
        return report(False, "document_incomplete")
    # 図アセットの一部が未解決でも、原文自体は完結しているため文書全体は不採用にしない
    # (P3: 黙って壊れない)。未解決の図はブロック単位で縮退させ、診断用に件数と
    # 情報コードだけを報告に残す。呼び出し側(worker)は figure_asset_failures を
    # revision の stats/joblog に記録し、キャプション/本文はそのまま保持する。
    if unresolved_figures > 0:
        return report(True, "figure_assets_degraded")
    return report(True, None)
