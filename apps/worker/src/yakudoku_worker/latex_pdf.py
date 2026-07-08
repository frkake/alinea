"""Build translated PDFs from arXiv LaTeX sources.

The builder keeps the original TeX commands/environments and replaces only
translatable text blocks with stored translation units. Figures, equations,
labels, citations, URLs, and hyperlinks remain LaTeX-native so the compiled
Japanese PDF stays as close as possible to the source layout.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import posixpath
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import (
    DocumentRevision,
    Paper,
    SourceAsset,
    TranslationSet,
    TranslationUnit,
)
from yakudoku_core.document.blocks import Block, DocumentContent
from yakudoku_core.parsing.latex_parser import (
    _BEGIN_RE,
    LatexArchive,
    _expand_includes,
    _read_braced,
    _read_environment,
    _resolve_bibliography,
    extract_latex_archive,
    select_main_tex,
)
from yakudoku_core.settings import CoreSettings
from yakudoku_core.storage.s3 import S3Storage, StorageKeys
from yakudoku_core.translation.pipeline import BLOCKING_FLAGS

PDF_BUILD_VERSION = "latex-ja-pdf-1.0.0"
DEFAULT_TEXLIVE_IMAGE = "yakudoku-texlive-ja:latest"

_SECTION_CMD_RE = re.compile(
    r"\\(?:section|subsection|subsubsection)\*?(?:\s*\[[^\]]*\])?\s*\{"
)
_CAPTION_CMD_RE = re.compile(r"\\caption\*?(?:\s*\[[^\]]*\])?\s*\{")
_ITEM_RE = re.compile(r"\\item\b\s*(?:\[[^\]]*\])?")
_OVERFULL_RE = re.compile(r"Overfull \\[hv]box \((?P<pt>[0-9.]+)pt too")
_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_PDF_BOUNDS_TOLERANCE_PT = 2.0
_LATEX_SOURCE_KINDS = ("arxiv_latex", "latex")

_SKIP_ENVS = {
    "equation",
    "align",
    "gather",
    "multline",
    "eqnarray",
    "tabular",
    "tabularx",
    "array",
    "tikzpicture",
    "picture",
    "verbatim",
    "lstlisting",
    "minted",
    "thebibliography",
}
_FIGURE_ENVS = {"figure", "wrapfigure"}
_TABLE_ENVS = {"table"}
_LIST_ENVS = {"itemize", "enumerate"}
_QUOTE_ENVS = {"quote", "quotation"}
_THEOREM_ENVS = {
    "theorem",
    "lemma",
    "corollary",
    "proposition",
    "definition",
    "remark",
    "claim",
    "example",
    "proof",
}
_FRONTMATTER_HINTS = (
    "\\maketitle",
    "\\title",
    "\\author",
    "\\date",
    "\\thanks",
    "\\affil",
    "\\institute",
)
_LEADING_PARAGRAPH_PREFIX_RE = re.compile(
    r"^(?P<prefix>\s*(?:(?:\\label\{[^}]*\}|\\noindent\b|\\par\b)\s*)*)",
    re.DOTALL,
)


class LatexPdfBuildError(RuntimeError):
    """Translated PDF build failed. ``kind`` is stable for log classification."""

    def __init__(self, kind: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail or {}


@dataclass(frozen=True)
class RenderedLatexSource:
    main_tex_name: str
    main_tex: str
    support_text_files: dict[str, str]
    binary_files: dict[str, bytes]
    replacements: dict[str, int]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LatexPdfBuildOutcome:
    built: bool
    translated_key: str | None = None
    bilingual_key: str | None = None
    warnings: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


class _TranslationCursor:
    """Consumes document blocks in source order while the TeX tree is transformed."""

    def __init__(self, content: DocumentContent, units: dict[str, TranslationUnit]) -> None:
        tracked = {
            "heading",
            "paragraph",
            "figure",
            "table",
            "list",
            "quote",
            "theorem",
            "footnote",
        }
        self.blocks = [block for _section, block in content.iter_blocks() if block.type in tracked]
        self.units = units
        self.pos = 0
        self.replacements: dict[str, int] = {}
        self.warnings: list[str] = []

    def take(self, *types: str) -> tuple[Block | None, TranslationUnit | None]:
        wanted = set(types)
        for idx in range(self.pos, len(self.blocks)):
            block = self.blocks[idx]
            if block.type not in wanted:
                continue
            self.pos = idx + 1
            unit = self.units.get(block.id)
            if unit is not None and _unit_is_displayable(unit):
                self.replacements[block.type] = self.replacements.get(block.type, 0) + 1
                return block, unit
            return block, None
        self.warnings.append(f"対応するブロックが見つかりません: {','.join(types)}")
        return None, None


def _unit_is_displayable(unit: TranslationUnit) -> bool:
    return bool(unit.text_ja) and not (set(unit.quality_flags or []) & BLOCKING_FLAGS)


def _latex_escape_text(text: str) -> str:
    repl = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "^": r"\^{}",
        "~": r"\~{}",
    }
    return "".join(repl.get(ch, ch) for ch in text)


def _inline_to_latex(inline: dict[str, Any]) -> str:
    t = inline.get("t")
    if t == "text":
        return _latex_escape_text(str(inline.get("v") or ""))
    if t == "emphasis":
        children = inline.get("children")
        inner = (
            "".join(_inline_to_latex(child) for child in children)
            if isinstance(children, list)
            else _latex_escape_text(str(inline.get("v") or ""))
        )
        return rf"\emph{{{inner}}}"
    if t == "math_inline":
        return f"${inline.get('v') or ''}$"
    if t == "citation":
        ref = str(inline.get("ref") or "").strip()
        return rf"\cite{{{ref}}}" if ref else ""
    if t == "ref":
        ref = str(inline.get("ref") or "").strip()
        if not ref:
            return _latex_escape_text(str(inline.get("v") or ""))
        return rf"\eqref{{{ref}}}" if inline.get("kind") == "equation" else rf"\ref{{{ref}}}"
    if t == "url":
        href = str(inline.get("href") or inline.get("v") or "").strip()
        label = _latex_escape_text(str(inline.get("v") or href))
        return rf"\href{{{href}}}{{{label}}}" if href else label
    if t == "code_inline":
        return rf"\texttt{{{_latex_escape_text(str(inline.get('v') or ''))}}}"
    if t == "footnote_ref":
        return ""
    return _latex_escape_text(str(inline.get("v") or ""))


def _unit_to_latex(unit: TranslationUnit | None) -> str | None:
    if unit is None or not _unit_is_displayable(unit):
        return None
    content = unit.content_ja
    if isinstance(content, list) and all(isinstance(item, dict) for item in content):
        return "".join(_inline_to_latex(item) for item in content)
    return _latex_escape_text(unit.text_ja)


def _inject_japanese_preamble(tex: str) -> str:
    if "% yakudoku-ja-pdf" in tex:
        return tex
    m = re.search(r"\\begin\{document\}", tex)
    if not m:
        raise LatexPdfBuildError("invalid_latex", "main TeX has no \\begin{document}")
    preamble = r"""
% yakudoku-ja-pdf: Japanese PDF build support
\usepackage{iftex}
\usepackage{graphicx}
\usepackage{amsmath,amssymb}
\IfFileExists{xurl.sty}{\usepackage{xurl}}{\usepackage{url}}
\ifLuaTeX
  \usepackage{luatexja}
  \usepackage{luatexja-fontspec}
  \IfFontExistsTF{Noto Serif CJK JP}{\setmainjfont{Noto Serif CJK JP}}{}
  \IfFontExistsTF{Noto Sans CJK JP}{\setsansjfont{Noto Sans CJK JP}}{}
  \IfFontExistsTF{Noto Sans Mono CJK JP}{\setmonofont{Noto Sans Mono CJK JP}}{}
\fi
\usepackage{hyperref}
\AtBeginDocument{\sloppy\emergencystretch=3em\tolerance=9999\hbadness=10000\hfuzz=1pt}
"""
    return tex[: m.start()] + preamble + "\n" + tex[m.start() :]


def _replace_braced_command_arg(
    text: str, match: re.Match[str], replacement: str | None
) -> tuple[str, int]:
    original, end = _read_braced(text, match.end() - 1)
    if replacement is None:
        return text[match.start() : end], end
    return text[match.start() : match.end() - 1] + "{" + replacement + "}", end


def _replace_caption(inner: str, cursor: _TranslationCursor, block_type: str) -> str:
    _block, unit = cursor.take(block_type)
    replacement = _unit_to_latex(unit)
    m = _CAPTION_CMD_RE.search(inner)
    if m is None or replacement is None:
        return inner
    repl, end = _replace_braced_command_arg(inner, m, replacement)
    return inner[: m.start()] + repl + inner[end:]


def _split_list_translation(text: str) -> list[str]:
    items = [part.strip() for part in re.split(r"(?:\n\s*-\s*|\n+)", text) if part.strip()]
    return items or ([text.strip()] if text.strip() else [])


def _replace_list_items(inner: str, cursor: _TranslationCursor) -> str:
    _block, unit = cursor.take("list")
    if unit is None or not _unit_is_displayable(unit):
        return inner
    translations = [_latex_escape_text(item) for item in _split_list_translation(unit.text_ja)]
    matches = list(_ITEM_RE.finditer(inner))
    if not matches or not translations:
        return inner
    out: list[str] = []
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(inner)
        out.append(inner[match.start() : match.end()])
        out.append(" ")
        out.append(translations[idx] if idx < len(translations) else inner[match.end() : end])
        out.append("\n")
    return "".join(out)


def _replace_whole_env(inner: str, cursor: _TranslationCursor, block_type: str) -> str:
    _block, unit = cursor.take(block_type)
    replacement = _unit_to_latex(unit)
    if replacement is None:
        return inner
    labels = "".join(m.group(0) + "\n" for m in re.finditer(r"\\label\{[^}]*\}", inner))
    return labels + replacement


def _replace_paragraphs(text: str, cursor: _TranslationCursor) -> str:
    parts = re.split(r"(\n\s*\n+)", text)
    out: list[str] = []
    for idx, chunk in enumerate(parts):
        if idx % 2 == 1:
            out.append(chunk)
            continue
        stripped = chunk.strip()
        if not stripped:
            out.append(chunk)
            continue
        if any(hint in stripped for hint in _FRONTMATTER_HINTS):
            out.append(chunk)
            continue
        if stripped.startswith("\\") and not stripped.startswith(
            ("\\noindent", "\\label", "\\par")
        ):
            out.append(chunk)
            continue
        _block, unit = cursor.take("paragraph", "footnote")
        replacement = _unit_to_latex(unit)
        if replacement is None:
            out.append(chunk)
            continue
        prefix_match = _LEADING_PARAGRAPH_PREFIX_RE.match(chunk)
        prefix = prefix_match.group("prefix") if prefix_match else ""
        trailing = "\n" if chunk.endswith("\n") else ""
        out.append(prefix + replacement + trailing)
    return "".join(out)


def _transform_env(
    name: str, inner: str, cursor: _TranslationCursor, abstract_ja: str | None
) -> str:
    base = name.rstrip("*")
    if base in _FIGURE_ENVS:
        return _replace_caption(inner, cursor, "figure")
    if base in _TABLE_ENVS:
        return _replace_caption(inner, cursor, "table")
    if base in _LIST_ENVS:
        return _replace_list_items(inner, cursor)
    if base in _QUOTE_ENVS:
        return _replace_whole_env(inner, cursor, "quote")
    if base in _THEOREM_ENVS:
        return _replace_whole_env(inner, cursor, "theorem")
    if base == "abstract" and abstract_ja:
        return _latex_escape_text(abstract_ja)
    if base in _SKIP_ENVS:
        return inner
    return _transform_latex_text(inner, cursor, abstract_ja)


def _transform_latex_text(text: str, cursor: _TranslationCursor, abstract_ja: str | None) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        m_sec = _SECTION_CMD_RE.search(text, i)
        m_env = _BEGIN_RE.search(text, i)
        matches = [m for m in (m_sec, m_env) if m is not None]
        if not matches:
            out.append(_replace_paragraphs(text[i:], cursor))
            break
        match = min(matches, key=lambda m: m.start())
        out.append(_replace_paragraphs(text[i : match.start()], cursor))
        if match is m_sec:
            _block, unit = cursor.take("heading")
            repl, end = _replace_braced_command_arg(text, match, _unit_to_latex(unit))
            out.append(repl)
            i = end
            continue
        name = match.group(1)
        inner, end = _read_environment(text, match.end(), name)
        end_token = rf"\end{{{name}}}"
        inner_end = end - len(end_token)
        out.append(text[match.start() : match.end()])
        out.append(_transform_env(name, inner, cursor, abstract_ja))
        out.append(text[inner_end:end])
        i = end
    return "".join(out)


def render_translated_latex_source(
    archive: LatexArchive,
    content: DocumentContent,
    units: dict[str, TranslationUnit],
    *,
    abstract_ja: str | None = None,
) -> RenderedLatexSource:
    """Return a single translated main TeX file plus original support files."""

    main_name, main_tex = select_main_tex(archive.text_files)
    expanded = _expand_includes(main_tex, archive.text_files, {main_name})
    expanded = _resolve_bibliography(expanded, archive.text_files)
    cursor = _TranslationCursor(content, units)
    translated = _transform_latex_text(expanded, cursor, abstract_ja)
    translated = _inject_japanese_preamble(translated)
    support_text = dict(archive.text_files)
    support_text.pop(main_name, None)
    return RenderedLatexSource(
        main_tex_name="main.ja.tex",
        main_tex=translated,
        support_text_files=support_text,
        binary_files=archive.binary_files,
        replacements=cursor.replacements,
        warnings=cursor.warnings,
    )


def _safe_write_path(root: Path, name: str) -> Path | None:
    clean = posixpath.normpath(name.replace("\\", "/")).removeprefix("./")
    if clean in {"", "."} or clean == ".." or clean.startswith("../"):
        return None
    path = root / clean
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _write_rendered_source(root: Path, rendered: RenderedLatexSource) -> None:
    main = root / rendered.main_tex_name
    main.write_text(rendered.main_tex, encoding="utf-8")
    for name, text in rendered.support_text_files.items():
        path = _safe_write_path(root, name)
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for name, data in rendered.binary_files.items():
        path = _safe_write_path(root, name)
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _find_overfull_boxes(log_text: str) -> list[str]:
    findings: list[str] = []
    for line in log_text.splitlines():
        m = _OVERFULL_RE.search(line)
        if m and float(m.group("pt")) > 1.0:
            findings.append(line.strip())
    return findings


def _compile_with_docker(
    workdir: Path, main_tex_name: str, *, image: str, timeout_s: int
) -> bytes:
    output_pdf = workdir / main_tex_name.replace(".tex", ".pdf")
    cmd = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "-v",
        f"{workdir}:/work",
        "-w",
        "/work",
        image,
        "latexmk",
        "-lualatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        main_tex_name,
    ]
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise LatexPdfBuildError("docker_unavailable", "docker command is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise LatexPdfBuildError("timeout", "LaTeX PDF build timed out") from exc

    log_path = workdir / main_tex_name.replace(".tex", ".log")
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    if result.returncode != 0 or not output_pdf.exists():
        detail = {
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
            "log": log_text[-4000:],
        }
        raise LatexPdfBuildError("compile_failed", "LaTeX PDF build failed", detail=detail)
    # Some arXiv sources already contain long display math that reports Overfull boxes
    # while still rendering inside the PDF page. Validate the compiled PDF geometry below.
    return output_pdf.read_bytes()


def _rect_exceeds_page(rect: fitz.Rect, page_rect: fitz.Rect) -> bool:
    tolerance = _PDF_BOUNDS_TOLERANCE_PT
    return (
        float(rect.x0) < float(page_rect.x0) - tolerance
        or float(rect.y0) < float(page_rect.y0) - tolerance
        or float(rect.x1) > float(page_rect.x1) + tolerance
        or float(rect.y1) > float(page_rect.y1) + tolerance
    )


def _find_pdf_page_bound_violations(doc: fitz.Document) -> list[str]:
    violations: list[str] = []
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_rect = page.rect
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            bbox = block.get("bbox")
            if not bbox:
                continue
            rect = fitz.Rect(bbox)
            if not rect.is_empty and _rect_exceeds_page(rect, page_rect):
                kind = "image" if block.get("type") == 1 else "text"
                violations.append(
                    f"page={page_index + 1} kind={kind} "
                    f"bbox=({rect.x0:.2f},{rect.y0:.2f},{rect.x1:.2f},{rect.y1:.2f}) "
                    f"page=({page_rect.x0:.2f},{page_rect.y0:.2f},"
                    f"{page_rect.x1:.2f},{page_rect.y1:.2f})"
                )
        for link in page.get_links():
            link_rect = link.get("from")
            if isinstance(link_rect, fitz.Rect) and _rect_exceeds_page(link_rect, page_rect):
                violations.append(
                    f"page={page_index + 1} kind=link "
                    f"bbox=({link_rect.x0:.2f},{link_rect.y0:.2f},"
                    f"{link_rect.x1:.2f},{link_rect.y1:.2f})"
                )
    return violations


def _validate_translated_pdf(pdf_bytes: bytes) -> None:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise LatexPdfBuildError("invalid_pdf", "compiled PDF cannot be opened") from exc
    try:
        if doc.page_count < 1:
            raise LatexPdfBuildError("invalid_pdf", "compiled PDF has no pages")
        sample = "\n".join(doc.load_page(i).get_text("text") for i in range(min(doc.page_count, 3)))
        if not _JAPANESE_RE.search(sample):
            raise LatexPdfBuildError("missing_japanese_text", "compiled PDF has no Japanese text")
        bound_violations = _find_pdf_page_bound_violations(doc)
        if bound_violations:
            raise LatexPdfBuildError(
                "page_bounds",
                "compiled PDF has content outside page bounds",
                detail={"violations": bound_violations[:20]},
            )
    finally:
        doc.close()


def _build_bilingual_pdf(original_pdf: bytes, translated_pdf: bytes) -> bytes:
    src = fitz.open(stream=original_pdf, filetype="pdf")
    ja = fitz.open(stream=translated_pdf, filetype="pdf")
    out = fitz.open()
    try:
        page_count = max(src.page_count, ja.page_count)
        if page_count < 1:
            raise LatexPdfBuildError("invalid_pdf", "cannot build bilingual PDF from empty inputs")
        for index in range(page_count):
            src_page = src.load_page(index) if index < src.page_count else None
            ja_page = ja.load_page(index) if index < ja.page_count else None
            fallback_rect = fitz.Rect(0, 0, 612, 792)
            src_rect = (
                src_page.rect
                if src_page is not None
                else ja_page.rect
                if ja_page
                else fallback_rect
            )
            ja_rect = ja_page.rect if ja_page is not None else src_rect
            height = max(src_rect.height, ja_rect.height)
            half_width = max(src_rect.width, ja_rect.width)
            page = out.new_page(width=half_width * 2, height=height)
            if src_page is not None:
                page.show_pdf_page(fitz.Rect(0, 0, half_width, height), src, src_page.number)
            if ja_page is not None:
                page.show_pdf_page(
                    fitz.Rect(half_width, 0, half_width * 2, height), ja, ja_page.number
                )
        return bytes(out.tobytes(deflate=True, garbage=4))
    finally:
        out.close()
        src.close()
        ja.close()


async def _compile_rendered_source(
    rendered: RenderedLatexSource, *, image: str, timeout_s: int
) -> bytes:
    def _run() -> bytes:
        with tempfile.TemporaryDirectory(prefix="yakudoku-latex-") as tmp:
            root = Path(tmp)
            _write_rendered_source(root, rendered)
            return _compile_with_docker(
                root, rendered.main_tex_name, image=image, timeout_s=timeout_s
            )

    return await asyncio.to_thread(_run)


async def _find_asset(
    session: AsyncSession,
    *,
    paper_id: str,
    source_version: str,
    kind: str,
) -> SourceAsset | None:
    return (
        (
            await session.execute(
                select(SourceAsset)
                .where(
                    SourceAsset.paper_id == paper_id,
                    SourceAsset.source_version == source_version,
                    SourceAsset.kind == kind,
                )
                .order_by(SourceAsset.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _record_pdf_asset(
    session: AsyncSession,
    *,
    paper_id: str,
    source_version: str,
    kind: str,
    storage_key: str,
    source_url: str,
    data: bytes,
) -> None:
    existing = await _find_asset(
        session, paper_id=paper_id, source_version=source_version, kind=kind
    )
    if existing is not None:
        existing.storage_key = storage_key
        existing.byte_size = len(data)
        existing.sha256 = hashlib.sha256(data).hexdigest()
        existing.content_type = "application/pdf"
        existing.source_url = source_url
        return
    session.add(
        SourceAsset(
            paper_id=paper_id,
            kind=kind,
            source_url=source_url,
            source_version=source_version,
            storage_key=storage_key,
            content_type="application/pdf",
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
    )


async def _load_original_pdf(
    session: AsyncSession, storage: S3Storage, *, paper_id: str, source_version: str
) -> bytes | None:
    asset = (
        (
            await session.execute(
                select(SourceAsset)
                .where(
                    SourceAsset.paper_id == paper_id,
                    SourceAsset.source_version == source_version,
                    SourceAsset.kind.in_(("pdf", "arxiv_pdf", "pdf_upload", "extension_capture")),
                )
                .order_by(SourceAsset.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if asset is None:
        return None
    return await storage.get(storage.sources_bucket, asset.storage_key)


async def _find_latex_asset(
    session: AsyncSession, *, paper_id: str, source_version: str
) -> SourceAsset | None:
    return (
        (
            await session.execute(
                select(SourceAsset)
                .where(
                    SourceAsset.paper_id == paper_id,
                    SourceAsset.source_version == source_version,
                    SourceAsset.kind.in_(_LATEX_SOURCE_KINDS),
                )
                .order_by(SourceAsset.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def _is_missing_s3_object(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    return str(error.get("Code") or "") in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}


async def build_latex_translation_pdfs_if_ready(
    session: AsyncSession,
    storage: S3Storage,
    settings: CoreSettings,
    *,
    set_id: str,
) -> LatexPdfBuildOutcome:
    """Build and persist translated/bilingual PDFs when the translation set is complete."""

    tset = await session.get(TranslationSet, set_id)
    if tset is None:
        return LatexPdfBuildOutcome(False, skipped_reason="translation_set_missing")
    if tset.status != "complete":
        return LatexPdfBuildOutcome(False, skipped_reason="translation_set_not_complete")
    revision = await session.get(DocumentRevision, str(tset.revision_id))
    if revision is None:
        return LatexPdfBuildOutcome(False, skipped_reason="revision_missing")
    if revision.source_format != "latex":
        return LatexPdfBuildOutcome(False, skipped_reason="not_latex")
    paper = await session.get(Paper, str(revision.paper_id))
    if paper is None:
        return LatexPdfBuildOutcome(False, skipped_reason="paper_missing")

    paper_id = str(paper.id)
    source_version = revision.source_version
    style = tset.style
    translated_key = StorageKeys.translated_pdf(paper_id, source_version, style)
    bilingual_key = StorageKeys.bilingual_pdf(paper_id, source_version, style)

    existing_translated = await _find_asset(
        session, paper_id=paper_id, source_version=source_version, kind="translated_pdf"
    )
    existing_bilingual = await _find_asset(
        session, paper_id=paper_id, source_version=source_version, kind="bilingual_pdf"
    )
    if existing_translated is not None and existing_bilingual is not None:
        return LatexPdfBuildOutcome(
            False,
            translated_key=existing_translated.storage_key,
            bilingual_key=existing_bilingual.storage_key,
            skipped_reason="already_built",
        )

    latex_asset = await _find_latex_asset(
        session, paper_id=paper_id, source_version=source_version
    )
    if latex_asset is None:
        return LatexPdfBuildOutcome(False, skipped_reason="latex_asset_missing")
    try:
        latex_bytes = await storage.get(storage.sources_bucket, latex_asset.storage_key)
    except ClientError as exc:
        if _is_missing_s3_object(exc):
            return LatexPdfBuildOutcome(False, skipped_reason="latex_asset_object_missing")
        raise
    archive = extract_latex_archive(latex_bytes)
    content = DocumentContent.model_validate(revision.content)
    units = {
        unit.block_id: unit
        for unit in (
            await session.execute(select(TranslationUnit).where(TranslationUnit.set_id == set_id))
        ).scalars()
    }
    rendered = render_translated_latex_source(
        archive,
        content,
        units,
        abstract_ja=paper.abstract_ja,
    )
    image = settings.yakudoku_texlive_image or DEFAULT_TEXLIVE_IMAGE
    translated_pdf = await _compile_rendered_source(
        rendered,
        image=image,
        timeout_s=settings.yakudoku_latex_build_timeout_s,
    )
    _validate_translated_pdf(translated_pdf)
    await storage.put(
        storage.sources_bucket,
        translated_key,
        translated_pdf,
        content_type="application/pdf",
        metadata={"build": PDF_BUILD_VERSION, "translation_set_id": set_id},
    )
    await _record_pdf_asset(
        session,
        paper_id=paper_id,
        source_version=source_version,
        kind="translated_pdf",
        storage_key=translated_key,
        source_url=f"translation-set:{set_id}",
        data=translated_pdf,
    )

    bilingual_written_key: str | None = None
    original_pdf = await _load_original_pdf(
        session, storage, paper_id=paper_id, source_version=source_version
    )
    if original_pdf is not None:
        bilingual_pdf = await asyncio.to_thread(_build_bilingual_pdf, original_pdf, translated_pdf)
        await storage.put(
            storage.sources_bucket,
            bilingual_key,
            bilingual_pdf,
            content_type="application/pdf",
            metadata={"build": PDF_BUILD_VERSION, "translation_set_id": set_id},
        )
        await _record_pdf_asset(
            session,
            paper_id=paper_id,
            source_version=source_version,
            kind="bilingual_pdf",
            storage_key=bilingual_key,
            source_url=f"translation-set:{set_id}",
            data=bilingual_pdf,
        )
        bilingual_written_key = bilingual_key

    stats = dict(revision.stats or {})
    pdf_stats = dict(stats.get("translated_pdf") or {})
    pdf_stats[style] = {
        "build_version": PDF_BUILD_VERSION,
        "translation_set_id": set_id,
        "storage_key": translated_key,
        "bilingual_storage_key": bilingual_written_key,
        "replacements": rendered.replacements,
        "built_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    stats["translated_pdf"] = pdf_stats
    revision.stats = stats
    await session.commit()
    return LatexPdfBuildOutcome(
        True,
        translated_key=translated_key,
        bilingual_key=bilingual_written_key,
        warnings=rendered.warnings,
    )
