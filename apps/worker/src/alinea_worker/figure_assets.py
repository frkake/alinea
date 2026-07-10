"""Safe resolution, fetching, and raster validation for paper figures.

The structured document initially contains author-controlled LaTeX paths or HTML
URLs.  This module keeps those values out of public asset keys and only returns a
payload after its actual bytes have been decoded (or a document format has been
rasterized) successfully.
"""

from __future__ import annotations

import io
import math
import posixpath
import re
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

import fitz
import httpx
from PIL import Image, UnidentifiedImageError

SUPPORTED_EXTENSIONS = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".eps",
    ".ps",
    ".svg",
)
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_IMAGE_DIMENSION = 20_000
MAX_IMAGE_PIXELS = 80_000_000
MAX_REDIRECTS = 3

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_GRAPHICSPATH_RE = re.compile(r"\\graphicspath(?![A-Za-z])")
_SVG_UNSAFE_DECLARATION_RE = re.compile(rb"<!\s*(?:doctype|entity)\b", re.IGNORECASE)
_SVG_EXTERNAL_REFERENCE_RE = re.compile(
    rb"(?:"
    rb"(?:href|src)\s*=\s*['\"]\s*(?:https?:|file:|//)"
    rb"|url\s*\(\s*['\"]?\s*(?:https?:|file:|//)"
    rb"|@import\s+['\"]\s*(?:https?:|file:|//)"
    rb")",
    re.IGNORECASE,
)
_RASTER_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}


class FigureAssetError(Exception):
    """A stable, non-secret failure suitable for ingest diagnostics."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class FigureAssetPayload:
    """A decoded and validated display payload."""

    content: bytes
    ext: str
    content_type: str
    width: int
    height: int


@dataclass(frozen=True)
class ResolvedLatexAsset:
    """The unique archive member selected for a LaTeX figure."""

    source_name: str
    payload: FigureAssetPayload


PostscriptConverter = Callable[[bytes, str], bytes]


def _has_control(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def _safe_relative_path(value: str, *, allow_parent: bool) -> str | None:
    clean = value.strip()
    if (
        not clean
        or clean in {".", ".."}
        or _has_control(value)
        or "\\" in clean
        or "{" in clean
        or "}" in clean
        or "?" in clean
        or "#" in clean
        or clean.startswith("/")
        or "//" in clean
        or _SCHEME_RE.match(clean) is not None
    ):
        return None
    parts = PurePosixPath(clean).parts
    if not parts or any(part in {"", "."} for part in parts):
        return None
    if not allow_parent and ".." in parts:
        return None
    return clean


def _archive_path(value: str) -> str | None:
    clean = _safe_relative_path(value, allow_parent=False)
    if clean is None:
        return None
    normalized = posixpath.normpath(clean).removeprefix("./")
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        return None
    return normalized


def normalize_requested_asset(value: str) -> str | None:
    """Return a safe archive-relative figure request, or ``None``.

    Backslashes are rejected rather than treated as path separators because an
    author-controlled LaTeX control sequence must never become a storage lookup.
    Parent components are reserved for validated ``graphicspath`` declarations.
    """

    clean = _safe_relative_path(value, allow_parent=False)
    if clean is None:
        return None
    normalized = _archive_path(clean)
    if normalized is None:
        return None
    suffix = PurePosixPath(normalized).suffix
    if suffix and suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None
    return normalized


def _joined_archive_path(*parts: str) -> str | None:
    joined = posixpath.join(*(part for part in parts if part not in {"", "."}))
    normalized = posixpath.normpath(joined).removeprefix("./")
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        return None
    return _archive_path(normalized)


def asset_candidates(
    requested: str,
    main_tex_name: str | None,
    graphicspaths: Sequence[str],
) -> list[str]:
    """Expand a LaTeX figure request into safe archive-relative candidates."""

    clean = normalize_requested_asset(requested)
    if clean is None:
        return []

    main_path = _archive_path(main_tex_name) if main_tex_name else None
    main_dir = posixpath.dirname(main_path) if main_path else ""
    roots: list[str] = [""]
    if main_dir:
        roots.append(main_dir)
    for declared in graphicspaths:
        path = _safe_relative_path(declared, allow_parent=True)
        if path is None:
            continue
        relative_to_main = _joined_archive_path(main_dir, path)
        if relative_to_main is not None:
            roots.append(relative_to_main)
        relative_to_archive = _joined_archive_path(path)
        if relative_to_archive is not None:
            roots.append(relative_to_archive)

    suffixes: tuple[str, ...]
    if PurePosixPath(clean).suffix:
        suffixes = ("",)
    else:
        suffixes = ("", *SUPPORTED_EXTENSIONS)

    output: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for suffix in suffixes:
            candidate = _joined_archive_path(root, f"{clean}{suffix}")
            if candidate is not None and candidate not in seen:
                seen.add(candidate)
                output.append(candidate)
    return output


def _read_braced(text: str, open_position: int) -> tuple[str, int] | None:
    if open_position >= len(text) or text[open_position] != "{":
        return None
    depth = 1
    escaped = False
    position = open_position + 1
    while position < len(text):
        char = text[position]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_position + 1 : position], position + 1
        position += 1
    return None


def _strip_latex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        comment_position: int | None = None
        for position, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = position - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                comment_position = position
                break
        lines.append(line if comment_position is None else line[:comment_position])
    return "\n".join(lines)


def _graphicspath_entries(text: str) -> list[str]:
    text = _strip_latex_comments(text)
    entries: list[str] = []
    for match in _GRAPHICSPATH_RE.finditer(text):
        position = match.end()
        while position < len(text) and text[position].isspace():
            position += 1
        outer = _read_braced(text, position)
        if outer is None:
            continue
        body, _end = outer
        inner_position = 0
        while inner_position < len(body):
            while inner_position < len(body) and body[inner_position].isspace():
                inner_position += 1
            inner = _read_braced(body, inner_position)
            if inner is None:
                break
            value, inner_position = inner
            clean = value.strip()
            if _safe_relative_path(clean, allow_parent=True) is not None:
                entries.append(clean)
    return entries


def extract_graphicspaths(text_files: Mapping[str, str], main_tex_name: str) -> tuple[str, ...]:
    """Extract safe ``graphicspath`` entries from the selected source set.

    The main document is scanned first, then included/style files in stable name
    order.  Resolution later checks both main-directory and archive-root bases and
    rejects a request if those bases produce more than one actual member.
    """

    names: list[str] = []
    if main_tex_name in text_files:
        names.append(main_tex_name)
    names.extend(sorted(name for name in text_files if name != main_tex_name))
    output: list[str] = []
    seen: set[str] = set()
    for name in names:
        for entry in _graphicspath_entries(text_files[name]):
            if entry not in seen:
                seen.add(entry)
                output.append(entry)
    return tuple(output)


def _check_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise FigureAssetError("invalid_image", "figure has non-positive dimensions")
    if (
        width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise FigureAssetError("image_too_large", "figure dimensions exceed the safe limit")


def _check_input_size(data: bytes) -> None:
    if not data:
        raise FigureAssetError("invalid_image", "figure payload is empty")
    if len(data) > MAX_ASSET_BYTES:
        raise FigureAssetError("asset_too_large", "figure payload exceeds the safe byte limit")


def validate_image_payload(
    data: bytes,
    *,
    source_name: str = "",
    content_type: str | None = None,
) -> FigureAssetPayload:
    """Decode a raster fully and derive its format from bytes, not metadata."""

    del source_name, content_type  # Deliberately untrusted hints.
    _check_input_size(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image_format = str(image.format or "").upper()
                width, height = image.size
                _check_dimensions(width, height)
                image.verify()
            with Image.open(io.BytesIO(data)) as decoded:
                frame_count = int(getattr(decoded, "n_frames", 1))
                if frame_count <= 0:
                    raise FigureAssetError("invalid_image", "figure has no raster frames")
                total_pixels = 0
                for frame_index in range(frame_count):
                    decoded.seek(frame_index)
                    frame_width, frame_height = decoded.size
                    _check_dimensions(frame_width, frame_height)
                    total_pixels += frame_width * frame_height
                    if total_pixels > MAX_IMAGE_PIXELS:
                        raise FigureAssetError(
                            "image_too_large", "animated figure exceeds the safe pixel limit"
                        )
                    decoded.load()
    except FigureAssetError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise FigureAssetError(
            "image_too_large", "figure dimensions exceed the safe limit"
        ) from exc
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        raise FigureAssetError("invalid_image", "figure bytes are not a complete raster") from exc

    normalized = _RASTER_FORMATS.get(image_format)
    if normalized is None:
        raise FigureAssetError("unsupported_figure_format", "decoded raster format is unsupported")
    ext, normalized_content_type = normalized
    return FigureAssetPayload(data, ext, normalized_content_type, width, height)


def _render_document(data: bytes, filetype: str) -> FigureAssetPayload:
    try:
        with fitz.open(stream=data, filetype=filetype) as document:
            if document.page_count < 1:
                raise FigureAssetError("invalid_figure_document", "figure document has no pages")
            page = document.load_page(0)
            rect = page.rect
            if not all(math.isfinite(value) for value in (rect.width, rect.height)):
                raise FigureAssetError(
                    "invalid_figure_document", "figure document dimensions are invalid"
                )
            width = math.ceil(rect.width * 2)
            height = math.ceil(rect.height * 2)
            _check_dimensions(width, height)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            rendered = bytes(pixmap.tobytes("png"))
    except FigureAssetError:
        raise
    except Exception as exc:
        raise FigureAssetError(
            "invalid_figure_document", f"{filetype.upper()} figure could not be rendered"
        ) from exc
    return validate_image_payload(rendered, source_name="rendered.png", content_type="image/png")


def _is_svg(data: bytes) -> bool:
    prefix = data[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    return prefix.startswith(b"<svg") or (prefix.startswith(b"<?xml") and b"<svg" in prefix)


def _is_supported_raster(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP")
    )


def _render_svg(data: bytes) -> FigureAssetPayload:
    if (
        _SVG_UNSAFE_DECLARATION_RE.search(data) is not None
        or _SVG_EXTERNAL_REFERENCE_RE.search(data) is not None
    ):
        raise FigureAssetError("unsafe_vector", "SVG contains an external or active resource")
    try:
        return _render_document(data, "svg")
    except FigureAssetError as exc:
        if exc.code in {"image_too_large", "asset_too_large", "unsafe_vector"}:
            raise
        raise FigureAssetError("invalid_vector", "SVG figure could not be rasterized") from exc


def _convert_postscript_default(data: bytes, source_format: str) -> bytes:
    return _render_document(data, source_format).content


def _normalize_raster_to_png(payload: FigureAssetPayload) -> FigureAssetPayload:
    if payload.ext == "png":
        return payload
    try:
        output = io.BytesIO()
        with Image.open(io.BytesIO(payload.content)) as image:
            image.seek(0)
            normalized = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            normalized.save(output, format="PNG")
    except (OSError, SyntaxError, ValueError) as exc:
        raise FigureAssetError(
            "conversion_failed", "converted figure could not be normalized to PNG"
        ) from exc
    return validate_image_payload(
        output.getvalue(), source_name="converted.png", content_type="image/png"
    )


def _postscript_payload(
    data: bytes,
    source_format: str,
    converter: PostscriptConverter | None,
) -> FigureAssetPayload:
    try:
        converted = (
            converter(data, source_format)
            if converter is not None
            else _convert_postscript_default(data, source_format)
        )
    except Exception as exc:
        code = "conversion_failed" if converter is not None else "conversion_unavailable"
        raise FigureAssetError(code, "PostScript raster conversion is unavailable") from exc
    try:
        payload = validate_image_payload(
            converted, source_name=f"converted.{source_format}", content_type="image/png"
        )
        return _normalize_raster_to_png(payload)
    except FigureAssetError as exc:
        raise FigureAssetError(
            "conversion_failed", "PostScript converter returned an invalid raster"
        ) from exc


def figure_asset_payload(
    data: bytes,
    *,
    source_name: str,
    content_type: str | None = None,
    postscript_converter: PostscriptConverter | None = None,
) -> FigureAssetPayload:
    """Convert a supported figure into a validated browser-display payload."""

    _check_input_size(data)
    normalized_name = source_name.split("?", 1)[0]
    suffix = PurePosixPath(normalized_name).suffix.lower()
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    stripped = data[:1024].lstrip()
    if _is_supported_raster(data):
        return validate_image_payload(data, source_name=source_name, content_type=content_type)
    if stripped.startswith(b"%PDF-"):
        return _render_document(data, "pdf")
    if _is_svg(data) or suffix == ".svg" or normalized_content_type == "image/svg+xml":
        return _render_svg(data)
    if stripped.startswith(b"%!PS-Adobe") or suffix in {".eps", ".ps"}:
        source_format = "eps" if suffix == ".eps" or b"EPSF" in stripped[:128] else "ps"
        return _postscript_payload(data, source_format, postscript_converter)
    return validate_image_payload(data, source_name=source_name, content_type=content_type)


def resolve_latex_asset(
    *,
    binary_files: Mapping[str, bytes],
    requested: str,
    main_tex_name: str | None,
    graphicspaths: Sequence[str],
    postscript_converter: PostscriptConverter | None = None,
) -> ResolvedLatexAsset:
    """Resolve exactly one safe archive member and validate its display payload."""

    candidates = asset_candidates(requested, main_tex_name, graphicspaths)
    if not candidates:
        raise FigureAssetError("invalid_asset_path", "LaTeX figure path is unsafe")

    members_by_casefold: dict[str, list[str]] = {}
    for source_name in binary_files:
        normalized = _archive_path(source_name)
        if normalized is not None:
            members_by_casefold.setdefault(normalized.casefold(), []).append(source_name)

    matched: set[str] = set()
    for candidate in candidates:
        matched.update(members_by_casefold.get(candidate.casefold(), []))
    if not matched:
        raise FigureAssetError("asset_not_found", "LaTeX figure member was not found")
    if len(matched) != 1:
        raise FigureAssetError("asset_ambiguous", "LaTeX figure path has multiple matches")

    source_name = next(iter(matched))
    payload = figure_asset_payload(
        binary_files[source_name],
        source_name=source_name,
        postscript_converter=postscript_converter,
    )
    return ResolvedLatexAsset(source_name, payload)


def _origin(split: SplitResult) -> tuple[str, str, int | None]:
    scheme = split.scheme.lower()
    hostname = split.hostname
    try:
        port = split.port
    except ValueError as exc:
        raise FigureAssetError("unsafe_asset_url", "figure URL port is invalid") from exc
    if scheme == "http" and port == 80:
        port = None
    if scheme == "https" and port == 443:
        port = None
    return scheme, str(hostname or "").lower(), port


def _safe_url_path(path: str, allowed_prefix: str) -> bool:
    if not path.startswith("/") or "\\" in path or "//" in path or _has_control(path):
        return False
    decoded = path
    for _attempt in range(5):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        return False
    if "\\" in decoded or "//" in decoded or _has_control(decoded):
        return False
    parts = PurePosixPath(decoded).parts
    if any(part in {".", ".."} for part in parts):
        return False
    normalized = posixpath.normpath(decoded)
    return normalized.startswith(allowed_prefix) and normalized != allowed_prefix.rstrip("/")


def html_asset_url(base: str, versioned: str, source: str) -> str:
    """Normalize an arXiv HTML figure URL while pinning it to one origin/version."""

    if _has_control(base) or _has_control(versioned) or _has_control(source):
        raise FigureAssetError("unsafe_asset_url", "figure URL contains control characters")
    base_parts = urlsplit(base)
    if (
        base_parts.scheme.lower() not in {"http", "https"}
        or not base_parts.hostname
        or base_parts.username is not None
        or base_parts.password is not None
        or base_parts.query
        or base_parts.fragment
    ):
        raise FigureAssetError("unsafe_asset_url", "configured figure origin is invalid")
    if not versioned or versioned in {".", ".."} or _VERSION_RE.fullmatch(versioned) is None:
        raise FigureAssetError("unsafe_asset_url", "arXiv version path is invalid")

    raw = source.strip()
    if not raw or "\\" in raw:
        raise FigureAssetError("unsafe_asset_url", "figure URL is invalid")
    source_parts = urlsplit(raw)
    if source_parts.fragment or _has_control(source_parts.query):
        raise FigureAssetError("unsafe_asset_url", "figure URL contains unsafe suffix data")

    if source_parts.scheme or source_parts.netloc:
        if (
            source_parts.scheme.lower() not in {"http", "https"}
            or not source_parts.hostname
            or source_parts.username is not None
            or source_parts.password is not None
            or _origin(source_parts) != _origin(base_parts)
        ):
            raise FigureAssetError("unsafe_asset_url", "figure URL escapes configured origin")
        path = source_parts.path
        query = source_parts.query
    else:
        path = source_parts.path
        query = source_parts.query
        if path.startswith("/"):
            pass
        elif path.startswith("html/"):
            path = f"/{path}"
        elif path.startswith(f"{versioned}/"):
            path = f"/html/{path}"
        else:
            path = f"/html/{versioned}/{path}"

    allowed_prefix = f"/html/{versioned}/"
    if not _safe_url_path(path, allowed_prefix):
        raise FigureAssetError("unsafe_asset_url", "figure URL path is outside the paper HTML")
    return urlunsplit((base_parts.scheme, base_parts.netloc, path, query, ""))


async def fetch_html_asset(
    http: httpx.AsyncClient,
    *,
    base: str,
    versioned: str,
    source: str,
    max_bytes: int = MAX_ASSET_BYTES,
) -> FigureAssetPayload:
    """Fetch one same-origin HTML figure with bounded redirects and bytes."""

    url = html_asset_url(base, versioned, source)
    for redirect_count in range(MAX_REDIRECTS + 1):
        try:
            async with http.stream(
                "GET",
                url,
                timeout=httpx.Timeout(30.0, connect=5.0),
                follow_redirects=False,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirect_count == MAX_REDIRECTS:
                        raise FigureAssetError(
                            "asset_redirect_invalid", "figure redirect limit was exceeded"
                        )
                    url = html_asset_url(base, versioned, location)
                    continue
                if response.status_code != 200:
                    raise FigureAssetError(
                        "asset_http_status", f"figure request returned HTTP {response.status_code}"
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise FigureAssetError(
                            "asset_http_invalid", "figure response length is invalid"
                        ) from exc
                    if declared_length < 0 or declared_length > max_bytes:
                        raise FigureAssetError(
                            "asset_too_large", "figure response exceeds the safe byte limit"
                        )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise FigureAssetError(
                            "asset_too_large", "figure response exceeds the safe byte limit"
                        )
                return figure_asset_payload(
                    bytes(body),
                    source_name=urlsplit(url).path,
                    content_type=response.headers.get("content-type"),
                )
        except FigureAssetError:
            raise
        except httpx.HTTPError as exc:
            raise FigureAssetError("asset_fetch_failed", "figure request failed") from exc
    raise FigureAssetError("asset_redirect_invalid", "figure redirect limit was exceeded")


__all__ = [
    "MAX_ASSET_BYTES",
    "SUPPORTED_EXTENSIONS",
    "FigureAssetError",
    "FigureAssetPayload",
    "ResolvedLatexAsset",
    "asset_candidates",
    "extract_graphicspaths",
    "fetch_html_asset",
    "figure_asset_payload",
    "html_asset_url",
    "normalize_requested_asset",
    "resolve_latex_asset",
    "validate_image_payload",
]
