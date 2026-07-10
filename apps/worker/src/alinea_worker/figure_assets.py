"""Safe resolution, fetching, and raster validation for paper figures.

The structured document initially contains author-controlled LaTeX paths or HTML
URLs.  This module keeps those values out of public asset keys and only returns a
payload after its actual bytes have been decoded (or a document format has been
rasterized) successfully.
"""

from __future__ import annotations

import asyncio
import io
import math
import multiprocessing as mp
import posixpath
import re
import sys
import time
import warnings
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from pathlib import PurePosixPath
from typing import Any, cast
from urllib.parse import SplitResult, unquote, urljoin, urlsplit, urlunsplit

import fitz
import httpx
from alinea_core.ingest.thumbnail import render_thumbnail as _render_thumbnail_trusted
from PIL import Image, UnidentifiedImageError
from selectolax.lexbor import LexborHTMLParser

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
MAX_IMAGE_DIMENSION = 12_000
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_FRAMES = 128
MAX_SVG_BYTES = 8 * 1024 * 1024
MAX_SVG_ELEMENTS = 50_000
MAX_SVG_DEPTH = 256
MAX_SVG_TEXT_CHARS = 2_000_000
MAX_INLINE_SVG_HTML_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_CONVERTED_BYTES = 32 * 1024 * 1024
MAX_CONVERSION_MEMORY_BYTES = 512 * 1024 * 1024
DEFAULT_CONVERSION_TIMEOUT_S = 15.0

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_GRAPHICSPATH_RE = re.compile(r"\\graphicspath(?![A-Za-z])")
_SVG_UNSAFE_DECLARATION_RE = re.compile(r"<!\s*(?:doctype|entity)\b", re.IGNORECASE)
_XML_DECLARATION_RE = re.compile(r"\A<\?xml(?:\s+[^?]*)?\?>", re.IGNORECASE)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_URL_RE = re.compile(r"url\s*\(\s*(?P<target>[^)]*?)\s*\)", re.IGNORECASE)
_CSS_FUNCTION_RE = re.compile(r"(?P<name>[-A-Za-z][-_A-Za-z0-9]*)\s*\(")
_CSS_SIMPLE_SELECTOR_RE = re.compile(
    r"(?:(?:\*|[A-Za-z][A-Za-z0-9_-]*)(?:[.#][A-Za-z_][A-Za-z0-9_-]*)*"
    r"|(?:[.#][A-Za-z_][A-Za-z0-9_-]*)+)\Z"
)
_SAFE_CSS_VALUE_RE = re.compile(r"[\w\s#.,:%+\-'\"/()]*\Z")
_CSS_DANGEROUS_RE = re.compile(
    r"@import\b|expression\s*\(|(?:behavior|-moz-binding)\s*:|javascript\s*:",
    re.IGNORECASE,
)
_SAFE_FRAGMENT_RE = re.compile(r"#[A-Za-z0-9_.:-]+\Z")
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_.:-]+\Z")
_SAFE_CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
_SAFE_ROLE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_SVG_TRANSFORM_FUNCTION_RE = re.compile(
    r"(?P<name>matrix|translate|scale|rotate|skewX|skewY)\s*"
    r"\((?P<arguments>[+\-0-9.eE,\s]+)\)"
)
_SVG_NUMBER_TOKEN_RE = re.compile(
    r"(?P<number>[-+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][-+]?\d+)?)"
    r"(?:%|px|pt|pc|cm|mm|in|em|ex|rem|deg|rad|grad|turn)?\Z",
    re.IGNORECASE,
)
_SVG_PATH_TOKEN_RE = re.compile(
    r"(?P<command>[MmZzLlHhVvCcSsQqTtAa])"
    r"|(?P<number>[-+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][-+]?\d+)?)"
    r"|(?P<separator>[,\s]+)"
)
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
_SVG_ACTIVE_ELEMENTS = frozenset(
    {
        "animate",
        "animatemotion",
        "animatetransform",
        "audio",
        "canvas",
        "discard",
        "embed",
        "foreignobject",
        "handler",
        "iframe",
        "listener",
        "object",
        "script",
        "set",
        "video",
    }
)
_SVG_ACTIVE_ATTRIBUTES = frozenset({"base", "srcdoc"})
_INLINE_ACTIVE_ELEMENTS = _SVG_ACTIVE_ELEMENTS | {"img"}
_SAFE_CSS_FUNCTIONS = frozenset(
    {
        "hsl",
        "hsla",
        "matrix",
        "matrix3d",
        "rgb",
        "rgba",
        "rotate",
        "rotate3d",
        "scalex",
        "scaley",
        "scale",
        "skewx",
        "skewy",
        "translate",
        "translate3d",
        "translatex",
        "translatey",
    }
)
_SAFE_STYLE_PROPERTIES = frozenset(
    {
        "alignment-baseline",
        "baseline-shift",
        "clip-path",
        "clip-rule",
        "color",
        "color-interpolation",
        "color-interpolation-filters",
        "dominant-baseline",
        "display",
        "fill",
        "fill-opacity",
        "fill-rule",
        "filter",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "letter-spacing",
        "marker-end",
        "marker-mid",
        "marker-start",
        "mask",
        "opacity",
        "paint-order",
        "shape-rendering",
        "stop-color",
        "stop-opacity",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "text-anchor",
        "text-decoration",
        "text-rendering",
        "transform",
        "transform-origin",
        "vector-effect",
        "visibility",
        "word-spacing",
    }
)
_SVG_TRANSFORM_ATTRIBUTES = frozenset({"gradienttransform", "patterntransform", "transform"})
_SVG_IDENTIFIER_VALUE_ATTRIBUTES = frozenset({"in", "in2", "result"})
_SVG_ENUM_ATTRIBUTE_VALUES: dict[str, frozenset[str]] = {
    "filterunits": frozenset({"objectBoundingBox", "userSpaceOnUse"}),
    "gradientunits": frozenset({"objectBoundingBox", "userSpaceOnUse"}),
    "lengthadjust": frozenset({"spacing", "spacingAndGlyphs"}),
    "markerunits": frozenset({"strokeWidth", "userSpaceOnUse"}),
    "mode": frozenset(
        {
            "color",
            "color-burn",
            "color-dodge",
            "darken",
            "difference",
            "exclusion",
            "hard-light",
            "hue",
            "lighten",
            "luminosity",
            "multiply",
            "normal",
            "overlay",
            "saturation",
            "screen",
            "soft-light",
        }
    ),
    "operator": frozenset(
        {"arithmetic", "atop", "dilate", "erode", "in", "lighter", "out", "over", "xor"}
    ),
    "patterncontentunits": frozenset({"objectBoundingBox", "userSpaceOnUse"}),
    "patternunits": frozenset({"objectBoundingBox", "userSpaceOnUse"}),
    "primitiveunits": frozenset({"objectBoundingBox", "userSpaceOnUse"}),
    "spreadmethod": frozenset({"pad", "reflect", "repeat"}),
    "type": frozenset(
        {
            "discrete",
            "fractalNoise",
            "gamma",
            "hueRotate",
            "identity",
            "linear",
            "luminanceToAlpha",
            "matrix",
            "saturate",
            "table",
            "text/css",
            "turbulence",
        }
    ),
    "xchannelselector": frozenset({"A", "B", "G", "R"}),
    "ychannelselector": frozenset({"A", "B", "G", "R"}),
}
_SVG_PRESERVE_ASPECT_RATIO_TOKENS = frozenset(
    {
        "defer",
        "meet",
        "none",
        "slice",
        "xMaxYMax",
        "xMaxYMid",
        "xMaxYMin",
        "xMidYMax",
        "xMidYMid",
        "xMidYMin",
        "xMinYMax",
        "xMinYMid",
        "xMinYMin",
    }
)
_SVG_GEOMETRY_ATTRIBUTES = frozenset(
    {
        "azimuth",
        "basefrequency",
        "bias",
        "by",
        "cx",
        "cy",
        "d",
        "diffuseconstant",
        "divisor",
        "dx",
        "dy",
        "elevation",
        "exponent",
        "filterunits",
        "fx",
        "fy",
        "gradientunits",
        "height",
        "in",
        "in2",
        "intercept",
        "kernelmatrix",
        "kernelunitlength",
        "lengthadjust",
        "limitingconeangle",
        "markerheight",
        "markerunits",
        "markerwidth",
        "mode",
        "numoctaves",
        "offset",
        "operator",
        "order",
        "orient",
        "pathlength",
        "patterncontentunits",
        "patternunits",
        "points",
        "preserveaspectratio",
        "primitiveunits",
        "r",
        "radius",
        "refx",
        "refy",
        "result",
        "rotate",
        "rx",
        "ry",
        "scale",
        "seed",
        "slope",
        "specularconstant",
        "specularexponent",
        "spreadmethod",
        "stddeviation",
        "surfacescale",
        "tablevalues",
        "targetx",
        "targety",
        "textlength",
        "type",
        "values",
        "version",
        "viewbox",
        "width",
        "x",
        "x1",
        "x2",
        "xchannelselector",
        "y",
        "y1",
        "y2",
        "ychannelselector",
        "z",
    }
)

ET.register_namespace("", _SVG_NAMESPACE)
ET.register_namespace("xlink", _XLINK_NAMESPACE)
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
    source_size: int | None = None


@dataclass(frozen=True)
class ThumbnailPayload:
    """Two WebP thumbnail sizes produced by an isolated child."""

    card: bytes
    retina: bytes


@dataclass(frozen=True)
class ResolvedLatexAsset:
    """The unique archive member selected for a LaTeX figure."""

    source_name: str
    payload: FigureAssetPayload


@dataclass(frozen=True)
class ResolvedLatexSource:
    """The unique unconverted archive member selected for a LaTeX figure."""

    source_name: str
    content: bytes


PostscriptConverter = Callable[[bytes, str], bytes]
IsolatedWorker = Callable[..., object]
AsyncPayloadLoader = Callable[[bytes, str, str | None], Awaitable[FigureAssetPayload]]
BeforeRequest = Callable[[], Awaitable[None]]


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


def _validate_image_payload_trusted(
    data: bytes,
    *,
    source_name: str = "",
    content_type: str | None = None,
) -> FigureAssetPayload:
    """Trusted child/test decoder deriving raster format from bytes."""

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
                if frame_count > MAX_IMAGE_FRAMES:
                    raise FigureAssetError("image_too_large", "animated figure has too many frames")
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
    return FigureAssetPayload(data, ext, normalized_content_type, width, height, len(data))


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
    return _validate_image_payload_trusted(
        rendered,
        source_name="rendered.png",
        content_type="image/png",
    )


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


def _xml_name(value: str) -> tuple[str | None, str]:
    if value.startswith("{") and "}" in value:
        namespace, local = value[1:].split("}", 1)
        return namespace, local.casefold()
    return None, value.rsplit(":", 1)[-1].casefold()


def _require_internal_fragment(value: str) -> None:
    if _SAFE_FRAGMENT_RE.fullmatch(value.strip()) is None:
        raise FigureAssetError("unsafe_vector", "SVG references must target an internal fragment")


def _validate_svg_inert_text(value: str, *, max_chars: int = 65_536) -> None:
    if len(value) > max_chars or any(
        (ord(character) < 0x20 and character not in "\t\r\n") or ord(character) == 0x7F
        for character in value
    ):
        raise FigureAssetError("unsafe_vector", "SVG attribute text is not accepted")


def _validate_svg_identifier(value: str) -> None:
    if _SAFE_IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise FigureAssetError("unsafe_vector", "SVG identifier is not accepted")


def _validate_svg_class_list(value: str) -> None:
    tokens = value.split()
    if not tokens or any(_SAFE_CLASS_RE.fullmatch(token) is None for token in tokens):
        raise FigureAssetError("unsafe_vector", "SVG class list is not accepted")


def _validate_svg_role_list(value: str) -> None:
    tokens = value.split()
    if not tokens or any(_SAFE_ROLE_RE.fullmatch(token) is None for token in tokens):
        raise FigureAssetError("unsafe_vector", "SVG role is not accepted")


def _validate_svg_transform(value: str) -> None:
    clean = value.strip()
    if clean == "none":
        return
    position = 0
    matched = False
    while position < len(clean):
        while position < len(clean) and clean[position] in " \t\r\n,":
            position += 1
        if position == len(clean):
            break
        match = _SVG_TRANSFORM_FUNCTION_RE.match(clean, position)
        if match is None or not any(character.isdigit() for character in match.group("arguments")):
            raise FigureAssetError("unsafe_vector", "SVG transform is not accepted")
        matched = True
        position = match.end()
    if not matched:
        raise FigureAssetError("unsafe_vector", "SVG transform is not accepted")


def _validate_svg_geometry(attribute: str, value: str) -> None:
    _validate_svg_inert_text(value)
    clean = value.strip()
    if attribute == "d":
        position = 0
        while position < len(clean):
            match = _SVG_PATH_TOKEN_RE.match(clean, position)
            if match is None:
                raise FigureAssetError("unsafe_vector", "SVG path data is not accepted")
            number = match.group("number")
            if number is not None and not math.isfinite(float(number)):
                raise FigureAssetError("unsafe_vector", "SVG path number is not finite")
            position = match.end()
        return
    if attribute in _SVG_IDENTIFIER_VALUE_ATTRIBUTES:
        _validate_svg_identifier(clean)
        return
    if attribute == "preserveaspectratio":
        tokens = clean.split()
        if (
            not tokens
            or len(tokens) > 3
            or any(token not in _SVG_PRESERVE_ASPECT_RATIO_TOKENS for token in tokens)
        ):
            raise FigureAssetError("unsafe_vector", "SVG aspect ratio is not accepted")
        return
    if attribute == "orient" and clean in {"auto", "auto-start-reverse"}:
        return
    allowed_values = _SVG_ENUM_ATTRIBUTE_VALUES.get(attribute)
    if allowed_values is not None:
        if clean not in allowed_values:
            raise FigureAssetError("unsafe_vector", "SVG enumeration is not accepted")
        return
    tokens = clean.replace(",", " ").replace(";", " ").split()
    if not tokens:
        raise FigureAssetError("unsafe_vector", "SVG numeric geometry is not accepted")
    for token in tokens:
        match = _SVG_NUMBER_TOKEN_RE.fullmatch(token)
        if match is None or not math.isfinite(float(match.group("number"))):
            raise FigureAssetError("unsafe_vector", "SVG numeric geometry is not accepted")


def _precheck_svg_css(value: str) -> str:
    if "\\" in value:
        raise FigureAssetError("unsafe_vector", "SVG CSS escapes are not accepted")
    without_comments = _CSS_COMMENT_RE.sub("", value)
    if "/*" in without_comments or "*/" in without_comments:
        raise FigureAssetError("unsafe_vector", "SVG CSS contains an invalid comment")
    if _CSS_DANGEROUS_RE.search(without_comments) is not None:
        raise FigureAssetError("unsafe_vector", "SVG CSS contains active content")
    if "@" in without_comments:
        raise FigureAssetError("unsafe_vector", "SVG CSS at-rules are not accepted")
    return without_comments


def _validate_svg_css_references(value: str) -> None:
    unmatched = _CSS_URL_RE.sub("", value)
    if re.search(r"url\s*\(", unmatched, re.IGNORECASE) is not None:
        raise FigureAssetError("unsafe_vector", "SVG CSS contains an invalid URL")
    for match in _CSS_URL_RE.finditer(value):
        target = match.group("target").strip()
        if len(target) >= 2 and target[0] == target[-1] and target[0] in {'"', "'"}:
            target = target[1:-1].strip()
        _require_internal_fragment(target)
    for match in _CSS_FUNCTION_RE.finditer(unmatched):
        if match.group("name").casefold() not in _SAFE_CSS_FUNCTIONS:
            raise FigureAssetError("unsafe_vector", "SVG CSS function is not accepted")


def _validate_svg_css_value(value: str) -> None:
    clean = value.strip()
    if not clean:
        raise FigureAssetError("unsafe_vector", "SVG style value is empty")
    if _SAFE_CSS_VALUE_RE.fullmatch(clean) is None:
        raise FigureAssetError("unsafe_vector", "SVG style value is not accepted")

    quote: str | None = None
    functions: list[str] = []
    for position, character in enumerate(clean):
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
            continue
        if character == "(":
            end = position
            while end > 0 and clean[end - 1].isspace():
                end -= 1
            start = end
            while start > 0 and (clean[start - 1].isalnum() or clean[start - 1] in {"-", "_"}):
                start -= 1
            function_name = clean[start:end].casefold()
            if function_name != "url" and function_name not in _SAFE_CSS_FUNCTIONS:
                raise FigureAssetError("unsafe_vector", "SVG CSS function is not accepted")
            functions.append(function_name)
        elif character == ")":
            if not functions:
                raise FigureAssetError("unsafe_vector", "SVG style value is unbalanced")
            functions.pop()
    if quote is not None or functions:
        raise FigureAssetError("unsafe_vector", "SVG style value is unbalanced")


def _validate_svg_css(value: str, *, declarations: bool = False) -> None:
    without_comments = _precheck_svg_css(value)

    if declarations:
        if "{" in without_comments or "}" in without_comments:
            raise FigureAssetError("unsafe_vector", "SVG style declaration is invalid")
        for declaration in without_comments.split(";"):
            clean = declaration.strip()
            if not clean:
                continue
            if ":" not in clean:
                raise FigureAssetError("unsafe_vector", "SVG style declaration is invalid")
            property_name, property_value = clean.split(":", 1)
            if property_name.strip().casefold() not in _SAFE_STYLE_PROPERTIES:
                raise FigureAssetError("unsafe_vector", "SVG style property is not accepted")
            _validate_svg_css_value(property_value)
    else:
        _validate_svg_css_value(without_comments)

    _validate_svg_css_references(without_comments)


def _validate_svg_css_selector(selector: str) -> None:
    for item in selector.split(","):
        clean = item.strip()
        if not clean:
            raise FigureAssetError("unsafe_vector", "SVG CSS selector is invalid")
        normalized = re.sub(r"\s*>\s*", " > ", clean)
        tokens = normalized.split()
        if not tokens or tokens[0] == ">" or tokens[-1] == ">":
            raise FigureAssetError("unsafe_vector", "SVG CSS selector is invalid")
        previous_was_combinator = False
        for part in tokens:
            if part == ">":
                if previous_was_combinator:
                    raise FigureAssetError("unsafe_vector", "SVG CSS selector is invalid")
                previous_was_combinator = True
                continue
            if _CSS_SIMPLE_SELECTOR_RE.fullmatch(part) is None:
                raise FigureAssetError("unsafe_vector", "SVG CSS selector is not accepted")
            previous_was_combinator = False


def _validate_svg_stylesheet(value: str) -> None:
    stylesheet = _precheck_svg_css(value)
    position = 0
    while position < len(stylesheet):
        while position < len(stylesheet) and stylesheet[position].isspace():
            position += 1
        if position == len(stylesheet):
            return
        open_brace = stylesheet.find("{", position)
        if open_brace < 0:
            raise FigureAssetError("unsafe_vector", "SVG stylesheet rule is invalid")
        close_brace = stylesheet.find("}", open_brace + 1)
        if close_brace < 0 or "{" in stylesheet[open_brace + 1 : close_brace]:
            raise FigureAssetError("unsafe_vector", "SVG stylesheet rule is invalid")
        _validate_svg_css_selector(stylesheet[position:open_brace])
        _validate_svg_css(stylesheet[open_brace + 1 : close_brace], declarations=True)
        position = close_brace + 1


def _decode_and_precheck_svg(data: bytes) -> str:
    if len(data) > MAX_SVG_BYTES:
        raise FigureAssetError("asset_too_large", "SVG exceeds the safe byte limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FigureAssetError("invalid_vector", "SVG must use UTF-8 XML") from exc
    if _SVG_UNSAFE_DECLARATION_RE.search(text) is not None:
        raise FigureAssetError("unsafe_vector", "SVG declarations are not accepted")

    declaration = _XML_DECLARATION_RE.match(text)
    remainder = text[declaration.end() :] if declaration is not None else text
    if "<?" in remainder or "?>" in remainder:
        raise FigureAssetError("unsafe_vector", "SVG processing instructions are not accepted")
    return text


def _parse_limited_svg(text: str) -> ET.Element[str]:
    """Parse SVG incrementally while bounding tree growth and retained text."""

    parser = ET.XMLPullParser(events=("start", "end"))
    root: ET.Element[str] | None = None
    depth = 0
    element_count = 0
    text_chars = 0
    try:
        for offset in range(0, len(text), 4096):
            parser.feed(text[offset : offset + 4096])
            for raw_event in parser.read_events():
                event, element = cast("tuple[str, ET.Element[str]]", raw_event)
                if event == "start":
                    if root is None:
                        root = element
                    depth += 1
                    element_count += 1
                    if element_count > MAX_SVG_ELEMENTS or depth > MAX_SVG_DEPTH:
                        raise FigureAssetError(
                            "vector_too_complex", "SVG structure exceeds the safe limit"
                        )
                else:
                    text_chars += len(element.text or "") + len(element.tail or "")
                    if text_chars > MAX_SVG_TEXT_CHARS:
                        raise FigureAssetError(
                            "vector_too_complex", "SVG text exceeds the safe limit"
                        )
                    depth -= 1
        parser.close()
        for raw_event in parser.read_events():
            event, element = cast("tuple[str, ET.Element[str]]", raw_event)
            if event == "start":
                if root is None:
                    root = element
                depth += 1
                element_count += 1
                if element_count > MAX_SVG_ELEMENTS or depth > MAX_SVG_DEPTH:
                    raise FigureAssetError(
                        "vector_too_complex", "SVG structure exceeds the safe limit"
                    )
            else:
                text_chars += len(element.text or "") + len(element.tail or "")
                if text_chars > MAX_SVG_TEXT_CHARS:
                    raise FigureAssetError("vector_too_complex", "SVG text exceeds the safe limit")
                depth -= 1
    except FigureAssetError:
        raise
    except ET.ParseError as exc:
        raise FigureAssetError("invalid_vector", "SVG XML is invalid") from exc
    if root is None or depth != 0:
        raise FigureAssetError("invalid_vector", "SVG XML is invalid")
    return root


def _validate_svg_document(data: bytes) -> bytes:
    """Return a passive, canonical SVG containing only rendering semantics."""

    text = _decode_and_precheck_svg(data)
    root = _parse_limited_svg(text)

    root_namespace, root_name = _xml_name(str(root.tag))
    if root_name != "svg" or root_namespace not in {None, _SVG_NAMESPACE}:
        raise FigureAssetError("unsafe_vector", "vector document root is not safe SVG")

    elements = list(root.iter())
    for element in elements:
        namespace, name = _xml_name(str(element.tag))
        if name in _SVG_ACTIVE_ELEMENTS:
            raise FigureAssetError("unsafe_vector", "SVG contains an active element")
        for raw_attribute, value in element.attrib.items():
            _attribute_namespace, attribute = _xml_name(raw_attribute)
            if attribute.startswith("on") or attribute in _SVG_ACTIVE_ATTRIBUTES:
                raise FigureAssetError("unsafe_vector", "SVG contains an active attribute")
            if attribute in {"href", "src"}:
                _require_internal_fragment(value)
        if namespace not in {None, _SVG_NAMESPACE}:
            continue
        for raw_attribute, value in list(element.attrib.items()):
            attribute_namespace, attribute = _xml_name(raw_attribute)
            if attribute in {"href", "src"}:
                if attribute_namespace not in {None, _SVG_NAMESPACE, _XLINK_NAMESPACE}:
                    del element.attrib[raw_attribute]
                continue
            if attribute_namespace not in {None, _SVG_NAMESPACE}:
                if attribute_namespace == _XML_NAMESPACE and attribute in {"lang", "space"}:
                    _validate_svg_inert_text(value, max_chars=256)
                else:
                    del element.attrib[raw_attribute]
                continue
            if attribute == "style":
                _validate_svg_css(value, declarations=True)
            elif attribute in _SAFE_STYLE_PROPERTIES:
                _validate_svg_css(value)
            elif attribute in _SVG_TRANSFORM_ATTRIBUTES:
                _validate_svg_transform(value)
            elif attribute == "id":
                _validate_svg_identifier(value)
            elif attribute == "class":
                _validate_svg_class_list(value)
            elif attribute == "role":
                _validate_svg_role_list(value)
            elif attribute.startswith("aria-"):
                _validate_svg_inert_text(value, max_chars=4096)
            elif attribute.startswith("data-"):
                del element.attrib[raw_attribute]
            elif attribute in _SVG_GEOMETRY_ATTRIBUTES:
                _validate_svg_geometry(attribute, value)
            else:
                del element.attrib[raw_attribute]
        if name == "style":
            _validate_svg_stylesheet("".join(element.itertext()))

    for parent in elements:
        for child in list(parent):
            child_namespace, _child_name = _xml_name(str(child.tag))
            if child_namespace not in {None, _SVG_NAMESPACE}:
                parent.remove(child)

    return cast(bytes, ET.tostring(root, encoding="utf-8", short_empty_elements=True))


def _render_svg(data: bytes) -> FigureAssetPayload:
    sanitized = _validate_svg_document(data)
    try:
        return _render_document(sanitized, "svg")
    except FigureAssetError as exc:
        if exc.code in {"image_too_large", "asset_too_large", "unsafe_vector"}:
            raise
        raise FigureAssetError("invalid_vector", "SVG figure could not be rasterized") from exc


def extract_inline_svg(raw_html: str) -> bytes:
    """Extract exactly one structurally inert SVG from author HTML."""

    if len(raw_html) > MAX_INLINE_SVG_HTML_BYTES:
        raise FigureAssetError("asset_too_large", "inline figure exceeds the safe byte limit")
    if len(raw_html.encode("utf-8")) > MAX_INLINE_SVG_HTML_BYTES:
        raise FigureAssetError("asset_too_large", "inline figure exceeds the safe byte limit")
    fragment = LexborHTMLParser(raw_html)
    svgs = fragment.css("svg")
    if len(svgs) != 1:
        raise FigureAssetError("unsafe_inline_figure", "inline figure is not one SVG")
    root = fragment.root
    if root is None:
        raise FigureAssetError("unsafe_inline_figure", "inline figure has no document root")
    for element in root.traverse(include_text=False):
        tag = str(element.tag or "").casefold()
        if tag in _INLINE_ACTIVE_ELEMENTS:
            raise FigureAssetError("unsafe_inline_figure", "inline figure contains active HTML")
        for raw_name in element.attributes:
            name = str(raw_name).rsplit(":", 1)[-1].casefold()
            if name.startswith("on") or name == "srcdoc":
                raise FigureAssetError(
                    "unsafe_inline_figure", "inline figure contains active attributes"
                )
    svg_html = svgs[0].html
    if svg_html is None:
        raise FigureAssetError("unsafe_inline_figure", "inline SVG could not be serialized")
    return svg_html.encode("utf-8")


def _inline_svg_payload_trusted(raw_html: str) -> FigureAssetPayload:
    """Trusted test helper; production extracts then uses the isolated API."""

    try:
        svg_bytes = extract_inline_svg(raw_html)
        return _figure_asset_payload_trusted(svg_bytes, source_name="inline.svg")
    except FigureAssetError as exc:
        if exc.code in {"asset_too_large", "image_too_large"}:
            raise
        raise FigureAssetError("unsafe_inline_figure", "inline SVG was rejected") from exc
    except Exception as exc:
        raise FigureAssetError("unsafe_inline_figure", "inline SVG could not be extracted") from exc


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
    return _validate_image_payload_trusted(
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
    except FigureAssetError as exc:
        if exc.code in {"asset_too_large", "image_too_large"}:
            raise
        code = "conversion_failed" if converter is not None else "conversion_unavailable"
        raise FigureAssetError(code, "PostScript raster conversion is unavailable") from exc
    except Exception as exc:
        code = "conversion_failed" if converter is not None else "conversion_unavailable"
        raise FigureAssetError(code, "PostScript raster conversion is unavailable") from exc
    try:
        payload = _validate_image_payload_trusted(
            converted, source_name=f"converted.{source_format}", content_type="image/png"
        )
        return _normalize_raster_to_png(payload)
    except FigureAssetError as exc:
        if exc.code in {"asset_too_large", "image_too_large"}:
            raise
        raise FigureAssetError(
            "conversion_failed", "PostScript converter returned an invalid raster"
        ) from exc


def _figure_asset_payload_trusted(
    data: bytes,
    *,
    source_name: str,
    content_type: str | None = None,
    postscript_converter: PostscriptConverter | None = None,
) -> FigureAssetPayload:
    """Trusted child/test decoder; production must use the async isolated API."""

    _check_input_size(data)
    normalized_name = source_name.split("?", 1)[0]
    suffix = PurePosixPath(normalized_name).suffix.lower()
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    stripped = data[:1024].lstrip()
    if _is_supported_raster(data):
        payload = _validate_image_payload_trusted(
            data,
            source_name=source_name,
            content_type=content_type,
        )
    elif stripped.startswith(b"%PDF-"):
        payload = _render_document(data, "pdf")
    elif _is_svg(data) or suffix == ".svg" or normalized_content_type == "image/svg+xml":
        payload = _render_svg(data)
    elif stripped.startswith(b"%!PS-Adobe") or suffix in {".eps", ".ps"}:
        source_format = "eps" if suffix == ".eps" or b"EPSF" in stripped[:128] else "ps"
        payload = _postscript_payload(data, source_format, postscript_converter)
    else:
        raise FigureAssetError(
            "unsupported_figure_format", "figure magic and declared format are unsupported"
        )
    return replace(payload, source_size=len(data))


def _thumbnail_payload_trusted(
    data: bytes,
    *,
    source_name: str,
    content_type: str | None = None,
) -> ThumbnailPayload:
    """Trusted child-only thumbnail decoder and renderer."""

    validated = _validate_image_payload_trusted(
        data,
        source_name=source_name,
        content_type=content_type,
    )
    card, retina = _render_thumbnail_trusted(validated.content)
    return ThumbnailPayload(card=card, retina=retina)


def conversion_requires_isolation(
    data: bytes,
    *,
    source_name: str,
    content_type: str | None = None,
) -> bool:
    """Return whether materialization invokes a document/vector renderer."""

    if _is_supported_raster(data):
        return False
    suffix = PurePosixPath(source_name.split("?", 1)[0]).suffix.lower()
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    stripped = data[:1024].lstrip()
    return (
        stripped.startswith(b"%PDF-")
        or _is_svg(data)
        or suffix == ".svg"
        or normalized_content_type == "image/svg+xml"
        or stripped.startswith(b"%!PS-Adobe")
        or suffix in {".eps", ".ps"}
    )


def _set_child_rlimit(resource_kind: int, soft_limit: int, hard_limit: int) -> None:
    import resource

    _current_soft, current_hard = resource.getrlimit(resource_kind)
    if current_hard != resource.RLIM_INFINITY:
        hard_limit = min(hard_limit, current_hard)
    soft_limit = min(soft_limit, hard_limit)
    resource.setrlimit(resource_kind, (soft_limit, hard_limit))


def _apply_conversion_resource_limits(timeout_s: float, max_output_bytes: int) -> None:
    """Apply Linux kernel limits inside the disposable conversion child."""

    if not sys.platform.startswith("linux"):
        return
    import resource

    cpu_seconds = max(1, math.ceil(timeout_s))
    _set_child_rlimit(resource.RLIMIT_CPU, cpu_seconds, cpu_seconds + 1)
    _set_child_rlimit(
        resource.RLIMIT_AS,
        MAX_CONVERSION_MEMORY_BYTES,
        MAX_CONVERSION_MEMORY_BYTES,
    )
    file_limit = max(1024 * 1024, max_output_bytes)
    _set_child_rlimit(resource.RLIMIT_FSIZE, file_limit, file_limit)
    _set_child_rlimit(resource.RLIMIT_NOFILE, 64, 64)


def _isolated_worker_entry(
    connection: Connection,
    data: bytes,
    source_name: str,
    content_type: str | None,
    timeout_s: float,
    max_output_bytes: int,
    worker: IsolatedWorker,
) -> None:
    try:
        _apply_conversion_resource_limits(timeout_s, max_output_bytes)
        result = worker(data, source_name=source_name, content_type=content_type)
        if isinstance(result, FigureAssetPayload):
            output_size = len(result.content)
        elif isinstance(result, ThumbnailPayload):
            output_size = len(result.card) + len(result.retina)
        else:
            connection.send(("crash",))
            return
        if output_size > max_output_bytes:
            connection.send(("oversize",))
        else:
            connection.send(("ok", result))
    except FigureAssetError as exc:
        connection.send(("figure_error", exc.code))
    except BaseException:
        try:
            connection.send(("crash",))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _terminate_and_reap(process: Any, *, failure_code: str) -> None:
    """Stop and reap a disposable child or report a stable lifecycle failure."""

    try:
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.5)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.5)
        if process.is_alive():
            raise FigureAssetError(failure_code, "isolated worker could not be reaped")
        if process.exitcode is None:
            process.join(timeout=0.5)
            if process.exitcode is None:
                raise FigureAssetError(failure_code, "isolated worker lifecycle is incomplete")
    except (AssertionError, ValueError):
        # A spawn/pickle failure can leave a Process object that never started.
        return

    close = getattr(process, "close", None)
    if callable(close):
        close()


def _run_isolated_worker(
    data: bytes,
    source_name: str,
    content_type: str | None,
    timeout_s: float,
    max_output_bytes: int,
    worker: IsolatedWorker,
    *,
    timeout_code: str,
    crash_code: str,
    oversize_code: str,
    lifecycle_code: str,
) -> object:
    context = mp.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_isolated_worker_entry,
        args=(
            send_connection,
            data,
            source_name,
            content_type,
            timeout_s,
            max_output_bytes,
            worker,
        ),
        daemon=True,
    )
    message: tuple[object, ...] | None = None
    timed_out = False
    try:
        process.start()
        send_connection.close()
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            if receive_connection.poll(min(0.05, remaining)):
                try:
                    received = receive_connection.recv()
                except EOFError:
                    break
                if isinstance(received, tuple):
                    message = received
                break
            if not process.is_alive():
                if receive_connection.poll(0.05):
                    try:
                        received = receive_connection.recv()
                    except EOFError:
                        break
                    if isinstance(received, tuple):
                        message = received
                break
    except Exception as exc:
        raise FigureAssetError(crash_code, "isolated worker could not start") from exc
    finally:
        try:
            _terminate_and_reap(process, failure_code=lifecycle_code)
        finally:
            receive_connection.close()
            send_connection.close()

    if timed_out:
        raise FigureAssetError(timeout_code, "isolated worker deadline was exceeded")
    if not message or message[0] == "crash":
        raise FigureAssetError(crash_code, "isolated worker process failed")
    if message[0] == "oversize":
        raise FigureAssetError(oversize_code, "isolated worker output exceeds the byte limit")
    if message[0] == "figure_error" and len(message) == 2 and isinstance(message[1], str):
        raise FigureAssetError(message[1], "isolated worker rejected the input")
    if message[0] == "ok" and len(message) == 2:
        return message[1]
    raise FigureAssetError(crash_code, "isolated worker returned invalid data")


async def isolated_figure_asset_payload(
    data: bytes,
    *,
    source_name: str,
    content_type: str | None = None,
    timeout_s: float = DEFAULT_CONVERSION_TIMEOUT_S,
    max_output_bytes: int = MAX_CONVERTED_BYTES,
    worker: IsolatedWorker = _figure_asset_payload_trusted,
) -> FigureAssetPayload:
    """Materialize a document figure in a bounded, disposable subprocess."""

    _check_input_size(data)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise FigureAssetError("conversion_timeout", "figure conversion deadline is invalid")
    if max_output_bytes <= 0:
        raise FigureAssetError("conversion_oversize", "converted figure byte limit is invalid")
    result = await asyncio.to_thread(
        _run_isolated_worker,
        data,
        source_name,
        content_type,
        timeout_s,
        min(max_output_bytes, MAX_CONVERTED_BYTES),
        worker,
        timeout_code="conversion_timeout",
        crash_code="conversion_crashed",
        oversize_code="conversion_oversize",
        lifecycle_code="conversion_lifecycle",
    )
    if isinstance(result, FigureAssetPayload):
        return result
    raise FigureAssetError("conversion_crashed", "figure conversion returned invalid data")


async def isolated_thumbnail_payload(
    data: bytes,
    *,
    source_name: str = "thumbnail-source.png",
    content_type: str | None = "image/png",
    timeout_s: float = DEFAULT_CONVERSION_TIMEOUT_S,
    max_output_bytes: int = MAX_CONVERTED_BYTES,
    worker: IsolatedWorker = _thumbnail_payload_trusted,
) -> ThumbnailPayload:
    """Decode and render thumbnails in a bounded, disposable subprocess."""

    _check_input_size(data)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise FigureAssetError("thumbnail_timeout", "thumbnail deadline is invalid")
    if max_output_bytes <= 0:
        raise FigureAssetError("thumbnail_oversize", "thumbnail byte limit is invalid")
    result = await asyncio.to_thread(
        _run_isolated_worker,
        data,
        source_name,
        content_type,
        timeout_s,
        min(max_output_bytes, MAX_CONVERTED_BYTES),
        worker,
        timeout_code="thumbnail_timeout",
        crash_code="thumbnail_crashed",
        oversize_code="thumbnail_oversize",
        lifecycle_code="thumbnail_lifecycle",
    )
    if isinstance(result, ThumbnailPayload):
        return result
    raise FigureAssetError("thumbnail_crashed", "thumbnail worker returned invalid data")


def resolve_latex_source(
    *,
    binary_files: Mapping[str, bytes],
    requested: str,
    main_tex_name: str | None,
    graphicspaths: Sequence[str],
) -> ResolvedLatexSource:
    """Resolve exactly one safe archive member without invoking a renderer."""

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
    return ResolvedLatexSource(source_name, binary_files[source_name])


def _resolve_latex_asset_trusted(
    *,
    binary_files: Mapping[str, bytes],
    requested: str,
    main_tex_name: str | None,
    graphicspaths: Sequence[str],
    postscript_converter: PostscriptConverter | None = None,
) -> ResolvedLatexAsset:
    """Trusted test helper resolving and decoding one archive member synchronously."""

    source = resolve_latex_source(
        binary_files=binary_files,
        requested=requested,
        main_tex_name=main_tex_name,
        graphicspaths=graphicspaths,
    )
    payload = _figure_asset_payload_trusted(
        source.content,
        source_name=source.source_name,
        postscript_converter=postscript_converter,
    )
    return ResolvedLatexAsset(source.source_name, payload)


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
    base_path = base_parts.path.rstrip("/")
    if base_path and (
        not base_path.startswith("/")
        or "\\" in base_path
        or "//" in base_path
        or "%" in base_path
        or any(part in {".", ".."} for part in PurePosixPath(base_path).parts)
    ):
        raise FigureAssetError("unsafe_asset_url", "configured figure base path is invalid")
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
            root_html_prefix = f"/html/{versioned}/"
            if base_path and path.startswith(root_html_prefix):
                path = f"{base_path}{path}"
        elif path.startswith("html/"):
            path = f"{base_path}/{path}"
        elif path.startswith(f"{versioned}/"):
            path = f"{base_path}/html/{path}"
        else:
            path = f"{base_path}/html/{versioned}/{path}"

    allowed_prefix = f"{base_path}/html/{versioned}/"
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
    total_timeout_s: float = 45.0,
    payload_loader: AsyncPayloadLoader | None = None,
    before_request: BeforeRequest | None = None,
) -> FigureAssetPayload:
    """Fetch one same-origin HTML figure with bounded redirects and bytes."""

    async def _fetch() -> FigureAssetPayload:
        url = html_asset_url(base, versioned, source)
        for redirect_count in range(MAX_REDIRECTS + 1):
            if before_request is not None:
                await before_request()
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
                    if _has_control(location):
                        raise FigureAssetError(
                            "unsafe_asset_url", "figure redirect contains control characters"
                        )
                    url = html_asset_url(base, versioned, urljoin(url, location))
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
                content = bytes(body)
                source_name = urlsplit(url).path
                content_type = response.headers.get("content-type")
                if payload_loader is not None:
                    return await payload_loader(content, source_name, content_type)
                return await isolated_figure_asset_payload(
                    content,
                    source_name=source_name,
                    content_type=content_type,
                )
        raise FigureAssetError("asset_redirect_invalid", "figure redirect limit was exceeded")

    try:
        async with asyncio.timeout(total_timeout_s):
            return await _fetch()
    except FigureAssetError:
        raise
    except TimeoutError as exc:
        raise FigureAssetError(
            "asset_fetch_timeout", "figure request deadline was exceeded"
        ) from exc
    except httpx.HTTPError as exc:
        raise FigureAssetError("asset_fetch_failed", "figure request failed") from exc


__all__ = [
    "MAX_ASSET_BYTES",
    "SUPPORTED_EXTENSIONS",
    "FigureAssetError",
    "FigureAssetPayload",
    "ResolvedLatexAsset",
    "ResolvedLatexSource",
    "asset_candidates",
    "conversion_requires_isolation",
    "extract_graphicspaths",
    "extract_inline_svg",
    "fetch_html_asset",
    "html_asset_url",
    "isolated_figure_asset_payload",
    "isolated_thumbnail_payload",
    "normalize_requested_asset",
    "resolve_latex_source",
]
