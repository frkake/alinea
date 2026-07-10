"""Generic and hostile-input tests for paper figure materialization."""

from __future__ import annotations

import gzip
import io
import tarfile
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import fitz
import httpx
import pytest
from alinea_core.document.blocks import Block
from alinea_core.parsing.html_parser import parse_arxiv_html
from alinea_core.storage.s3 import StorageKeys
from alinea_worker import figure_assets
from alinea_worker.figure_assets import (
    FigureAssetError,
    asset_candidates,
    extract_graphicspaths,
    fetch_html_asset,
    figure_asset_payload,
    html_asset_url,
    normalize_requested_asset,
    resolve_latex_asset,
    validate_image_payload,
)
from alinea_worker.pipeline import IngestRun
from alinea_worker.source_candidates import parse_latex_candidate
from PIL import Image
from starlette.applications import Starlette
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route


def _raster_bytes(
    image_format: str = "PNG", *, size: tuple[int, int] = (16, 12), color: str = "navy"
) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format=image_format)
    return stream.getvalue()


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=72, height=36)
    page.draw_rect(page.rect, color=(0, 0, 1), fill=(0.7, 0.8, 1))
    data = bytes(document.tobytes())
    document.close()
    return data


SVG_BYTES = b"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<rect width="20" height="10" fill="blue"/>
</svg>"""
EPS_BYTES = b"""%!PS-Adobe-3.0 EPSF-3.0
%%BoundingBox: 0 0 20 10
newpath 0 0 moveto 20 10 lineto stroke
showpage
%%EOF
"""


@pytest.mark.parametrize(
    "requested",
    [
        "",
        ".",
        "../secret.png",
        "/absolute.png",
        "https://example.test/x.png",
        "//example.test/x.png",
        "folder\\image.png",
        "image.png?download=1",
        "image.png#fragment",
        "image\x00.png",
        "image.png\n",
        r"\iftoggle{largefigures",
    ],
)
def test_normalize_requested_asset_rejects_unsafe_values(requested: str) -> None:
    assert normalize_requested_asset(requested) is None


def test_asset_candidates_combine_archive_main_directory_and_graphicspath() -> None:
    candidates = asset_candidates(
        "plot",
        main_tex_name="paper/main.tex",
        graphicspaths=["../images/"],
    )

    assert "images/plot.eps" in candidates
    assert "paper/plot.png" in candidates
    assert "plot.pdf" in candidates
    assert len(candidates) == len(set(candidates))


def test_resolves_extensionless_asset_using_graphicspath_and_injected_eps_converter() -> None:
    converted = _raster_bytes("PNG", size=(20, 10))

    result = resolve_latex_asset(
        binary_files={"images/plot.eps": EPS_BYTES},
        requested="plot",
        main_tex_name="paper/main.tex",
        graphicspaths=["../images/"],
        postscript_converter=lambda _content, _format: converted,
    )

    assert result.source_name == "images/plot.eps"
    assert result.payload.ext == "png"
    assert result.payload.content.startswith(b"\x89PNG")
    assert (result.payload.width, result.payload.height) == (20, 10)


def test_postscript_converter_output_is_normalized_to_png() -> None:
    result = resolve_latex_asset(
        binary_files={"figure.ps": EPS_BYTES},
        requested="figure.ps",
        main_tex_name="main.tex",
        graphicspaths=[],
        postscript_converter=lambda _content, _format: _raster_bytes("JPEG"),
    )

    assert result.payload.ext == "png"
    assert result.payload.content_type == "image/png"
    assert result.payload.content.startswith(b"\x89PNG")


def test_resolves_case_insensitive_supported_extension() -> None:
    result = resolve_latex_asset(
        binary_files={"Figures/Result.JPEG": _raster_bytes("JPEG")},
        requested="figures/result.jpeg",
        main_tex_name="main.tex",
        graphicspaths=[],
    )

    assert result.source_name == "Figures/Result.JPEG"
    assert result.payload.ext == "jpg"
    assert result.payload.content_type == "image/jpeg"


@pytest.mark.parametrize(
    "binary_files,requested",
    [
        ({"../escape.png": b"x"}, "escape.png"),
        ({"a/plot.png": b"x", "A/PLOT.PNG": b"y"}, "a/plot.png"),
        ({"plot.png": b"x", "paper/plot.png": b"y"}, "plot.png"),
    ],
)
def test_latex_resolution_rejects_unsafe_or_ambiguous_archive_matches(
    binary_files: dict[str, bytes], requested: str
) -> None:
    with pytest.raises(FigureAssetError) as caught:
        resolve_latex_asset(
            binary_files=binary_files,
            requested=requested,
            main_tex_name="paper/main.tex",
            graphicspaths=[],
        )

    assert caught.value.code in {"asset_not_found", "asset_ambiguous"}


@pytest.mark.parametrize(
    ("image_format", "expected_ext", "expected_type"),
    [
        ("PNG", "png", "image/png"),
        ("JPEG", "jpg", "image/jpeg"),
        ("WEBP", "webp", "image/webp"),
        ("GIF", "gif", "image/gif"),
    ],
)
def test_validate_image_payload_decodes_and_normalizes_raster_format(
    image_format: str, expected_ext: str, expected_type: str
) -> None:
    payload = validate_image_payload(
        _raster_bytes(image_format),
        source_name="misleading.dat",
        content_type="application/octet-stream",
    )

    assert payload.ext == expected_ext
    assert payload.content_type == expected_type
    assert (payload.width, payload.height) == (16, 12)


def test_validate_image_payload_uses_decoded_format_not_spoofed_metadata() -> None:
    payload = validate_image_payload(
        _raster_bytes("JPEG"),
        source_name="spoofed.png",
        content_type="image/png",
    )

    assert payload.ext == "jpg"
    assert payload.content_type == "image/jpeg"


@pytest.mark.parametrize(("suffix", "image_format"), [(".eps", "PNG"), (".ps", "JPEG")])
def test_valid_raster_bytes_take_precedence_over_postscript_filename(
    suffix: str, image_format: str
) -> None:
    payload = figure_asset_payload(
        _raster_bytes(image_format),
        source_name=f"mislabelled{suffix}",
    )

    assert payload.ext == ("png" if image_format == "PNG" else "jpg")


def test_validate_image_payload_enforces_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _raster_bytes("PNG")
    monkeypatch.setattr(figure_assets, "MAX_ASSET_BYTES", len(data) - 1)

    with pytest.raises(FigureAssetError) as caught:
        validate_image_payload(data)

    assert caught.value.code == "asset_too_large"


@pytest.mark.parametrize(
    ("limit_name", "limit"),
    [("MAX_IMAGE_DIMENSION", 15), ("MAX_IMAGE_PIXELS", 100)],
)
def test_validate_image_payload_enforces_dimension_and_pixel_limits(
    monkeypatch: pytest.MonkeyPatch, limit_name: str, limit: int
) -> None:
    monkeypatch.setattr(figure_assets, limit_name, limit)

    with pytest.raises(FigureAssetError) as caught:
        validate_image_payload(_raster_bytes("PNG", size=(16, 12)))

    assert caught.value.code == "image_too_large"


def test_validate_image_payload_bounds_all_animation_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.BytesIO()
    frames = [Image.new("RGB", (16, 12), color) for color in ("navy", "white")]
    frames[0].save(stream, format="GIF", save_all=True, append_images=frames[1:])
    monkeypatch.setattr(figure_assets, "MAX_IMAGE_PIXELS", 300)

    with pytest.raises(FigureAssetError) as caught:
        validate_image_payload(stream.getvalue())

    assert caught.value.code == "image_too_large"


@pytest.mark.parametrize(
    "data",
    [
        b"not an image",
        _raster_bytes("PNG")[:20],
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
    ],
)
def test_validate_image_payload_rejects_invalid_or_truncated_rasters(data: bytes) -> None:
    with pytest.raises(FigureAssetError) as caught:
        validate_image_payload(data, source_name="figure.png", content_type="image/png")

    assert caught.value.code == "invalid_image"


def test_pdf_and_svg_are_rendered_to_valid_png() -> None:
    for source_name, source in (("figure.pdf", _pdf_bytes()), ("figure.svg", SVG_BYTES)):
        payload = figure_asset_payload(source, source_name=source_name)

        assert payload.ext == "png"
        assert payload.content_type == "image/png"
        assert payload.content.startswith(b"\x89PNG")
        assert payload.width > 0
        assert payload.height > 0


@pytest.mark.parametrize(
    "external",
    [
        b'<image href="https://example.org/tracker.png" width="20" height="10"/>',
        b"<style>@import url(https://example.org/tracker.css);</style>",
    ],
)
def test_svg_with_external_resource_is_rejected_instead_of_persisted(
    external: bytes,
) -> None:
    source = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">' + external + b"</svg>"
    )

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "unsafe_vector"


@pytest.mark.parametrize(
    "source",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="relative.png"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="/etc/passwd"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><use href="ftp://example.org/x"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><use href="javascript:alert(1)"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png,x"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="h&#116;tp://example.org/x"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="relative.svg#x"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(relative.png)"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:u&#114;l(relative.png)"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>@im/**/port url(relative.css)</style></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>@imp&#111;rt url(relative.css)</style></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>fill:u\\72l(relative.png)</style></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><p>x</p></foreignObject></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        b'<?xml version="1.0"?><?xml-stylesheet href="relative.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
    ],
)
def test_svg_structural_validator_rejects_active_content_before_render(
    monkeypatch: pytest.MonkeyPatch, source: bytes
) -> None:
    rendered: list[str] = []

    def fake_render(_content: bytes, filetype: str) -> Any:
        rendered.append(filetype)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", fake_render)

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "unsafe_vector"
    assert rendered == []


def test_svg_structural_validator_allows_internal_gradient_and_use_fragment() -> None:
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<defs>
  <linearGradient id="gradient"><stop offset="0" stop-color="blue"/></linearGradient>
  <path id="shape" d="M0 0 L10 0 L10 10 Z"/>
</defs>
<rect width="20" height="10" fill="url(#gradient)"/>
<use href="#shape"/>
</svg>"""

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert payload.width > 0
    assert payload.height > 0


def test_svg_suffix_uses_validated_renderer_even_after_long_xml_prolog() -> None:
    source = (
        b'<?xml version="1.0"?>\n<!--'
        + b"metadata" * 700
        + b'--><svg xmlns="http://www.w3.org/2000/svg" width="8" height="6">'
        + b'<rect width="8" height="6" fill="blue"/></svg>'
    )

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert (payload.width, payload.height) == (16, 12)


def test_svg_rejects_entity_declaration_before_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        b'<?xml version="1.0"?>\n<!--'
        + b"padding" * 1500
        + b'--><!DOCTYPE svg [<!ENTITY x "unsafe">]>'
        + b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="6">&x;</svg>'
    )
    rendered: list[str] = []

    def fake_render(_content: bytes, filetype: str) -> Any:
        rendered.append(filetype)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", fake_render)

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "unsafe_vector"
    assert rendered == []


def test_eps_without_available_converter_fails_without_returning_source_bytes() -> None:
    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(EPS_BYTES, source_name="figure.eps")

    assert caught.value.code == "conversion_unavailable"


def test_extract_graphicspaths_and_latex_candidate_propagate_real_declaration() -> None:
    main_tex = r"""
\documentclass{article}
\graphicspath{{../images/}{figures/}}
\begin{document}
\section{Method}
First paragraph has enough content for a compact structured note.

Second paragraph reports a result from the method.
\end{document}
"""
    paths = extract_graphicspaths({"paper/main.tex": main_tex}, "paper/main.tex")
    assert paths == ("../images/", "figures/")

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        encoded = main_tex.encode()
        info = tarfile.TarInfo("paper/main.tex")
        info.size = len(encoded)
        archive.addfile(info, io.BytesIO(encoded))
    candidate, _binary_files, main_name = parse_latex_candidate(
        gzip.compress(stream.getvalue(), mtime=0), pdf_text=""
    )

    assert main_name == "paper/main.tex"
    assert candidate.graphicspaths == ("../images/", "figures/")


def test_extract_graphicspaths_ignores_commented_declarations() -> None:
    source = r"""
% \graphicspath{{ignored/}}
\graphicspath{{real/}} % \graphicspath{{also-ignored/}}
"""

    assert extract_graphicspaths({"main.tex": source}, "main.tex") == ("real/",)


@pytest.mark.parametrize(
    "source",
    [
        "ftp://arxiv.org/html/2401.00001v2/x1.png",
        "https://user:pass@arxiv.org/html/2401.00001v2/x1.png",
        "https://example.org/html/2401.00001v2/x1.png",
        "../private/x1.png",
        "%2e%2e/private/x1.png",
        "/html/2401.00001v2/../../private/x1.png",
        "%252e%252e/private/x1.png",
        "%252fprivate/x1.png",
        "/html/2401.00001v1/x1.png",
        "x1.png#fragment",
        "x1\x00.png",
        "x1.png\n",
    ],
)
def test_html_asset_url_rejects_scheme_origin_credentials_and_traversal(source: str) -> None:
    with pytest.raises(FigureAssetError) as caught:
        html_asset_url("https://arxiv.org", "2401.00001v2", source)

    assert caught.value.code == "unsafe_asset_url"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "2401.00001v2/x1.png",
            "https://arxiv.org/html/2401.00001v2/x1.png",
        ),
        ("x1.png", "https://arxiv.org/html/2401.00001v2/x1.png"),
        (
            "/html/2401.00001v2/figures/x1.png",
            "https://arxiv.org/html/2401.00001v2/figures/x1.png",
        ),
        (
            "https://arxiv.org/html/2401.00001v2/x1.png?download=1",
            "https://arxiv.org/html/2401.00001v2/x1.png?download=1",
        ),
    ],
)
def test_html_asset_url_normalizes_safe_version_relative_paths(source: str, expected: str) -> None:
    assert html_asset_url("https://arxiv.org", "2401.00001v2", source) == expected


@pytest.fixture
async def figure_http() -> AsyncIterator[httpx.AsyncClient]:
    png = _raster_bytes("PNG")

    async def good(_request: Any) -> Response:
        return Response(png, media_type="text/plain")

    async def redirect(_request: Any) -> RedirectResponse:
        return RedirectResponse("/html/2401.00001v2/good.png", status_code=302)

    async def unsafe_redirect(_request: Any) -> RedirectResponse:
        return RedirectResponse("https://example.org/figure.png", status_code=302)

    async def missing(_request: Any) -> Response:
        return Response("missing", status_code=404)

    async def nested_redirect(_request: Any) -> RedirectResponse:
        return RedirectResponse("new.png", status_code=302)

    async def nested_image(_request: Any) -> Response:
        return Response(png, media_type="image/png")

    async def escaping_redirect(_request: Any) -> RedirectResponse:
        return RedirectResponse("../../../2401.00001v1/private.png", status_code=302)

    app = Starlette(
        routes=[
            Route("/html/2401.00001v2/good.png", good),
            Route("/html/2401.00001v2/redirect.png", redirect),
            Route("/html/2401.00001v2/unsafe.png", unsafe_redirect),
            Route("/html/2401.00001v2/missing.png", missing),
            Route("/html/2401.00001v2/figures/old.png", nested_redirect),
            Route("/html/2401.00001v2/figures/new.png", nested_image),
            Route("/html/2401.00001v2/figures/escape.png", escaping_redirect),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://arxiv.org"
    ) as client:
        yield client


async def test_fetch_html_asset_validates_bytes_and_safe_redirects(
    figure_http: httpx.AsyncClient,
) -> None:
    direct = await fetch_html_asset(
        figure_http,
        base="https://arxiv.org",
        versioned="2401.00001v2",
        source="good.png",
    )
    redirected = await fetch_html_asset(
        figure_http,
        base="https://arxiv.org",
        versioned="2401.00001v2",
        source="redirect.png",
    )

    assert direct.ext == redirected.ext == "png"
    assert direct.content_type == redirected.content_type == "image/png"


async def test_fetch_html_asset_rejects_cross_origin_redirect(
    figure_http: httpx.AsyncClient,
) -> None:
    with pytest.raises(FigureAssetError) as caught:
        await fetch_html_asset(
            figure_http,
            base="https://arxiv.org",
            versioned="2401.00001v2",
            source="unsafe.png",
        )

    assert caught.value.code == "unsafe_asset_url"


async def test_fetch_html_asset_resolves_relative_redirect_against_current_directory(
    figure_http: httpx.AsyncClient,
) -> None:
    payload = await fetch_html_asset(
        figure_http,
        base="https://arxiv.org",
        versioned="2401.00001v2",
        source="figures/old.png",
    )

    assert payload.ext == "png"


async def test_fetch_html_asset_rejects_relative_redirect_outside_current_version(
    figure_http: httpx.AsyncClient,
) -> None:
    with pytest.raises(FigureAssetError) as caught:
        await fetch_html_asset(
            figure_http,
            base="https://arxiv.org",
            versioned="2401.00001v2",
            source="figures/escape.png",
        )

    assert caught.value.code == "unsafe_asset_url"


@pytest.mark.parametrize(
    ("source", "max_bytes", "expected_code"),
    [("missing.png", 1024, "asset_http_status"), ("good.png", 10, "asset_too_large")],
)
async def test_fetch_html_asset_rejects_bad_status_and_oversized_response(
    figure_http: httpx.AsyncClient,
    source: str,
    max_bytes: int,
    expected_code: str,
) -> None:
    with pytest.raises(FigureAssetError) as caught:
        await fetch_html_asset(
            figure_http,
            base="https://arxiv.org",
            versioned="2401.00001v2",
            source=source,
            max_bytes=max_bytes,
        )

    assert caught.value.code == expected_code


class _RecordingStorage:
    assets_bucket = "assets"

    def __init__(self) -> None:
        self.puts: list[tuple[str, str, bytes, str]] = []

    async def put(
        self, bucket: str, key: str, body: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        self.puts.append((bucket, key, body, content_type))


def _figure_run(
    fig: Block,
    binary_files: dict[str, bytes],
    *,
    source_format: str = "latex",
) -> tuple[IngestRun, _RecordingStorage]:
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.parsed = SimpleNamespace(figures=[fig])
    run.paper_id = "paper-id"
    run.source_format = source_format
    run.latex_binary_files = binary_files
    run.latex_main_tex_name = "paper/main.tex"
    run.latex_graphicspaths = ["../images/"]
    run.ref = None
    run.deps = SimpleNamespace(s3=storage, http=None, redis=None)
    return run, storage


async def test_pipeline_uploads_only_canonical_validated_figure_payload() -> None:
    fig = Block(id="fig-1", type="figure", asset_key="plot")
    run, storage = _figure_run(fig, {"images/plot.png": _raster_bytes("PNG")})

    output, warnings, failures = await run._save_figures("revision-id")

    expected_key = StorageKeys.figure("paper-id", "revision-id", "fig-1", "png")
    assert fig.asset_key == expected_key
    assert output["fig-1"].startswith(b"\x89PNG")
    assert warnings == []
    assert failures == []
    assert [(bucket, key, content_type) for bucket, key, _body, content_type in storage.puts] == [
        ("assets", expected_key, "image/png")
    ]


@pytest.mark.parametrize(
    ("requested", "expected_code"),
    [("", "missing_asset_key"), (r"\iftoggle{largefigures", "invalid_asset_path")],
)
async def test_pipeline_failure_clears_public_asset_key_and_records_diagnostic(
    requested: str, expected_code: str
) -> None:
    fig = Block(id="fig-bad", type="figure", asset_key=requested)
    run, storage = _figure_run(fig, {"images/plot.png": _raster_bytes("PNG")})

    output, warnings, failures = await run._save_figures("revision-id")

    assert fig.asset_key is None
    assert output == {}
    assert storage.puts == []
    assert warnings and "fig-bad" in warnings[0]
    assert failures == [
        {
            "code": expected_code,
            "figure_id": "fig-bad",
            "source": "latex",
        }
    ]


async def test_pipeline_records_missing_asset_key_without_upload() -> None:
    fig = Block(id="fig-missing", type="figure", asset_key=None)
    run, storage = _figure_run(fig, {})

    output, warnings, failures = await run._save_figures("revision-id")

    assert fig.asset_key is None
    assert output == {}
    assert storage.puts == []
    assert warnings and "fig-missing" in warnings[0]
    assert failures == [
        {
            "code": "missing_asset_key",
            "figure_id": "fig-missing",
            "source": "latex",
        }
    ]


async def test_pipeline_allows_safe_inline_html_figure_without_asset_diagnostic() -> None:
    parsed = parse_arxiv_html(
        """<article class="ltx_document"><section class="ltx_section">
<h2 class="ltx_title">Result</h2><figure id="fig-inline" class="ltx_figure">
<svg width="10" height="10"><path d="M0 0 L10 10"></path></svg>
</figure></section></article>"""
    )
    fig = parsed.figures[0]
    assert fig.asset_key is None
    assert fig.raw is not None
    run, storage = _figure_run(fig, {}, source_format="arxiv_html")

    output, warnings, failures = await run._save_figures("revision-id")

    assert fig.asset_key is None
    assert output == {}
    assert storage.puts == []
    assert warnings == []
    assert failures == []
