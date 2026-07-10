"""Typed source candidates used by the arXiv ingest fallback pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest import DocumentCompleteness, assess_document_completeness
from alinea_core.parsing.html_parser import ParsedDocument, parse_arxiv_html
from alinea_core.parsing.latex_parser import (
    LatexParseError,
    extract_latex_archive,
    parse_latex_source,
    select_main_tex,
)
from alinea_core.parsing.pdf_parser import ParsedPdfDocument, PdfParseError, parse_pdf
from alinea_core.storage.s3 import S3Storage, StorageKeys

_LATEX_CANDIDATE_MESSAGES = {
    "empty_archive": "e-print archive is empty",
    "no_main_tex": "no .tex content found in e-print archive",
    "unbalanced_braces": "latex source contains unbalanced braces",
    "unterminated_environment": "latex source contains an unterminated environment",
}


@dataclass
class SourceCandidate:
    """A parsed source that is ready for completeness assessment and persistence."""

    source_format: Literal["latex", "arxiv_html", "pdf"]
    content: DocumentContent
    parsed: ParsedDocument | ParsedPdfDocument
    report: DocumentCompleteness
    source_bytes: bytes
    diagnostics: list[dict[str, Any]]


@dataclass(frozen=True)
class CandidateUnavailable(Exception):  # noqa: N818 - task-defined public API
    """A source-specific failure that permits trying the next candidate."""

    source_format: str
    code: str
    message: str

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, str]:
        return {"format": self.source_format, "code": self.code, "message": self.message}


def parse_latex_candidate(
    source_bytes: bytes, *, pdf_text: str
) -> tuple[SourceCandidate, dict[str, bytes], str]:
    """Parse and assess a LaTeX archive, returning accepted-path figure state too."""

    try:
        extracted = extract_latex_archive(source_bytes)
        main_tex_name, _ = select_main_tex(extracted.text_files)
        parsed = parse_latex_source(main_tex_name, extracted.text_files)
    except LatexParseError as exc:
        message = _LATEX_CANDIDATE_MESSAGES.get(exc.kind, "latex source could not be parsed")
        raise CandidateUnavailable("latex", exc.kind, message) from exc
    except Exception as exc:
        raise CandidateUnavailable("latex", "parse_error", "latex parse failed") from exc

    content = parsed.to_document_content()
    report = assess_document_completeness(
        content,
        pdf_text=pdf_text,
        source_manifest={"binary_files": sorted(extracted.binary_files)},
    )
    candidate = SourceCandidate(
        source_format="latex",
        content=content,
        parsed=parsed,
        report=report,
        source_bytes=source_bytes,
        diagnostics=[],
    )
    return candidate, extracted.binary_files, main_tex_name


def parse_html_candidate(source_bytes: bytes, *, pdf_text: str) -> SourceCandidate:
    """Parse and assess an arXiv HTML candidate."""

    try:
        parsed = parse_arxiv_html(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CandidateUnavailable("arxiv_html", "parse_error", "arxiv html parse failed") from exc
    except Exception as exc:
        raise CandidateUnavailable("arxiv_html", "parse_error", "arxiv html parse failed") from exc

    content = parsed.to_document_content()
    report = assess_document_completeness(
        content,
        pdf_text=pdf_text,
        source_manifest={},
    )
    return SourceCandidate(
        source_format="arxiv_html",
        content=content,
        parsed=parsed,
        report=report,
        source_bytes=source_bytes,
        diagnostics=[],
    )


def parse_pdf_candidate(source_bytes: bytes, *, pdf_text: str) -> SourceCandidate:
    """Parse and assess the retained original PDF candidate."""

    try:
        parsed = parse_pdf(source_bytes)
    except PdfParseError as exc:
        raise CandidateUnavailable("pdf", exc.kind, exc.message) from exc
    except Exception as exc:
        raise CandidateUnavailable("pdf", "parse_error", "pdf parse failed") from exc

    content = parsed.to_document_content()
    report = assess_document_completeness(
        content,
        pdf_text=pdf_text,
        source_manifest={},
    )
    return SourceCandidate(
        source_format="pdf",
        content=content,
        parsed=parsed,
        report=report,
        source_bytes=source_bytes,
        diagnostics=[],
    )


async def load_original_pdf(storage: S3Storage, paper_id: str, source_version: str) -> bytes:
    """Load the canonical original-PDF object for an ingest source version."""

    return await storage.get(
        storage.sources_bucket, StorageKeys.original_pdf(paper_id, source_version)
    )


__all__ = [
    "CandidateUnavailable",
    "SourceCandidate",
    "load_original_pdf",
    "parse_html_candidate",
    "parse_latex_candidate",
    "parse_pdf_candidate",
]
