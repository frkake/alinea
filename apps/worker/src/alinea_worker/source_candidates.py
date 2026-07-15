"""Typed source candidates used by the arXiv ingest fallback pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import multiprocessing as mp
import os
import re
import select
import struct
import sys
import threading
import time
import unicodedata
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from pathlib import PurePosixPath
from typing import Any, Literal

import structlog
from alinea_core.arxiv.limits import MAX_ARXIV_PDF_BYTES
from alinea_core.document.blocks import BLOCK_TYPES, Block, DocumentContent, Section
from alinea_core.document.inlines import Inline
from alinea_core.ingest import DocumentCompleteness, assess_document_completeness
from alinea_core.parsing.html_parser import ParsedDocument, parse_arxiv_html
from alinea_core.parsing.latex_parser import (
    LatexParseError,
    extract_latex_archive,
    parse_latex_source,
    select_main_tex,
)
from alinea_core.parsing.pdf_parser import (
    MAX_PDF_EXTRACTED_CHARS,
    ParsedPdfDocument,
    PdfParseError,
    PdfTextEvidenceCounts,
    count_pdf_text_evidence,
    parse_pdf,
)
from alinea_core.parsing.pdf_parser import (
    PARSER_VERSION as PDF_PARSER_VERSION,
)
from alinea_core.storage.s3 import S3Storage, StorageKeys
from pydantic import ValidationError

from alinea_worker.figure_assets import MAX_ASSET_BYTES, FigureAssetPayload, extract_graphicspaths

log = structlog.get_logger("alinea.worker.pdf_ocr")

_LATEX_CANDIDATE_MESSAGES = {
    "archive_expanded_too_large": "e-print archive expands beyond the safe limit",
    "archive_member_limit": "e-print archive has too many members",
    "archive_member_too_large": "e-print archive member exceeds the safe limit",
    "archive_too_large": "e-print archive exceeds the safe input limit",
    "empty_archive": "e-print archive is empty",
    "invalid_archive": "e-print archive is invalid",
    "no_main_tex": "no .tex content found in e-print archive",
    "unbalanced_braces": "latex source contains unbalanced braces",
    "unterminated_environment": "latex source contains an unterminated environment",
}
_SCHEME_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_OCR_LANGUAGE_RE = re.compile(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*\Z")
PDF_OCR_CANDIDATE_VERSION = "pdf-ocr-1.0.0"
PDF_OCR_IPC_VERSION = "pdf-ocr-ipc-1"
PDF_TEXT_EVIDENCE_IPC_VERSION = "pdf-text-evidence-ipc-1"
MAX_PDF_OCR_SECONDS = 300.0
MAX_PDF_OCR_OUTPUT_BYTES = 160 * 1024 * 1024
MAX_PDF_OCR_MEMORY_BYTES = 768 * 1024 * 1024
MAX_PDF_OCR_METADATA_BYTES = 16 * 1024 * 1024
MAX_PDF_OCR_ERROR_BYTES = 4 * 1024
MAX_PDF_TEXT_EVIDENCE_METADATA_BYTES = 1024
MAX_PDF_OCR_FIGURES = 200
MAX_PDF_OCR_FIGURE_BYTES = 128 * 1024 * 1024
MAX_PDF_OCR_JSON_DEPTH = 64
MAX_PDF_OCR_JSON_CONTAINERS = 200_000
MAX_PDF_OCR_JSON_CONTAINER_ITEMS = 200_000
MAX_PDF_OCR_JSON_TOKENS = 1_000_000
MAX_PDF_OCR_JSON_STRING_CHARS = 1_000_000
MAX_PDF_OCR_JSON_NUMBER_CHARS = 64
MAX_PDF_OCR_SECTIONS = 20_000
MAX_PDF_OCR_BLOCKS = 200_000
MAX_PDF_OCR_INLINES = 1_000_000
MAX_PDF_OCR_EXTRACTED_CHARS = 20_000_000
MAX_PDF_OCR_PAGES = 2_000
MAX_PDF_OCR_IDENTIFIER_CHARS = 256
MAX_PDF_OCR_WARNINGS = 10_000
_PDF_OCR_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_.:-]+\Z")
_PDF_OCR_NUMBER_RE = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z"
)
_PDF_PARSE_ERROR_CODES = frozenset(
    {
        "no_text_layer",
        "pdf_block_limit",
        "pdf_crashed",
        "pdf_figure_bytes_limit",
        "pdf_figure_limit",
        "pdf_geometry_limit",
        "pdf_layout_limit",
        "pdf_open_error",
        "pdf_output_too_large",
        "pdf_page_limit",
        "pdf_section_limit",
        "pdf_text_error",
        "pdf_text_limit",
    }
)
_PDF_OCR_ERROR_CODES = frozenset(
    {
        *_PDF_PARSE_ERROR_CODES,
        "ocr_crashed",
        "ocr_engine_unavailable",
        "ocr_failed",
        "ocr_language_invalid",
        "ocr_language_unavailable",
        "ocr_output_too_large",
        "ocr_platform_unsupported",
        "ocr_readiness_timeout",
        "ocr_timeout",
    }
)
_PDF_TEXT_EVIDENCE_ERROR_CODES = frozenset(
    {
        "pdf_crashed",
        "pdf_geometry_limit",
        "pdf_open_error",
        "pdf_output_too_large",
        "pdf_page_limit",
        "pdf_text_error",
        "pdf_text_limit",
    }
)
_CANCELLED_PDF_OCR_RESULT = object()
_PDF_OCR_ADMISSION_LOCK = threading.Lock()
_PDF_SUBPROCESS_START_GATE = threading.BoundedSemaphore(value=1)


@dataclass(frozen=True)
class _PdfOcrAdmission:
    limit: int
    semaphore: asyncio.Semaphore


_PDF_OCR_ADMISSIONS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, _PdfOcrAdmission
] = weakref.WeakKeyDictionary()


def _pdf_ocr_admission(limit: int) -> _PdfOcrAdmission:
    loop = asyncio.get_running_loop()
    with _PDF_OCR_ADMISSION_LOCK:
        admission = _PDF_OCR_ADMISSIONS.get(loop)
        if admission is None:
            admission = _PdfOcrAdmission(limit=limit, semaphore=asyncio.Semaphore(limit))
            _PDF_OCR_ADMISSIONS[loop] = admission
        elif admission.limit != limit:
            raise CandidateUnavailable(
                "pdf_ocr",
                "ocr_lifecycle",
                "PDF OCR admission limit changed within a running worker",
            )
        return admission


def _clear_pdf_ocr_admission_for_tests() -> None:
    with _PDF_OCR_ADMISSION_LOCK:
        _PDF_OCR_ADMISSIONS.clear()


@dataclass(frozen=True)
class _PdfOcrImageManifestEntry:
    block_id: str
    size: int
    sha256: str


@dataclass(frozen=True)
class _PdfOcrDecodedMetadata:
    parsed: ParsedPdfDocument
    manifest: tuple[_PdfOcrImageManifestEntry, ...]


class _PdfOcrJsonError(ValueError):
    pass


@dataclass
class SourceCandidate:
    """A parsed source that is ready for completeness assessment and persistence."""

    source_format: Literal["latex", "arxiv_html", "pdf"]
    content: DocumentContent
    parsed: ParsedDocument | ParsedPdfDocument
    report: DocumentCompleteness
    source_bytes: bytes
    diagnostics: list[dict[str, Any]]
    source_manifest: dict[str, Any] = field(default_factory=dict)
    graphicspaths: tuple[str, ...] = ()
    latex_binary_files: dict[str, bytes] = field(default_factory=dict, repr=False)
    latex_main_tex_name: str | None = None
    container_source_bytes: bytes | None = field(default=None, repr=False)
    materialized_figures: dict[str, FigureAssetPayload] = field(default_factory=dict, repr=False)
    figure_asset_failures: list[dict[str, str]] = field(default_factory=list)
    figure_materialization_validated: bool = False


@dataclass
class CandidateUnavailable(Exception):  # noqa: N818 - task-defined public API
    """A source-specific failure that permits trying the next candidate."""

    source_format: str
    code: str
    message: str
    retry_after_s: int | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, str]:
        return {"format": self.source_format, "code": self.code, "message": self.message}


def _pdf_protocol_error(source_format: str, message: str) -> CandidateUnavailable:
    code = "ocr_crashed" if source_format == "pdf_ocr" else "pdf_crashed"
    return CandidateUnavailable(source_format, code, message)


def _pdf_ocr_protocol_error(message: str) -> CandidateUnavailable:
    return _pdf_protocol_error("pdf_ocr", message)


def _pdf_output_too_large(source_format: str, message: str) -> CandidateUnavailable:
    code = "ocr_output_too_large" if source_format == "pdf_ocr" else "pdf_output_too_large"
    return CandidateUnavailable(source_format, code, message)


def _pdf_ocr_output_too_large(message: str) -> CandidateUnavailable:
    return _pdf_output_too_large("pdf_ocr", message)


def _preflight_pdf_ocr_json(payload: bytes, *, source_format: str = "pdf_ocr") -> str:
    if len(payload) > MAX_PDF_OCR_METADATA_BYTES:
        raise _pdf_output_too_large(source_format, "PDF metadata exceeds the safe limit")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _pdf_protocol_error(
            source_format, "PDF subprocess returned invalid metadata"
        ) from exc

    stack: list[list[Any]] = []
    containers = 0
    tokens = 0
    index = 0
    length = len(text)

    def charge_token() -> None:
        nonlocal tokens
        tokens += 1
        if tokens > MAX_PDF_OCR_JSON_TOKENS:
            raise _PdfOcrJsonError("too many JSON tokens")

    try:
        while index < length:
            char = text[index]
            if char in " \t\r\n":
                index += 1
                continue
            if char in "[{":
                charge_token()
                containers += 1
                if containers > MAX_PDF_OCR_JSON_CONTAINERS:
                    raise _PdfOcrJsonError("too many JSON containers")
                stack.append([char, 1])
                if len(stack) > MAX_PDF_OCR_JSON_DEPTH:
                    raise _PdfOcrJsonError("JSON nesting is too deep")
                index += 1
                continue
            if char in "]}":
                charge_token()
                expected = "[" if char == "]" else "{"
                if not stack or stack[-1][0] != expected:
                    raise _PdfOcrJsonError("mismatched JSON container")
                stack.pop()
                index += 1
                continue
            if char == ",":
                charge_token()
                if not stack:
                    raise _PdfOcrJsonError("JSON separator is outside a container")
                stack[-1][1] += 1
                if stack[-1][1] > MAX_PDF_OCR_JSON_CONTAINER_ITEMS:
                    raise _PdfOcrJsonError("JSON container has too many items")
                index += 1
                continue
            if char == ":":
                charge_token()
                index += 1
                continue
            if char == '"':
                charge_token()
                index += 1
                string_start = index
                raw_chars = 0
                while index < length:
                    current = text[index]
                    if current == '"':
                        break
                    if ord(current) < 0x20:
                        raise _PdfOcrJsonError("JSON string contains a control character")
                    if current == "\\":
                        index += 1
                        if index >= length or text[index] not in '"\\/bfnrtu':
                            raise _PdfOcrJsonError("JSON string escape is invalid")
                        if text[index] == "u":
                            if index + 4 >= length or any(
                                item not in "0123456789abcdefABCDEF"
                                for item in text[index + 1 : index + 5]
                            ):
                                raise _PdfOcrJsonError("JSON unicode escape is invalid")
                            index += 4
                    raw_chars = index - string_start + 1
                    if raw_chars > MAX_PDF_OCR_JSON_STRING_CHARS:
                        raise _PdfOcrJsonError("JSON string is too long")
                    index += 1
                if index >= length:
                    raise _PdfOcrJsonError("JSON string is unterminated")
                index += 1
                continue
            if char == "-" or char.isdigit():
                charge_token()
                start = index
                index += 1
                while index < length and text[index] not in " \t\r\n,]}":
                    index += 1
                token = text[start:index]
                if (
                    len(token) > MAX_PDF_OCR_JSON_NUMBER_CHARS
                    or _PDF_OCR_NUMBER_RE.fullmatch(token) is None
                ):
                    raise _PdfOcrJsonError("JSON number is invalid")
                continue
            matched_literal = next(
                (
                    literal
                    for literal in ("true", "false", "null")
                    if text.startswith(literal, index)
                ),
                None,
            )
            if matched_literal is None:
                raise _PdfOcrJsonError("JSON token is invalid")
            charge_token()
            index += len(matched_literal)
        if stack:
            raise _PdfOcrJsonError("JSON container is unterminated")
    except _PdfOcrJsonError as exc:
        raise _pdf_protocol_error(
            source_format, "PDF subprocess returned invalid metadata"
        ) from exc
    return text


def _reject_pdf_ocr_json_constant(value: str) -> Any:
    raise _PdfOcrJsonError(f"unsupported JSON constant: {value}")


def _pdf_ocr_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _PdfOcrJsonError("duplicate JSON object key")
        result[key] = value
    return result


def _exact_pdf_ocr_object(value: Any, keys: set[str] | frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise _PdfOcrJsonError("JSON object schema mismatch")
    return value


def _bounded_pdf_ocr_string(value: Any, *, maximum: int = MAX_PDF_OCR_JSON_STRING_CHARS) -> str:
    if type(value) is not str or len(value) > maximum:
        raise _PdfOcrJsonError("JSON string field is invalid")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
        raise _PdfOcrJsonError("JSON string contains an unsafe Unicode character")
    return value


def _optional_pdf_ocr_string(value: Any) -> str | None:
    if value is None:
        return None
    return _bounded_pdf_ocr_string(value)


def _pdf_ocr_integer(value: Any, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _PdfOcrJsonError("JSON integer field is invalid")
    return value


def _pdf_ocr_number(value: Any, *, minimum: float, maximum: float) -> float:
    if type(value) not in {int, float}:
        raise _PdfOcrJsonError("JSON number field is invalid")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise _PdfOcrJsonError("JSON number field is out of range")
    return result


_PDF_OCR_INLINE_KEYS = frozenset({"t", "v", "ref", "kind", "href"})
_PDF_OCR_BLOCK_KEYS = frozenset(
    {
        "id",
        "type",
        "inlines",
        "level",
        "number",
        "title",
        "label",
        "asset_key",
        "caption",
        "latex",
        "language",
        "code",
        "ordered",
        "items",
        "raw",
        "structured",
        "page",
        "bbox",
    }
)
_PDF_OCR_SECTION_KEYS = frozenset({"id", "heading", "blocks", "sections"})
_PDF_OCR_HEADING_KEYS = frozenset({"number", "title"})
_PDF_OCR_STATS_KEYS = frozenset(
    {
        "pages",
        "ocr",
        "extracted_chars",
        "figures",
        "tables",
        "blocks",
        "columns",
        "pdf_sync_rate",
        "figure_caption_match_rate",
        "equation_latex_rate",
    }
)
_PDF_OCR_STRUCTURED_REFERENCE_KEYS = frozenset({"arxiv_id", "year", "title", "doi"})
_PDF_OCR_DISPLAY_ASSET_TYPES = frozenset({"figure", "table", "equation"})
_PDF_CHILD_BLOCK_TYPES = frozenset(
    {"paragraph", "heading", "figure", "table", "equation", "reference_entry"}
)


def _validate_pdf_ocr_identifier(value: Any) -> str:
    identifier = _bounded_pdf_ocr_string(value, maximum=MAX_PDF_OCR_IDENTIFIER_CHARS)
    if not identifier or _PDF_OCR_IDENTIFIER_RE.fullmatch(identifier) is None:
        raise _PdfOcrJsonError("PDF OCR identifier is invalid")
    return identifier


def _validate_pdf_ocr_inline(value: Any, counters: dict[str, int]) -> Inline:
    raw = _exact_pdf_ocr_object(value, _PDF_OCR_INLINE_KEYS)
    counters["inlines"] += 1
    if counters["inlines"] > MAX_PDF_OCR_INLINES:
        raise _PdfOcrJsonError("PDF OCR inline limit exceeded")
    if raw["t"] != "text":
        raise _PdfOcrJsonError("PDF child inline type is invalid")
    _bounded_pdf_ocr_string(raw["v"])
    if any(raw[key] is not None for key in ("ref", "kind", "href")):
        raise _PdfOcrJsonError("PDF child inline fields are invalid")
    try:
        return Inline.model_validate(raw)
    except ValidationError as exc:
        raise _PdfOcrJsonError("PDF OCR inline model is invalid") from exc


def _validate_pdf_child_block_semantics(raw: dict[str, Any]) -> None:
    block_type = raw["type"]
    if block_type not in _PDF_CHILD_BLOCK_TYPES:
        raise _PdfOcrJsonError("PDF child block type is invalid")
    if raw["asset_key"] is not None:
        raise _PdfOcrJsonError("PDF child asset key is invalid")

    empty_list_fields = {
        key for key in ("inlines", "caption", "items") if raw[key]
    }
    non_null_fields = {
        key
        for key in (
            "level",
            "number",
            "title",
            "label",
            "latex",
            "language",
            "code",
            "ordered",
            "raw",
            "structured",
        )
        if raw[key] is not None
    }
    allowed_lists: set[str]
    allowed_values: set[str]
    if block_type == "paragraph":
        if not raw["inlines"]:
            raise _PdfOcrJsonError("PDF paragraph text is missing")
        allowed_lists = {"inlines"}
        allowed_values = set()
    elif block_type == "heading":
        if raw["level"] is None or raw["title"] is None:
            raise _PdfOcrJsonError("PDF heading fields are incomplete")
        allowed_lists = set()
        allowed_values = {"level", "number", "title"}
    elif block_type in {"figure", "table"}:
        allowed_lists = {"caption"}
        allowed_values = {"number"}
        if block_type == "table":
            allowed_values.add("raw")
    elif block_type == "equation":
        allowed_lists = set()
        allowed_values = {"number"}
    else:
        if raw["label"] is None or raw["raw"] is None:
            raise _PdfOcrJsonError("PDF reference fields are incomplete")
        allowed_lists = set()
        allowed_values = {"label", "raw", "structured"}
    if not empty_list_fields.issubset(allowed_lists) or not non_null_fields.issubset(
        allowed_values
    ):
        raise _PdfOcrJsonError("PDF child block fields are inconsistent")


def _validate_pdf_ocr_block(
    value: Any,
    *,
    pages: int,
    counters: dict[str, int],
    block_ids: set[str],
    display_asset_ids: set[str],
) -> Block:
    raw = _exact_pdf_ocr_object(value, _PDF_OCR_BLOCK_KEYS)
    counters["blocks"] += 1
    if counters["blocks"] > MAX_PDF_OCR_BLOCKS:
        raise _PdfOcrJsonError("PDF OCR block limit exceeded")
    block_id = _validate_pdf_ocr_identifier(raw["id"])
    if block_id in block_ids:
        raise _PdfOcrJsonError("duplicate PDF OCR block id")
    block_ids.add(block_id)
    if raw["type"] not in BLOCK_TYPES:
        raise _PdfOcrJsonError("PDF OCR block type is invalid")
    if raw["type"] in _PDF_OCR_DISPLAY_ASSET_TYPES:
        display_asset_ids.add(block_id)
    for key in ("number", "title", "label", "asset_key", "latex", "language", "code", "raw"):
        _optional_pdf_ocr_string(raw[key])
    if raw["level"] is not None:
        _pdf_ocr_integer(raw["level"], minimum=1, maximum=16)
    if raw["ordered"] is not None and type(raw["ordered"]) is not bool:
        raise _PdfOcrJsonError("PDF OCR ordered field is invalid")
    if not isinstance(raw["inlines"], list) or not isinstance(raw["caption"], list):
        raise _PdfOcrJsonError("PDF OCR inline collection is invalid")
    raw["inlines"] = [_validate_pdf_ocr_inline(item, counters) for item in raw["inlines"]]
    raw["caption"] = [_validate_pdf_ocr_inline(item, counters) for item in raw["caption"]]
    if not isinstance(raw["items"], list):
        raise _PdfOcrJsonError("PDF OCR list items are invalid")
    validated_items: list[list[Inline]] = []
    for item in raw["items"]:
        if not isinstance(item, list):
            raise _PdfOcrJsonError("PDF OCR list item is invalid")
        validated_items.append([_validate_pdf_ocr_inline(inline, counters) for inline in item])
    raw["items"] = validated_items
    structured = raw["structured"]
    if structured is not None:
        if type(structured) is not dict or not set(structured).issubset(
            _PDF_OCR_STRUCTURED_REFERENCE_KEYS
        ):
            raise _PdfOcrJsonError("PDF OCR structured reference is invalid")
        for item in structured.values():
            _bounded_pdf_ocr_string(item)
    _pdf_ocr_integer(raw["page"], minimum=1, maximum=pages)
    bbox = raw["bbox"]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise _PdfOcrJsonError("PDF OCR bbox is invalid")
    coordinates = [_pdf_ocr_number(item, minimum=0.0, maximum=100_000.0) for item in bbox]
    if coordinates[0] >= coordinates[2] or coordinates[1] >= coordinates[3]:
        raise _PdfOcrJsonError("PDF OCR bbox ordering is invalid")
    raw["bbox"] = coordinates
    _validate_pdf_child_block_semantics(raw)
    try:
        return Block.model_validate(raw)
    except ValidationError as exc:
        raise _PdfOcrJsonError("PDF OCR block model is invalid") from exc


def _validate_pdf_ocr_section(
    value: Any,
    *,
    pages: int,
    counters: dict[str, int],
    section_ids: set[str],
    block_ids: set[str],
    display_asset_ids: set[str],
) -> Section:
    raw = _exact_pdf_ocr_object(value, _PDF_OCR_SECTION_KEYS)
    counters["sections"] += 1
    if counters["sections"] > MAX_PDF_OCR_SECTIONS:
        raise _PdfOcrJsonError("PDF OCR section limit exceeded")
    section_id = _validate_pdf_ocr_identifier(raw["id"])
    if section_id in section_ids:
        raise _PdfOcrJsonError("duplicate PDF OCR section id")
    section_ids.add(section_id)
    heading = _exact_pdf_ocr_object(raw["heading"], _PDF_OCR_HEADING_KEYS)
    _bounded_pdf_ocr_string(heading["number"])
    _bounded_pdf_ocr_string(heading["title"])
    if not isinstance(raw["blocks"], list) or not isinstance(raw["sections"], list):
        raise _PdfOcrJsonError("PDF OCR section collections are invalid")
    raw["blocks"] = [
        _validate_pdf_ocr_block(
            block,
            pages=pages,
            counters=counters,
            block_ids=block_ids,
            display_asset_ids=display_asset_ids,
        )
        for block in raw["blocks"]
    ]
    raw["sections"] = [
        _validate_pdf_ocr_section(
            section,
            pages=pages,
            counters=counters,
            section_ids=section_ids,
            block_ids=block_ids,
            display_asset_ids=display_asset_ids,
        )
        for section in raw["sections"]
    ]
    try:
        return Section.model_validate(raw)
    except ValidationError as exc:
        raise _PdfOcrJsonError("PDF OCR section model is invalid") from exc


def _validate_pdf_ocr_stats(value: Any, *, expected_ocr: bool) -> dict[str, Any]:
    stats = _exact_pdf_ocr_object(value, _PDF_OCR_STATS_KEYS)
    pages = _pdf_ocr_integer(stats["pages"], minimum=1, maximum=MAX_PDF_OCR_PAGES)
    if stats["ocr"] is not expected_ocr:
        raise _PdfOcrJsonError("PDF OCR stats identity is invalid")
    extracted_chars = _pdf_ocr_integer(
        stats["extracted_chars"], minimum=0, maximum=MAX_PDF_OCR_EXTRACTED_CHARS
    )
    if extracted_chars < 40 * pages:
        raise _PdfOcrJsonError("PDF OCR extracted text is insufficient")
    for key in ("figures", "tables", "blocks"):
        _pdf_ocr_integer(stats[key], minimum=0, maximum=MAX_PDF_OCR_BLOCKS)
    _pdf_ocr_integer(stats["columns"], minimum=1, maximum=2)
    if stats["pdf_sync_rate"] is not None:
        raise _PdfOcrJsonError("PDF OCR sync rate is invalid")
    _pdf_ocr_number(stats["figure_caption_match_rate"], minimum=0.0, maximum=1.0)
    _pdf_ocr_number(stats["equation_latex_rate"], minimum=0.0, maximum=1.0)
    return stats


def _decode_pdf_ocr_metadata_frame(
    payload: bytes,
    *,
    max_output_bytes: int,
    expected_ocr: bool = True,
    error_source_format: str = "pdf_ocr",
) -> _PdfOcrDecodedMetadata:
    text = _preflight_pdf_ocr_json(payload, source_format=error_source_format)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pdf_ocr_object_pairs,
            parse_constant=_reject_pdf_ocr_json_constant,
        )
        root = _exact_pdf_ocr_object(value, set(value) if type(value) is dict else set())
        if root.get("protocol") != PDF_OCR_IPC_VERSION:
            raise _PdfOcrJsonError("PDF OCR protocol version mismatch")
        status = root.get("status")
        if status == "error":
            _exact_pdf_ocr_object(
                root,
                {"protocol", "status", "source_format", "code", "message"},
            )
            if len(payload) > MAX_PDF_OCR_ERROR_BYTES:
                raise _pdf_output_too_large(
                    error_source_format,
                    "PDF error metadata exceeds the safe limit",
                )
            source_format = _bounded_pdf_ocr_string(root["source_format"], maximum=32)
            code = _bounded_pdf_ocr_string(root["code"], maximum=64)
            message = _bounded_pdf_ocr_string(root["message"], maximum=1024)
            allowed_codes = (
                _PDF_OCR_ERROR_CODES if expected_ocr else _PDF_PARSE_ERROR_CODES
            )
            if source_format != error_source_format or code not in allowed_codes:
                raise _PdfOcrJsonError("PDF OCR error metadata is invalid")
            raise CandidateUnavailable(source_format, code, message)
        _exact_pdf_ocr_object(root, {"protocol", "status", "document", "figures"})
        if status != "ok":
            raise _PdfOcrJsonError("PDF OCR status is invalid")
        document = _exact_pdf_ocr_object(
            root["document"],
            {"quality_level", "source_format", "parser_version", "sections", "warnings", "stats"},
        )
        if (
            document["quality_level"] != "B"
            or document["source_format"] != "pdf"
            or document["parser_version"] != PDF_PARSER_VERSION
        ):
            raise _PdfOcrJsonError("PDF OCR document identity is invalid")
        stats = _validate_pdf_ocr_stats(document["stats"], expected_ocr=expected_ocr)
        warnings = document["warnings"]
        if not isinstance(warnings, list) or len(warnings) > MAX_PDF_OCR_WARNINGS:
            raise _PdfOcrJsonError("PDF OCR warnings are invalid")
        validated_warnings = [_bounded_pdf_ocr_string(item) for item in warnings]
        sections = document["sections"]
        if not isinstance(sections, list):
            raise _PdfOcrJsonError("PDF OCR sections are invalid")
        counters = {"sections": 0, "blocks": 0, "inlines": 0}
        section_ids: set[str] = set()
        block_ids: set[str] = set()
        display_asset_ids: set[str] = set()
        validated_sections = [
            _validate_pdf_ocr_section(
                section,
                pages=stats["pages"],
                counters=counters,
                section_ids=section_ids,
                block_ids=block_ids,
                display_asset_ids=display_asset_ids,
            )
            for section in sections
        ]
        if counters["blocks"] != stats["blocks"]:
            raise _PdfOcrJsonError("PDF OCR block count is inconsistent")
        flat_blocks = [
            block
            for section in validated_sections
            for block in _walk_pdf_ocr_blocks(section)
        ]
        figure_blocks = sum(block.type == "figure" for block in flat_blocks)
        table_blocks = sum(block.type == "table" for block in flat_blocks)
        if figure_blocks != stats["figures"] or table_blocks != stats["tables"]:
            raise _PdfOcrJsonError("PDF OCR display block counts are inconsistent")
        figures = root["figures"]
        if not isinstance(figures, list) or len(figures) > MAX_PDF_OCR_FIGURES:
            raise _PdfOcrJsonError("PDF OCR figure manifest count is invalid")
        manifest: list[_PdfOcrImageManifestEntry] = []
        seen_figure_ids: set[str] = set()
        aggregate_size = 0
        for item in figures:
            entry = _exact_pdf_ocr_object(item, {"id", "size", "sha256"})
            block_id = _validate_pdf_ocr_identifier(entry["id"])
            if block_id in seen_figure_ids or block_id not in display_asset_ids:
                raise _PdfOcrJsonError("PDF OCR figure manifest id is invalid")
            seen_figure_ids.add(block_id)
            raw_size = entry["size"]
            if type(raw_size) is not int or raw_size < 1:
                raise _PdfOcrJsonError("PDF image size is invalid")
            size = raw_size
            if size > MAX_ASSET_BYTES:
                raise _pdf_output_too_large(
                    error_source_format,
                    "PDF image exceeds the safe limit",
                )
            digest = _bounded_pdf_ocr_string(entry["sha256"], maximum=64)
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise _PdfOcrJsonError("PDF OCR figure digest is invalid")
            aggregate_size += size
            if aggregate_size > MAX_PDF_OCR_FIGURE_BYTES:
                raise _pdf_output_too_large(
                    error_source_format,
                    "PDF figures exceed the safe limit",
                )
            manifest.append(_PdfOcrImageManifestEntry(block_id, size, digest))
        if len(payload) + aggregate_size > max_output_bytes:
            raise _pdf_output_too_large(
                error_source_format,
                "PDF output exceeds the safe limit",
            )
        parsed = ParsedPdfDocument(
            quality_level="B",
            source_format="pdf",
            parser_version=PDF_PARSER_VERSION,
            sections=validated_sections,
            warnings=validated_warnings,
            stats=stats,
            figure_images={},
        )
        return _PdfOcrDecodedMetadata(parsed=parsed, manifest=tuple(manifest))
    except CandidateUnavailable:
        raise
    except (_PdfOcrJsonError, RecursionError, TypeError, ValueError) as exc:
        raise _pdf_protocol_error(
            error_source_format,
            "PDF subprocess returned invalid metadata",
        ) from exc


def _walk_pdf_ocr_blocks(section: Section) -> list[Block]:
    blocks = list(section.blocks)
    for child in section.sections:
        blocks.extend(_walk_pdf_ocr_blocks(child))
    return blocks


class _PdfOperationCancelledError(Exception):
    pass


class _PdfOperationTimedOutError(Exception):
    pass


def _check_pdf_operation_state(
    *,
    absolute_deadline: float,
    cancellation_event: threading.Event,
) -> None:
    if cancellation_event.is_set():
        raise _PdfOperationCancelledError
    if time.monotonic() >= absolute_deadline:
        raise _PdfOperationTimedOutError


def _check_pdf_deadline_only(*, absolute_deadline: float) -> None:
    if time.monotonic() >= absolute_deadline:
        raise _PdfOperationTimedOutError


class _PdfSubprocessStartLease:
    def __init__(self, gate: Any) -> None:
        self._gate = gate
        self._lock = threading.Lock()
        self._released = False

    def release(self) -> None:
        with self._lock:
            if self._released:
                raise RuntimeError("PDF subprocess start lease was released twice")
            self._released = True
        self._gate.release()


def _acquire_pdf_subprocess_start_lease(
    *,
    absolute_deadline: float,
    cancellation_event: threading.Event,
) -> _PdfSubprocessStartLease:
    while True:
        _check_pdf_operation_state(
            absolute_deadline=absolute_deadline,
            cancellation_event=cancellation_event,
        )
        remaining = absolute_deadline - time.monotonic()
        if _PDF_SUBPROCESS_START_GATE.acquire(
            timeout=min(0.01, max(0.0, remaining))
        ):
            lease = _PdfSubprocessStartLease(_PDF_SUBPROCESS_START_GATE)
            try:
                _check_pdf_operation_state(
                    absolute_deadline=absolute_deadline,
                    cancellation_event=cancellation_event,
                )
            except (_PdfOperationCancelledError, _PdfOperationTimedOutError):
                lease.release()
                raise
            return lease


def _pdf_lifecycle_error(source_format: str, message: str) -> CandidateUnavailable:
    code = "ocr_lifecycle" if source_format == "pdf_ocr" else "pdf_lifecycle"
    return CandidateUnavailable(source_format, code, message)


def _pdf_connection_fd(connection: Connection, *, source_format: str, label: str) -> int:
    try:
        descriptor = connection.fileno()
        os.set_blocking(descriptor, False)
    except (OSError, TypeError, ValueError) as exc:
        raise _pdf_lifecycle_error(
            source_format,
            f"{label} connection is unavailable",
        ) from exc
    return descriptor


def _read_pdf_pipe_exact(
    descriptor: int,
    length: int,
    *,
    absolute_deadline: float,
    cancellation_event: threading.Event,
    source_format: str,
    label: str,
) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while received < length:
        _check_pdf_operation_state(
            absolute_deadline=absolute_deadline,
            cancellation_event=cancellation_event,
        )
        remaining = absolute_deadline - time.monotonic()
        try:
            readable, _, _ = select.select(
                [descriptor],
                [],
                [],
                min(0.05, max(0.0, remaining)),
            )
        except (OSError, ValueError) as exc:
            raise _pdf_lifecycle_error(
                source_format,
                f"{label} frame wait failed",
            ) from exc
        if not readable:
            continue
        try:
            chunk = os.read(descriptor, min(64 * 1024, length - received))
        except (BlockingIOError, InterruptedError):
            continue
        except OSError as exc:
            raise _pdf_lifecycle_error(
                source_format,
                f"{label} frame receive failed",
            ) from exc
        if not chunk:
            raise _pdf_lifecycle_error(
                source_format,
                f"{label} frame ended before its declared length",
            )
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


def _read_pdf_ipc_frame(
    connection: Connection,
    *,
    maximum_length: int,
    absolute_deadline: float,
    cancellation_event: threading.Event,
    source_format: str,
    label: str,
    expected_length: int | None = None,
) -> bytes:
    descriptor = _pdf_connection_fd(
        connection,
        source_format=source_format,
        label=label,
    )
    header = _read_pdf_pipe_exact(
        descriptor,
        4,
        absolute_deadline=absolute_deadline,
        cancellation_event=cancellation_event,
        source_format=source_format,
        label=label,
    )
    (length,) = struct.unpack("!i", header)
    if length == -1:
        extended = _read_pdf_pipe_exact(
            descriptor,
            8,
            absolute_deadline=absolute_deadline,
            cancellation_event=cancellation_event,
            source_format=source_format,
            label=label,
        )
        (length,) = struct.unpack("!Q", extended)
    elif length < 0:
        raise _pdf_protocol_error(source_format, f"{label} frame length is invalid")
    if expected_length is not None and length != expected_length:
        raise _pdf_protocol_error(
            source_format,
            f"{label} frame does not match its declared size",
        )
    if length > maximum_length:
        raise _pdf_output_too_large(
            source_format,
            f"{label} frame exceeds the safe limit",
        )
    return _read_pdf_pipe_exact(
        descriptor,
        length,
        absolute_deadline=absolute_deadline,
        cancellation_event=cancellation_event,
        source_format=source_format,
        label=label,
    )


def _wait_for_pdf_ipc_eof(
    connection: Connection,
    *,
    absolute_deadline: float,
    cancellation_event: threading.Event,
    source_format: str,
    label: str,
) -> None:
    descriptor = _pdf_connection_fd(
        connection,
        source_format=source_format,
        label=label,
    )
    while True:
        _check_pdf_operation_state(
            absolute_deadline=absolute_deadline,
            cancellation_event=cancellation_event,
        )
        remaining = absolute_deadline - time.monotonic()
        try:
            readable, _, _ = select.select(
                [descriptor],
                [],
                [],
                min(0.05, max(0.0, remaining)),
            )
        except (OSError, ValueError) as exc:
            raise _pdf_lifecycle_error(
                source_format,
                f"{label} completion wait failed",
            ) from exc
        if not readable:
            continue
        try:
            extra = os.read(descriptor, 1)
        except (BlockingIOError, InterruptedError):
            continue
        except OSError as exc:
            raise _pdf_lifecycle_error(
                source_format,
                f"{label} completion receive failed",
            ) from exc
        if extra:
            raise _pdf_protocol_error(
                source_format,
                f"{label} subprocess returned an extra frame",
            )
        return


def _receive_pdf_ocr_image_frame(
    connection: Connection,
    entry: _PdfOcrImageManifestEntry,
    *,
    source_format: str = "pdf_ocr",
    absolute_deadline: float | None = None,
    cancellation_event: threading.Event | None = None,
) -> bytes:
    deadline = (
        absolute_deadline
        if absolute_deadline is not None
        else time.monotonic() + MAX_PDF_OCR_SECONDS
    )
    cancel = cancellation_event or threading.Event()
    payload = _read_pdf_ipc_frame(
        connection,
        maximum_length=entry.size,
        expected_length=entry.size,
        absolute_deadline=deadline,
        cancellation_event=cancel,
        source_format=source_format,
        label="PDF image",
    )
    if len(payload) != entry.size or hashlib.sha256(payload).hexdigest() != entry.sha256:
        raise _pdf_protocol_error(
            source_format,
            "PDF image frame does not match its manifest",
        )
    return payload


def _safe_archive_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and name == name.strip()
        and "\\" not in name
        and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in name)
        and not path.is_absolute()
        and _SCHEME_PREFIX_RE.match(name) is None
        and all(part not in {"", ".", ".."} for part in path.parts)
        and str(path) == name
    )


def embedded_pdf_bytes(
    report: DocumentCompleteness,
    binary_files: Mapping[str, bytes],
) -> tuple[str, bytes] | None:
    """Select the sole safe PDF member from an embedded-PDF LaTeX wrapper."""

    if report.code != "embedded_pdf_wrapper":
        return None
    pdfs = [
        (name, data)
        for name, data in sorted(binary_files.items(), key=lambda item: item[0])
        if name.lower().endswith(".pdf")
    ]
    if len(pdfs) != 1:
        return None
    name, data = pdfs[0]
    if (
        not _safe_archive_member_name(name)
        or len(data) < 8
        or not data[:1024].lstrip().startswith(b"%PDF-")
    ):
        return None
    return name, data


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
        source_manifest={"binary_files": sorted(extracted.binary_files)},
        graphicspaths=extract_graphicspaths(extracted.text_files, main_tex_name),
        latex_binary_files=extracted.binary_files,
        latex_main_tex_name=main_tex_name,
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
        source_manifest={},
    )


def parse_pdf_candidate(
    source_bytes: bytes,
    *,
    pdf_text: str,
) -> SourceCandidate:
    """Parse and assess the retained original PDF candidate."""

    if not sys.platform.startswith("linux"):
        raise CandidateUnavailable(
            "pdf",
            "pdf_platform_unsupported",
            "Isolated PDF parsing is unsupported on this platform",
        )
    if len(source_bytes) > MAX_ARXIV_PDF_BYTES:
        raise CandidateUnavailable("pdf", "source_too_large", "PDF input exceeds the safe limit")
    try:
        result = _run_isolated_pdf_ocr(
            source_bytes,
            "eng",
            MAX_PDF_OCR_SECONDS,
            MAX_PDF_OCR_OUTPUT_BYTES,
            _parse_pdf_document_trusted,
            cancellation_event=threading.Event(),
            expected_ocr=False,
            error_source_format="pdf",
        )
    except CandidateUnavailable as exc:
        raise CandidateUnavailable("pdf", exc.code, exc.message) from exc
    if not isinstance(result, ParsedPdfDocument):
        raise CandidateUnavailable("pdf", "pdf_lifecycle", "PDF parser subprocess failed")

    return _pdf_candidate_from_parsed(
        source_bytes,
        pdf_text=pdf_text,
        parsed=result,
        ocr_language=None,
    )


def _pdf_candidate_from_parsed(
    source_bytes: bytes,
    *,
    pdf_text: str,
    parsed: ParsedPdfDocument,
    ocr_language: str | None,
) -> SourceCandidate:
    source_char_count: int | None = None
    candidate_source = "pdf_ocr" if ocr_language is not None else "pdf"
    identity_error = "ocr_crashed" if ocr_language is not None else "parse_error"
    pages = parsed.stats.get("pages")
    extracted_chars = parsed.stats.get("extracted_chars")
    if (
        parsed.quality_level != "B"
        or parsed.source_format != "pdf"
        or parsed.parser_version != PDF_PARSER_VERSION
        or type(pages) is not int
        or not 1 <= pages <= MAX_PDF_OCR_PAGES
        or type(extracted_chars) is not int
        or not 0 <= extracted_chars <= MAX_PDF_EXTRACTED_CHARS
        or extracted_chars < 40 * pages
    ):
        raise CandidateUnavailable(
            candidate_source,
            identity_error,
            "PDF parser result identity is invalid",
        )
    if ocr_language is None:
        if parsed.stats.get("ocr") is not False:
            raise CandidateUnavailable(
                "pdf", "parse_error", "PDF parser result identity is invalid"
            )
    else:
        if parsed.stats.get("ocr") is not True:
            raise CandidateUnavailable(
                "pdf_ocr", "ocr_crashed", "PDF OCR parser result identity is invalid"
            )
        source_char_count = extracted_chars
    content = parsed.to_document_content()
    report = assess_document_completeness(
        content,
        pdf_text=pdf_text,
        source_char_count=source_char_count,
        source_manifest={},
    )
    return SourceCandidate(
        source_format="pdf",
        content=content,
        parsed=parsed,
        report=report,
        source_bytes=source_bytes,
        diagnostics=(
            [
                {
                    "kind": "pdf_ocr",
                    "version": PDF_OCR_CANDIDATE_VERSION,
                    "language": ocr_language,
                }
            ]
            if ocr_language is not None
            else []
        ),
        source_manifest={},
    )


PdfOcrWorker = Callable[..., ParsedPdfDocument]
PdfOcrChildEntry = Callable[..., None]
PdfTextEvidenceWorker = Callable[[bytes], PdfTextEvidenceCounts]
PdfTextEvidenceChildEntry = Callable[..., None]


def _parse_pdf_document_trusted(
    source_bytes: bytes,
    *,
    ocr_language: str,
) -> ParsedPdfDocument:
    del ocr_language
    return parse_pdf(source_bytes)


def _parse_pdf_ocr_document_trusted(
    source_bytes: bytes,
    *,
    ocr_language: str,
) -> ParsedPdfDocument:
    return parse_pdf(
        source_bytes,
        use_ocr=True,
        ocr_language=ocr_language,
    )


def _set_pdf_ocr_child_limit(resource_kind: int, soft_limit: int, hard_limit: int) -> None:
    import resource

    _current_soft, current_hard = resource.getrlimit(resource_kind)
    if current_hard != resource.RLIM_INFINITY:
        hard_limit = min(hard_limit, current_hard)
    resource.setrlimit(resource_kind, (min(soft_limit, hard_limit), hard_limit))


def _apply_pdf_ocr_resource_limits(timeout_s: float, max_output_bytes: int) -> None:
    if not sys.platform.startswith("linux"):
        raise CandidateUnavailable(
            "pdf_ocr",
            "ocr_platform_unsupported",
            "PDF OCR is unsupported on this platform",
        )
    import resource

    cpu_seconds = max(1, math.ceil(timeout_s))
    _set_pdf_ocr_child_limit(resource.RLIMIT_CPU, cpu_seconds, cpu_seconds + 1)
    _set_pdf_ocr_child_limit(
        resource.RLIMIT_AS,
        MAX_PDF_OCR_MEMORY_BYTES,
        MAX_PDF_OCR_MEMORY_BYTES,
    )
    _set_pdf_ocr_child_limit(
        resource.RLIMIT_FSIZE,
        max(1024 * 1024, max_output_bytes),
        max(1024 * 1024, max_output_bytes),
    )
    _set_pdf_ocr_child_limit(resource.RLIMIT_NOFILE, 64, 64)


def _pdf_ocr_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _pdf_ocr_error_frame(source_format: str, code: str, message: str) -> bytes:
    safe_source = source_format if source_format in {"pdf", "pdf_ocr"} else "pdf_ocr"
    fallback_code = "ocr_crashed" if safe_source == "pdf_ocr" else "pdf_crashed"
    allowed_codes = (
        _PDF_OCR_ERROR_CODES if safe_source == "pdf_ocr" else _PDF_PARSE_ERROR_CODES
    )
    safe_code = code if code in allowed_codes else fallback_code
    try:
        safe_message = _bounded_pdf_ocr_string(message[:1024], maximum=1024)
    except _PdfOcrJsonError:
        safe_code = fallback_code
        safe_message = "PDF subprocess failed"
    payload = _pdf_ocr_json_bytes(
        {
            "protocol": PDF_OCR_IPC_VERSION,
            "status": "error",
            "source_format": safe_source,
            "code": safe_code,
            "message": safe_message,
        }
    )
    if len(payload) > MAX_PDF_OCR_ERROR_BYTES:
        return _pdf_ocr_json_bytes(
            {
                "protocol": PDF_OCR_IPC_VERSION,
                "status": "error",
                "source_format": safe_source,
                "code": fallback_code,
                "message": "PDF subprocess failed",
            }
        )
    return payload


def _prepare_pdf_ocr_success_frames(
    parsed: ParsedPdfDocument,
    *,
    max_output_bytes: int,
    expected_ocr: bool = True,
    error_source_format: str = "pdf_ocr",
) -> tuple[bytes, list[bytes]]:
    images = sorted(parsed.figure_images.items(), key=lambda item: item[0])
    manifest = [
        {
            "id": block_id,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for block_id, payload in images
    ]
    metadata = _pdf_ocr_json_bytes(
        {
            "protocol": PDF_OCR_IPC_VERSION,
            "status": "ok",
            "document": {
                "quality_level": parsed.quality_level,
                "source_format": parsed.source_format,
                "parser_version": parsed.parser_version,
                "sections": [section.model_dump(mode="json") for section in parsed.sections],
                "warnings": list(parsed.warnings),
                "stats": dict(parsed.stats),
            },
            "figures": manifest,
        }
    )
    _decode_pdf_ocr_metadata_frame(
        metadata,
        max_output_bytes=max_output_bytes,
        expected_ocr=expected_ocr,
        error_source_format=error_source_format,
    )
    return metadata, [payload for _block_id, payload in images]


def _pdf_ocr_child_entry(
    connection: Connection,
    source_bytes: bytes,
    ocr_language: str,
    timeout_s: float,
    max_output_bytes: int,
    worker: PdfOcrWorker,
    expected_ocr: bool = True,
    error_source_format: str = "pdf_ocr",
) -> None:
    crash_code = "ocr_crashed" if error_source_format == "pdf_ocr" else "pdf_crashed"
    try:
        _apply_pdf_ocr_resource_limits(timeout_s, max_output_bytes)
        parsed = worker(
            source_bytes,
            ocr_language=ocr_language,
        )
        if not isinstance(parsed, ParsedPdfDocument):
            connection.send_bytes(
                _pdf_ocr_error_frame(
                    error_source_format, crash_code, "PDF parser subprocess failed"
                )
            )
            return
        metadata, image_frames = _prepare_pdf_ocr_success_frames(
            parsed,
            max_output_bytes=max_output_bytes,
            expected_ocr=expected_ocr,
            error_source_format=error_source_format,
        )
        connection.send_bytes(metadata)
        for image_frame in image_frames:
            connection.send_bytes(image_frame)
    except PdfParseError as exc:
        connection.send_bytes(
            _pdf_ocr_error_frame(error_source_format, exc.kind, exc.message)
        )
    except CandidateUnavailable as exc:
        connection.send_bytes(
            _pdf_ocr_error_frame(error_source_format, exc.code, exc.message)
        )
    except BaseException:
        try:
            connection.send_bytes(
                _pdf_ocr_error_frame(
                    error_source_format, crash_code, "PDF parser subprocess failed"
                )
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _pdf_text_evidence_error_frame(code: str, message: str) -> bytes:
    safe_code = code if code in _PDF_TEXT_EVIDENCE_ERROR_CODES else "pdf_crashed"
    try:
        safe_message = _bounded_pdf_ocr_string(message[:512], maximum=512)
    except _PdfOcrJsonError:
        safe_code = "pdf_crashed"
        safe_message = "PDF text evidence subprocess failed"
    payload = _pdf_ocr_json_bytes(
        {
            "protocol": PDF_TEXT_EVIDENCE_IPC_VERSION,
            "status": "error",
            "source_format": "pdf",
            "code": safe_code,
            "message": safe_message,
        }
    )
    if len(payload) <= MAX_PDF_TEXT_EVIDENCE_METADATA_BYTES:
        return payload
    return _pdf_ocr_json_bytes(
        {
            "protocol": PDF_TEXT_EVIDENCE_IPC_VERSION,
            "status": "error",
            "source_format": "pdf",
            "code": "pdf_crashed",
            "message": "PDF text evidence subprocess failed",
        }
    )


def _decode_pdf_text_evidence_frame(payload: bytes) -> PdfTextEvidenceCounts:
    if len(payload) > MAX_PDF_TEXT_EVIDENCE_METADATA_BYTES:
        raise _pdf_output_too_large("pdf", "PDF text evidence exceeds the safe limit")
    text = _preflight_pdf_ocr_json(payload, source_format="pdf")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pdf_ocr_object_pairs,
            parse_constant=_reject_pdf_ocr_json_constant,
        )
        if type(value) is not dict:
            raise _PdfOcrJsonError("PDF text evidence envelope is invalid")
        if value.get("protocol") != PDF_TEXT_EVIDENCE_IPC_VERSION:
            raise _PdfOcrJsonError("PDF text evidence protocol version mismatch")
        if value.get("status") == "error":
            error = _exact_pdf_ocr_object(
                value,
                {"protocol", "status", "source_format", "code", "message"},
            )
            source_format = _bounded_pdf_ocr_string(error["source_format"], maximum=8)
            code = _bounded_pdf_ocr_string(error["code"], maximum=64)
            message = _bounded_pdf_ocr_string(error["message"], maximum=512)
            if source_format != "pdf" or code not in _PDF_TEXT_EVIDENCE_ERROR_CODES:
                raise _PdfOcrJsonError("PDF text evidence error is invalid")
            raise CandidateUnavailable(source_format, code, message)
        success = _exact_pdf_ocr_object(
            value,
            {
                "protocol",
                "status",
                "source_format",
                "parser_version",
                "pages",
                "extracted_chars",
            },
        )
        if (
            success["status"] != "ok"
            or success["source_format"] != "pdf"
            or success["parser_version"] != PDF_PARSER_VERSION
        ):
            raise _PdfOcrJsonError("PDF text evidence identity is invalid")
        return PdfTextEvidenceCounts(
            pages=_pdf_ocr_integer(success["pages"], minimum=0, maximum=MAX_PDF_OCR_PAGES),
            extracted_chars=_pdf_ocr_integer(
                success["extracted_chars"],
                minimum=0,
                maximum=MAX_PDF_OCR_EXTRACTED_CHARS,
            ),
        )
    except CandidateUnavailable:
        raise
    except (_PdfOcrJsonError, RecursionError, TypeError, ValueError) as exc:
        raise _pdf_protocol_error(
            "pdf", "PDF text evidence subprocess returned invalid metadata"
        ) from exc


def _prepare_pdf_text_evidence_success_frame(evidence: PdfTextEvidenceCounts) -> bytes:
    payload = _pdf_ocr_json_bytes(
        {
            "protocol": PDF_TEXT_EVIDENCE_IPC_VERSION,
            "status": "ok",
            "source_format": "pdf",
            "parser_version": PDF_PARSER_VERSION,
            "pages": evidence.pages,
            "extracted_chars": evidence.extracted_chars,
        }
    )
    _decode_pdf_text_evidence_frame(payload)
    return payload


def _pdf_text_evidence_child_entry(
    connection: Connection,
    source_bytes: bytes,
    timeout_s: float,
    worker: PdfTextEvidenceWorker = count_pdf_text_evidence,
) -> None:
    try:
        _apply_pdf_ocr_resource_limits(timeout_s, MAX_PDF_TEXT_EVIDENCE_METADATA_BYTES)
        evidence = worker(source_bytes)
        if not isinstance(evidence, PdfTextEvidenceCounts):
            connection.send_bytes(
                _pdf_text_evidence_error_frame(
                    "pdf_crashed", "PDF text evidence subprocess failed"
                )
            )
            return
        connection.send_bytes(_prepare_pdf_text_evidence_success_frame(evidence))
    except PdfParseError as exc:
        connection.send_bytes(_pdf_text_evidence_error_frame(exc.kind, exc.message))
    except CandidateUnavailable as exc:
        connection.send_bytes(_pdf_text_evidence_error_frame(exc.code, exc.message))
    except BaseException:
        try:
            connection.send_bytes(
                _pdf_text_evidence_error_frame(
                    "pdf_crashed", "PDF text evidence subprocess failed"
                )
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _terminate_pdf_ocr_child(
    process: Any,
    *,
    source_format: str = "pdf_ocr",
    lifecycle_code: str = "ocr_lifecycle",
    label: str = "PDF OCR",
) -> None:
    errors: list[BaseException] = []

    def attempt(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except BaseException as exc:
            errors.append(exc)
            return None

    if hasattr(process, "pid") and getattr(process, "pid", None) is None:
        close_unstarted = getattr(process, "close", None)
        if callable(close_unstarted):
            attempt(close_unstarted)
        if errors:
            raise CandidateUnavailable(
                source_format,
                lifecycle_code,
                f"{label} subprocess lifecycle failed",
            ) from errors[0]
        return

    alive = attempt(process.is_alive)
    if alive is True:
        attempt(process.terminate)
        attempt(lambda: process.join(timeout=0.5))
    alive = attempt(process.is_alive)
    if alive is not False:
        attempt(process.kill)
        attempt(lambda: process.join(timeout=0.5))
    # A naturally exited multiprocessing.Process may not expose exitcode until joined.
    attempt(lambda: process.join(timeout=0.1))
    final_alive = attempt(process.is_alive)
    exitcode = attempt(lambda: process.exitcode)
    if final_alive is not False or exitcode is None:
        errors.append(RuntimeError(f"{label} subprocess could not be reaped"))
    close = getattr(process, "close", None)
    if callable(close):
        attempt(close)
    if errors:
        raise CandidateUnavailable(
            source_format,
            lifecycle_code,
            f"{label} subprocess lifecycle failed",
        ) from errors[0]


def _cleanup_pdf_subprocess(
    *,
    process: Any | None,
    receive_connection: Any | None,
    send_connection: Any | None,
    source_format: str,
    lifecycle_code: str,
    label: str,
) -> None:
    errors: list[BaseException] = []
    if process is not None:
        try:
            _terminate_pdf_ocr_child(
                process,
                source_format=source_format,
                lifecycle_code=lifecycle_code,
                label=label,
            )
        except BaseException as exc:
            errors.append(exc)
    for connection in (receive_connection, send_connection):
        if connection is None:
            continue
        try:
            connection.close()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise CandidateUnavailable(
            source_format,
            lifecycle_code,
            f"{label} subprocess cleanup failed",
        ) from errors[0]


class _PdfSubprocessStartSupervisor:
    """Bound ``Process.start`` without racing cleanup against a late start."""

    def __init__(
        self,
        *,
        process: Any,
        receive_connection: Any,
        send_connection: Any,
        source_format: str,
        lifecycle_code: str,
        label: str,
        start_lease: _PdfSubprocessStartLease,
    ) -> None:
        self._process = process
        self._receive_connection = receive_connection
        self._send_connection = send_connection
        self._source_format = source_format
        self._lifecycle_code = lifecycle_code
        self._label = label
        self._start_lease = start_lease
        self._lock = threading.Lock()
        self._completed = threading.Event()
        self._caller_abandoned = False
        self._start_error: BaseException | None = None
        self.cleanup_deferred = False

    def _start_and_cleanup_if_abandoned(self) -> None:
        start_error: BaseException | None = None
        try:
            self._process.start()
        except BaseException as exc:
            start_error = exc
        with self._lock:
            self._start_error = start_error
            caller_abandoned = self._caller_abandoned
            self._completed.set()
        if not caller_abandoned:
            return
        cleanup_error: BaseException | None = None
        try:
            _cleanup_pdf_subprocess(
                process=self._process,
                receive_connection=self._receive_connection,
                send_connection=self._send_connection,
                source_format=self._source_format,
                lifecycle_code=self._lifecycle_code,
                label=self._label,
            )
        except BaseException as exc:
            cleanup_error = exc
        finally:
            self._start_lease.release()
        if cleanup_error is not None:
            code = (
                cleanup_error.code
                if isinstance(cleanup_error, CandidateUnavailable)
                else self._lifecycle_code
            )
            log.warning(
                "pdf_subprocess_late_start_cleanup_failed",
                code=code,
                error_type=type(cleanup_error).__name__,
                operation=self._label,
            )

    def start_and_wait(
        self,
        *,
        absolute_deadline: float,
        cancellation_event: threading.Event,
    ) -> None:
        thread_started = False
        try:
            start_thread = threading.Thread(
                target=self._start_and_cleanup_if_abandoned,
                name="alinea-pdf-subprocess-start",
                daemon=True,
            )
            start_thread.start()
            thread_started = True
            while True:
                remaining = absolute_deadline - time.monotonic()
                if self._completed.wait(timeout=min(0.01, max(0.0, remaining))):
                    with self._lock:
                        start_error = self._start_error
                    if start_error is not None:
                        raise start_error
                    return
                try:
                    _check_pdf_operation_state(
                        absolute_deadline=absolute_deadline,
                        cancellation_event=cancellation_event,
                    )
                except (_PdfOperationCancelledError, _PdfOperationTimedOutError):
                    with self._lock:
                        if not self._completed.is_set():
                            self._caller_abandoned = True
                            self.cleanup_deferred = True
                    raise
        finally:
            if not thread_started:
                self._start_lease.release()
            else:
                with self._lock:
                    if not self._completed.is_set() and not self._caller_abandoned:
                        self._caller_abandoned = True
                        self.cleanup_deferred = True
                    cleanup_deferred = self.cleanup_deferred
                if not cleanup_deferred:
                    self._start_lease.release()


def _run_isolated_pdf_ocr(
    source_bytes: bytes,
    ocr_language: str,
    timeout_s: float,
    max_output_bytes: int,
    worker: PdfOcrWorker,
    *,
    cancellation_event: threading.Event,
    child_entry: PdfOcrChildEntry = _pdf_ocr_child_entry,
    expected_ocr: bool = True,
    error_source_format: str = "pdf_ocr",
    absolute_deadline: float | None = None,
) -> object:
    is_ocr = error_source_format == "pdf_ocr"
    lifecycle_code = "ocr_lifecycle" if is_ocr else "pdf_lifecycle"
    timeout_code = "ocr_timeout" if is_ocr else "pdf_timeout"
    label = "PDF OCR" if is_ocr else "PDF parser"
    deadline = (
        absolute_deadline
        if absolute_deadline is not None
        else time.monotonic() + timeout_s
    )
    receive_connection: Connection | None = None
    send_connection: Connection | None = None
    process: Any | None = None
    timed_out = False
    cancelled = False
    parsed: ParsedPdfDocument | None = None
    primary_error: CandidateUnavailable | None = None
    primary_cause: BaseException | None = None
    start_supervisor: _PdfSubprocessStartSupervisor | None = None
    start_lease: _PdfSubprocessStartLease | None = None
    try:
        _check_pdf_deadline_only(absolute_deadline=deadline)
        start_lease = _acquire_pdf_subprocess_start_lease(
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
        )
        context = mp.get_context("spawn")
        _check_pdf_deadline_only(absolute_deadline=deadline)
        receive_connection, send_connection = context.Pipe(duplex=False)
        _check_pdf_deadline_only(absolute_deadline=deadline)
        process = context.Process(
            target=child_entry,
            args=(
                send_connection,
                source_bytes,
                ocr_language,
                timeout_s,
                max_output_bytes,
                worker,
                expected_ocr,
                error_source_format,
            ),
            daemon=True,
        )
        _check_pdf_operation_state(
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
        )
        start_supervisor = _PdfSubprocessStartSupervisor(
            process=process,
            receive_connection=receive_connection,
            send_connection=send_connection,
            source_format=error_source_format,
            lifecycle_code=lifecycle_code,
            label=label,
            start_lease=start_lease,
        )
        start_lease = None
        start_supervisor.start_and_wait(
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
        )
        _check_pdf_operation_state(
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
        )
        send_connection.close()
        metadata_frame = _read_pdf_ipc_frame(
            receive_connection,
            maximum_length=MAX_PDF_OCR_METADATA_BYTES,
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
            source_format=error_source_format,
            label=f"{label} metadata",
        )
        decoded = _decode_pdf_ocr_metadata_frame(
            metadata_frame,
            max_output_bytes=max_output_bytes,
            expected_ocr=expected_ocr,
            error_source_format=error_source_format,
        )
        del metadata_frame
        figure_images: dict[str, bytes] = {}
        for entry in decoded.manifest:
            figure_images[entry.block_id] = _receive_pdf_ocr_image_frame(
                receive_connection,
                entry,
                source_format=error_source_format,
                absolute_deadline=deadline,
                cancellation_event=cancellation_event,
            )
        _wait_for_pdf_ipc_eof(
            receive_connection,
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
            source_format=error_source_format,
            label=label,
        )
        decoded.parsed.figure_images = figure_images
        parsed = decoded.parsed
    except _PdfOperationCancelledError:
        cancelled = True
    except _PdfOperationTimedOutError:
        timed_out = True
    except CandidateUnavailable as exc:
        primary_error = exc
    except Exception as exc:
        primary_error = CandidateUnavailable(
            error_source_format,
            lifecycle_code,
            f"{label} subprocess lifecycle failed",
        )
        primary_cause = exc
    finally:
        try:
            if not (
                start_supervisor is not None and start_supervisor.cleanup_deferred
            ):
                try:
                    _cleanup_pdf_subprocess(
                        process=process,
                        receive_connection=receive_connection,
                        send_connection=send_connection,
                        source_format=error_source_format,
                        lifecycle_code=lifecycle_code,
                        label=label,
                    )
                except CandidateUnavailable as cleanup_error:
                    cause = primary_error or primary_cause
                    if cause is not None:
                        raise cleanup_error from cause
                    raise
        finally:
            if start_lease is not None:
                start_lease.release()

    if primary_error is not None:
        if primary_cause is not None:
            raise primary_error from primary_cause
        raise primary_error
    if cancelled:
        return _CANCELLED_PDF_OCR_RESULT
    if timed_out:
        raise CandidateUnavailable(
            error_source_format,
            timeout_code,
            f"{label} deadline was exceeded",
        )
    if parsed is not None:
        return parsed
    raise CandidateUnavailable(
        error_source_format,
        lifecycle_code,
        f"{label} subprocess failed",
    )


def _run_isolated_pdf_text_evidence(
    source_bytes: bytes,
    timeout_s: float,
    worker: PdfTextEvidenceWorker,
    *,
    cancellation_event: threading.Event,
    child_entry: PdfTextEvidenceChildEntry = _pdf_text_evidence_child_entry,
    absolute_deadline: float | None = None,
) -> object:
    deadline = (
        absolute_deadline
        if absolute_deadline is not None
        else time.monotonic() + timeout_s
    )
    receive_connection: Connection | None = None
    send_connection: Connection | None = None
    process: Any | None = None
    cancelled = False
    timed_out = False
    evidence: PdfTextEvidenceCounts | None = None
    primary_error: CandidateUnavailable | None = None
    primary_cause: BaseException | None = None
    start_supervisor: _PdfSubprocessStartSupervisor | None = None
    start_lease: _PdfSubprocessStartLease | None = None
    try:
        _check_pdf_deadline_only(absolute_deadline=deadline)
        start_lease = _acquire_pdf_subprocess_start_lease(
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
        )
        context = mp.get_context("spawn")
        _check_pdf_deadline_only(absolute_deadline=deadline)
        receive_connection, send_connection = context.Pipe(duplex=False)
        _check_pdf_deadline_only(absolute_deadline=deadline)
        process = context.Process(
            target=child_entry,
            args=(send_connection, source_bytes, timeout_s, worker),
            daemon=True,
        )
        _check_pdf_operation_state(
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
        )
        start_supervisor = _PdfSubprocessStartSupervisor(
            process=process,
            receive_connection=receive_connection,
            send_connection=send_connection,
            source_format="pdf",
            lifecycle_code="pdf_lifecycle",
            label="PDF text evidence",
            start_lease=start_lease,
        )
        start_lease = None
        start_supervisor.start_and_wait(
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
        )
        _check_pdf_operation_state(
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
        )
        send_connection.close()
        frame = _read_pdf_ipc_frame(
            receive_connection,
            maximum_length=MAX_PDF_TEXT_EVIDENCE_METADATA_BYTES,
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
            source_format="pdf",
            label="PDF text evidence",
        )
        evidence = _decode_pdf_text_evidence_frame(frame)
        del frame
        _wait_for_pdf_ipc_eof(
            receive_connection,
            absolute_deadline=deadline,
            cancellation_event=cancellation_event,
            source_format="pdf",
            label="PDF text evidence",
        )
    except _PdfOperationCancelledError:
        cancelled = True
    except _PdfOperationTimedOutError:
        timed_out = True
    except CandidateUnavailable as exc:
        primary_error = exc
    except Exception as exc:
        primary_error = CandidateUnavailable(
            "pdf",
            "pdf_lifecycle",
            "PDF text evidence subprocess lifecycle failed",
        )
        primary_cause = exc
    finally:
        try:
            if not (
                start_supervisor is not None and start_supervisor.cleanup_deferred
            ):
                try:
                    _cleanup_pdf_subprocess(
                        process=process,
                        receive_connection=receive_connection,
                        send_connection=send_connection,
                        source_format="pdf",
                        lifecycle_code="pdf_lifecycle",
                        label="PDF text evidence",
                    )
                except CandidateUnavailable as cleanup_error:
                    cause = primary_error or primary_cause
                    if cause is not None:
                        raise cleanup_error from cause
                    raise
        finally:
            if start_lease is not None:
                start_lease.release()

    if primary_error is not None:
        if primary_cause is not None:
            raise primary_error from primary_cause
        raise primary_error
    if cancelled:
        return _CANCELLED_PDF_OCR_RESULT
    if timed_out:
        raise CandidateUnavailable(
            "pdf", "pdf_timeout", "PDF text evidence deadline was exceeded"
        )
    if evidence is not None:
        return evidence
    raise CandidateUnavailable(
        "pdf", "pdf_lifecycle", "PDF text evidence subprocess failed"
    )


async def _drain_pdf_ocr_supervisor(
    worker_task: asyncio.Task[object],
) -> BaseException | None:
    while not worker_task.done():
        try:
            await asyncio.shield(worker_task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    try:
        return worker_task.exception()
    except BaseException as exc:
        return exc


async def _supervise_pdf_subprocess(
    runner: Callable[..., object],
    *args: Any,
    task_name: str,
    cleanup_event: str,
    lifecycle_code: str,
    **kwargs: Any,
) -> object:
    """Run one isolated PDF operation with cancellation propagated to its child."""

    cancellation_event = threading.Event()
    worker_task = asyncio.create_task(
        asyncio.to_thread(
            runner,
            *args,
            cancellation_event=cancellation_event,
            **kwargs,
        ),
        name=task_name,
    )
    try:
        return await asyncio.shield(worker_task)
    except asyncio.CancelledError as original_cancel:
        cancellation_event.set()
        cleanup_error = await _drain_pdf_ocr_supervisor(worker_task)
        if cleanup_error is not None:
            code = (
                cleanup_error.code
                if isinstance(cleanup_error, CandidateUnavailable)
                else lifecycle_code
            )
            await log.awarning(
                cleanup_event,
                code=code,
                error_type=type(cleanup_error).__name__,
            )
            raise original_cancel from cleanup_error
        raise original_cancel


async def parse_pdf_candidate_async(
    source_bytes: bytes,
    *,
    pdf_text: str,
    timeout_s: float = MAX_PDF_OCR_SECONDS,
    max_output_bytes: int = MAX_PDF_OCR_OUTPUT_BYTES,
    worker: PdfOcrWorker = _parse_pdf_document_trusted,
) -> SourceCandidate:
    """Parse a normal PDF without blocking the event loop and reap on cancellation."""

    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise CandidateUnavailable("pdf", "pdf_timeout", "PDF parser deadline is invalid")
    deadline = time.monotonic() + timeout_s
    if not sys.platform.startswith("linux"):
        raise CandidateUnavailable(
            "pdf",
            "pdf_platform_unsupported",
            "Isolated PDF parsing is unsupported on this platform",
        )
    if max_output_bytes <= 0 or max_output_bytes > MAX_PDF_OCR_OUTPUT_BYTES:
        raise CandidateUnavailable(
            "pdf", "pdf_output_too_large", "PDF parser output limit is invalid"
        )
    if len(source_bytes) > MAX_ARXIV_PDF_BYTES:
        raise CandidateUnavailable("pdf", "source_too_large", "PDF input exceeds the safe limit")
    result = await _supervise_pdf_subprocess(
        _run_isolated_pdf_ocr,
        source_bytes,
        "eng",
        timeout_s,
        max_output_bytes,
        worker,
        task_name="alinea-pdf-parser-supervisor",
        cleanup_event="pdf_parser_cancel_cleanup_failed",
        lifecycle_code="pdf_lifecycle",
        expected_ocr=False,
        error_source_format="pdf",
        absolute_deadline=deadline,
    )
    if not isinstance(result, ParsedPdfDocument):
        raise CandidateUnavailable("pdf", "pdf_crashed", "PDF parser subprocess failed")
    return _pdf_candidate_from_parsed(
        source_bytes,
        pdf_text=pdf_text,
        parsed=result,
        ocr_language=None,
    )


async def count_pdf_text_evidence_isolated(
    source_bytes: bytes,
    *,
    timeout_s: float = MAX_PDF_OCR_SECONDS,
    worker: PdfTextEvidenceWorker = count_pdf_text_evidence,
) -> PdfTextEvidenceCounts:
    """Return count-only PDF text evidence from a bounded, killable subprocess."""

    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise CandidateUnavailable(
            "pdf", "pdf_timeout", "PDF text evidence deadline is invalid"
        )
    deadline = time.monotonic() + timeout_s
    if not sys.platform.startswith("linux"):
        raise CandidateUnavailable(
            "pdf",
            "pdf_platform_unsupported",
            "Isolated PDF text evidence is unsupported on this platform",
        )
    if len(source_bytes) > MAX_ARXIV_PDF_BYTES:
        raise CandidateUnavailable("pdf", "source_too_large", "PDF input exceeds the safe limit")
    result = await _supervise_pdf_subprocess(
        _run_isolated_pdf_text_evidence,
        source_bytes,
        timeout_s,
        worker,
        task_name="alinea-pdf-text-evidence-supervisor",
        cleanup_event="pdf_text_evidence_cancel_cleanup_failed",
        lifecycle_code="pdf_lifecycle",
        absolute_deadline=deadline,
    )
    if not isinstance(result, PdfTextEvidenceCounts):
        raise CandidateUnavailable(
            "pdf", "pdf_crashed", "PDF text evidence subprocess failed"
        )
    return result


async def parse_pdf_ocr_candidate(
    source_bytes: bytes,
    *,
    pdf_text: str,
    ocr_language: str = "eng",
    timeout_s: float = MAX_PDF_OCR_SECONDS,
    max_output_bytes: int = MAX_PDF_OCR_OUTPUT_BYTES,
    admission_limit: int = 1,
    worker: PdfOcrWorker = _parse_pdf_ocr_document_trusted,
) -> SourceCandidate:
    """Parse the optional OCR candidate in a bounded, killable subprocess."""

    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise CandidateUnavailable("pdf_ocr", "ocr_timeout", "PDF OCR deadline is invalid")
    if not sys.platform.startswith("linux"):
        raise CandidateUnavailable(
            "pdf_ocr",
            "ocr_platform_unsupported",
            "PDF OCR is unsupported on this platform",
        )
    if type(admission_limit) is not int or not 1 <= admission_limit <= 4:
        raise CandidateUnavailable(
            "pdf_ocr", "ocr_lifecycle", "PDF OCR admission limit is invalid"
        )
    if max_output_bytes <= 0 or max_output_bytes > MAX_PDF_OCR_OUTPUT_BYTES:
        raise CandidateUnavailable(
            "pdf_ocr",
            "ocr_output_too_large",
            "PDF OCR output limit is invalid",
        )
    if len(source_bytes) > MAX_ARXIV_PDF_BYTES:
        raise CandidateUnavailable(
            "pdf_ocr",
            "source_too_large",
            "PDF OCR input exceeds the safe limit",
        )
    if len(ocr_language) > 64 or _OCR_LANGUAGE_RE.fullmatch(ocr_language) is None:
        raise CandidateUnavailable(
            "pdf_ocr",
            "ocr_language_invalid",
            "PDF OCR language is invalid",
        )
    deadline = time.monotonic() + timeout_s
    admission = _pdf_ocr_admission(admission_limit)
    try:
        await asyncio.wait_for(
            admission.semaphore.acquire(),
            timeout=max(0.0, deadline - time.monotonic()),
        )
    except TimeoutError as exc:
        raise CandidateUnavailable(
            "pdf_ocr", "ocr_timeout", "PDF OCR deadline was exceeded"
        ) from exc
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CandidateUnavailable(
                "pdf_ocr", "ocr_timeout", "PDF OCR deadline was exceeded"
            )
        result = await _supervise_pdf_subprocess(
            _run_isolated_pdf_ocr,
            source_bytes,
            ocr_language,
            remaining,
            max_output_bytes,
            worker,
            task_name="alinea-pdf-ocr-supervisor",
            cleanup_event="pdf_ocr_cancel_cleanup_failed",
            lifecycle_code="ocr_lifecycle",
            absolute_deadline=deadline,
        )
        if isinstance(result, ParsedPdfDocument):
            return _pdf_candidate_from_parsed(
                source_bytes,
                pdf_text=pdf_text,
                parsed=result,
                ocr_language=ocr_language,
            )
        raise CandidateUnavailable("pdf_ocr", "ocr_crashed", "PDF OCR subprocess failed")
    finally:
        admission.semaphore.release()


async def load_original_pdf(
    storage: S3Storage,
    paper_id: str,
    source_version: str,
    *,
    max_bytes: int = MAX_ARXIV_PDF_BYTES,
) -> bytes:
    """Load the canonical original-PDF object for an ingest source version."""

    return await storage.get_bounded(
        storage.sources_bucket,
        StorageKeys.original_pdf(paper_id, source_version),
        max_bytes=max_bytes,
    )


__all__ = [
    "PDF_OCR_CANDIDATE_VERSION",
    "CandidateUnavailable",
    "SourceCandidate",
    "count_pdf_text_evidence_isolated",
    "embedded_pdf_bytes",
    "load_original_pdf",
    "parse_html_candidate",
    "parse_latex_candidate",
    "parse_pdf_candidate",
    "parse_pdf_candidate_async",
    "parse_pdf_ocr_candidate",
]
