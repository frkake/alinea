"""Generic and hostile-input tests for paper figure materialization."""

from __future__ import annotations

import asyncio
import gzip
import io
import multiprocessing as mp
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
from alinea_worker import pipeline as worker_pipeline
from alinea_worker.figure_assets import (
    FigureAssetError,
    FigureAssetPayload,
    asset_candidates,
    extract_graphicspaths,
    fetch_html_asset,
    html_asset_url,
    isolated_figure_asset_payload,
    normalize_requested_asset,
)
from alinea_worker.figure_assets import (
    _figure_asset_payload_trusted as figure_asset_payload,
)
from alinea_worker.figure_assets import (
    _resolve_latex_asset_trusted as resolve_latex_asset,
)
from alinea_worker.figure_assets import (
    _validate_image_payload_trusted as validate_image_payload,
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


def _animated_gif_bytes() -> bytes:
    stream = io.BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("navy", "white")]
    frames[0].save(stream, format="GIF", save_all=True, append_images=frames[1:])
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


def _isolated_sleep_worker(
    _data: bytes, *, source_name: str, content_type: str | None = None
) -> FigureAssetPayload:
    import time

    del source_name, content_type
    time.sleep(5)
    return FigureAssetPayload(b"x", "png", "image/png", 1, 1)


def _isolated_crash_worker(
    _data: bytes, *, source_name: str, content_type: str | None = None
) -> FigureAssetPayload:
    import os

    del source_name, content_type
    os._exit(17)


def _isolated_oversize_worker(
    _data: bytes, *, source_name: str, content_type: str | None = None
) -> FigureAssetPayload:
    del source_name, content_type
    return FigureAssetPayload(b"x" * 64, "png", "image/png", 1, 1)


def _isolated_rlimit_worker(
    _data: bytes, *, source_name: str, content_type: str | None = None
) -> FigureAssetPayload:
    import resource

    del source_name, content_type
    soft_limit, _hard_limit = resource.getrlimit(resource.RLIMIT_AS)
    return FigureAssetPayload(b"x", "png", "image/png", soft_limit // (1024 * 1024), 1)


def _isolated_thumbnail_pixel_limit_worker(
    data: bytes, *, source_name: str, content_type: str | None = None
) -> object:
    from alinea_worker import figure_assets as child_assets

    child_assets.MAX_IMAGE_PIXELS = 1
    return child_assets._thumbnail_payload_trusted(
        data,
        source_name=source_name,
        content_type=content_type,
    )


def _isolated_thumbnail_dimension_limit_worker(
    data: bytes, *, source_name: str, content_type: str | None = None
) -> object:
    from alinea_worker import figure_assets as child_assets

    child_assets.MAX_IMAGE_DIMENSION = 1
    return child_assets._thumbnail_payload_trusted(
        data,
        source_name=source_name,
        content_type=content_type,
    )


def _isolated_thumbnail_frame_limit_worker(
    data: bytes, *, source_name: str, content_type: str | None = None
) -> object:
    from alinea_worker import figure_assets as child_assets

    child_assets.MAX_IMAGE_FRAMES = 1
    return child_assets._thumbnail_payload_trusted(
        data,
        source_name=source_name,
        content_type=content_type,
    )


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


def test_postscript_conversion_preserves_size_failure_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized_raster = _raster_bytes("PNG") + b"padding" * 100
    monkeypatch.setattr(figure_assets, "MAX_ASSET_BYTES", len(EPS_BYTES) + 1)

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(
            EPS_BYTES,
            source_name="figure.eps",
            postscript_converter=lambda _content, _format: oversized_raster,
        )

    assert caught.value.code == "asset_too_large"


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


def test_validate_image_payload_enforces_explicit_animation_frame_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("navy", "white", "red")]
    frames[0].save(stream, format="GIF", save_all=True, append_images=frames[1:])
    monkeypatch.setattr(figure_assets, "MAX_IMAGE_FRAMES", 2)

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


def test_unknown_magic_is_rejected_before_pillow_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[bool] = []

    def unexpected_open(*_args: Any, **_kwargs: Any) -> Any:
        opened.append(True)
        raise AssertionError("Pillow must not receive unknown magic")

    monkeypatch.setattr(figure_assets.Image, "open", unexpected_open)

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(b"not-a-known-image", source_name="figure.bin")

    assert caught.value.code == "unsupported_figure_format"
    assert opened == []


def test_default_raster_resource_limits_are_bounded_for_child_memory() -> None:
    assert figure_assets.MAX_IMAGE_DIMENSION <= 12_000
    assert figure_assets.MAX_IMAGE_PIXELS <= 25_000_000
    assert figure_assets.MAX_IMAGE_FRAMES <= 128
    assert figure_assets.MAX_CONVERSION_MEMORY_BYTES <= 512 * 1024 * 1024


def test_pdf_and_svg_are_rendered_to_valid_png() -> None:
    for source_name, source in (("figure.pdf", _pdf_bytes()), ("figure.svg", SVG_BYTES)):
        payload = figure_asset_payload(source, source_name=source_name)

        assert payload.ext == "png"
        assert payload.content_type == "image/png"
        assert payload.content.startswith(b"\x89PNG")
        assert payload.width > 0
        assert payload.height > 0


@pytest.mark.parametrize(
    ("worker", "timeout_s", "max_output_bytes", "expected_code"),
    [
        (_isolated_sleep_worker, 0.05, 1024, "conversion_timeout"),
        (_isolated_crash_worker, 5.0, 1024, "conversion_crashed"),
        (_isolated_oversize_worker, 5.0, 32, "conversion_oversize"),
    ],
    ids=["timeout", "crash", "oversize"],
)
async def test_isolated_conversion_reports_stable_resource_failures(
    worker: Any,
    timeout_s: float,
    max_output_bytes: int,
    expected_code: str,
) -> None:
    with pytest.raises(FigureAssetError) as caught:
        await isolated_figure_asset_payload(
            SVG_BYTES,
            source_name="figure.svg",
            timeout_s=timeout_s,
            max_output_bytes=max_output_bytes,
            worker=worker,
        )

    assert caught.value.code == expected_code


async def test_default_isolated_conversion_returns_validated_png() -> None:
    payload = await isolated_figure_asset_payload(
        SVG_BYTES,
        source_name="figure.svg",
        timeout_s=10.0,
    )

    assert payload.ext == "png"
    assert payload.content_type == "image/png"
    assert payload.content.startswith(b"\x89PNG")


async def test_isolated_conversion_child_receives_memory_rlimit() -> None:
    payload = await isolated_figure_asset_payload(
        SVG_BYTES,
        source_name="figure.svg",
        timeout_s=10.0,
        worker=_isolated_rlimit_worker,
    )

    assert payload.width <= 512


async def test_thumbnail_decode_and_render_are_isolated_from_main_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import alinea_core.ingest.thumbnail as thumbnail_module

    monkeypatch.setattr(
        thumbnail_module.Image,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("main process must not decode thumbnail source")
        ),
    )

    payload = await figure_assets.isolated_thumbnail_payload(_raster_bytes("PNG"), timeout_s=10.0)

    assert payload.card.startswith(b"RIFF")
    assert payload.retina.startswith(b"RIFF")


async def test_thumbnail_timeout_is_stable_and_child_is_reaped() -> None:
    existing_children = {child.pid for child in mp.active_children()}

    with pytest.raises(FigureAssetError) as caught:
        await figure_assets.isolated_thumbnail_payload(
            _raster_bytes("PNG"),
            timeout_s=0.05,
            worker=_isolated_sleep_worker,
        )

    assert caught.value.code == "thumbnail_timeout"
    assert {child.pid for child in mp.active_children()} <= existing_children


@pytest.mark.parametrize(
    ("worker", "data"),
    [
        (_isolated_thumbnail_dimension_limit_worker, _raster_bytes("PNG")),
        (_isolated_thumbnail_pixel_limit_worker, _raster_bytes("PNG")),
        (_isolated_thumbnail_frame_limit_worker, _animated_gif_bytes()),
    ],
    ids=["dimension", "pixels", "frames"],
)
async def test_thumbnail_child_enforces_raster_limits_before_render(
    worker: Any,
    data: bytes,
) -> None:
    with pytest.raises(FigureAssetError) as caught:
        await figure_assets.isolated_thumbnail_payload(
            data,
            timeout_s=10.0,
            worker=worker,
        )

    assert caught.value.code == "image_too_large"


class _UnreapableProcess:
    exitcode = None

    def __init__(self) -> None:
        self.actions: list[str] = []

    def is_alive(self) -> bool:
        return True

    def terminate(self) -> None:
        self.actions.append("terminate")

    def kill(self) -> None:
        self.actions.append("kill")

    def join(self, timeout: float) -> None:
        self.actions.append(f"join:{timeout}")


def test_unreapable_child_raises_stable_lifecycle_failure() -> None:
    process = _UnreapableProcess()

    with pytest.raises(FigureAssetError) as caught:
        figure_assets._terminate_and_reap(process, failure_code="conversion_lifecycle")

    assert caught.value.code == "conversion_lifecycle"
    assert process.actions == ["terminate", "join:0.5", "kill", "join:0.5"]


def test_sync_decoder_is_not_exported_as_production_api() -> None:
    assert "figure_asset_payload" not in figure_assets.__all__
    assert not hasattr(figure_assets, "figure_asset_payload")
    assert not hasattr(figure_assets, "validate_image_payload")
    assert not hasattr(figure_assets, "inline_svg_payload")
    assert not hasattr(figure_assets, "resolve_latex_asset")


async def test_isolated_conversion_reports_process_start_failure_as_crash() -> None:
    unpicklable_worker = lambda *_args, **_kwargs: FigureAssetPayload(  # noqa: E731
        b"x", "png", "image/png", 1, 1
    )

    with pytest.raises(FigureAssetError) as caught:
        await isolated_figure_asset_payload(
            SVG_BYTES,
            source_name="figure.svg",
            timeout_s=1.0,
            worker=unpicklable_worker,
        )

    assert caught.value.code == "conversion_crashed"


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
        b'<svg xmlns="http://www.w3.org/2000/svg" srcdoc="active markup"/>',
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
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10" preserveAspectRatio="xMidYMid meet">
<defs>
  <linearGradient id="gradient" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="blue"/></linearGradient>
  <path id="shape" d="M0 0 L10 0 L10 10 Z"/>
</defs>
<rect width="20" height="10" fill="url(#gradient)"/>
<use href="#shape"/>
</svg>"""

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert payload.width > 0
    assert payload.height > 0


@pytest.mark.parametrize(
    "attribute",
    [
        'style="fill:image-set(url(#gradient) 1x)"',
        'style="fill:cross-fade(url(#gradient), #fff)"',
        'style="fill:element(#shape)"',
        'style="fill:paint(worklet)"',
        'style="fill:var(--author-value)"',
        'fill="image-set(url(#gradient) 1x)"',
        'style="fill:i\\6d age-set(url(#gradient) 1x)"',
        'style="fill:red[unexpected]"',
        'style="fill:rgb(0,0,0"',
        'fill="red[unexpected]"',
        'fill="rgb(0,0,0"',
        'fill="\'red"',
    ],
)
def test_svg_css_rejects_non_allowlisted_resource_functions_before_render(
    monkeypatch: pytest.MonkeyPatch, attribute: str
) -> None:
    source = f"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<defs><linearGradient id="gradient"/></defs><rect {attribute} width="20" height="10"/>
</svg>""".encode()
    rendered: list[str] = []

    def fake_render(_content: bytes, filetype: str) -> Any:
        rendered.append(filetype)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", fake_render)

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "unsafe_vector"
    assert rendered == []


def test_svg_style_attribute_allows_safe_colors_numbers_and_internal_gradient() -> None:
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<defs><linearGradient id="gradient"><stop offset="0" stop-color="blue"/></linearGradient></defs>
<rect width="20" height="10" fill="url(#gradient)" style="stroke:rgb(0,0,0);stroke-width:1;opacity:.5"/>
</svg>"""

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"


def test_svg_style_transform_with_pixel_units_is_rendered() -> None:
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<rect width="8" height="6" style="fill:navy;transform:translate(2px, 1px)"/>
</svg>"""

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert payload.content.startswith(b"\x89PNG")


def test_svg_internal_clip_path_presentation_style_is_rendered() -> None:
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<defs><clipPath id="plot-clip"><rect width="10" height="10"/></clipPath></defs>
<rect width="20" height="10" style="clip-path:url(#plot-clip);fill:navy"/>
</svg>"""

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert payload.content.startswith(b"\x89PNG")


def test_svg_sanitizer_strips_data_and_foreign_metadata_before_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"""<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 width="20" height="10" data-author="untrusted">
<metadata><rdf:RDF><rdf:Description>author@example.org</rdf:Description></rdf:RDF></metadata>
<g inkscape:label="Plot (a)"><rect width="20" height="10" fill="navy"/></g>
</svg>"""
    rendered: list[bytes] = []

    def capture_render(content: bytes, _filetype: str) -> FigureAssetPayload:
        rendered.append(content)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", capture_render)

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert len(rendered) == 1
    assert b"data-author" not in rendered[0]
    assert b"inkscape" not in rendered[0]
    assert b"rdf" not in rendered[0].lower()
    assert b"<rect" in rendered[0]


@pytest.mark.parametrize(
    "attribute",
    [
        'width="javascript:alert(1)"',
        'width="1e999"',
        'd="M0 0 L10 10;url(https://example.org/x)"',
        'd="M 1e999 0"',
        'gradientUnits="networkResource"',
        'preserveAspectRatio="xMidYMid onload(1)"',
    ],
)
def test_svg_geometry_attributes_reject_invalid_semantic_syntax_before_render(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
) -> None:
    source = f"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<path {attribute}/></svg>""".encode()
    rendered: list[str] = []
    monkeypatch.setattr(
        figure_assets,
        "_render_document",
        lambda _data, filetype: rendered.append(filetype),
    )

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "unsafe_vector"
    assert rendered == []


@pytest.mark.parametrize(
    "source",
    [
        b"""<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:dc="http://purl.org/dc/elements/1.1/" width="72pt" height="36pt"
 viewBox="0 0 72 36" version="1.1">
<metadata><dc:date>2026-07-11</dc:date></metadata>
<g id="figure_1"><g id="axes_1" transform="translate(7.2 28.8)">
<path d="M 0 0 L 50 0 L 50 -20 Z" style="fill:none;stroke:#1f77b4"/>
</g></g></svg>""",
        b"""<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
 xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
 width="20" height="10" viewBox="0 0 20 10">
<sodipodi:namedview inkscape:document-units="px"/>
<g inkscape:groupmode="layer" inkscape:label="Layer 1">
<rect width="20" height="10" fill="#1f77b4"/></g></svg>""",
        b"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"
 role="img" aria-label="Plot (a)" class="ltx_graphics" data-mml-node="svg">
<title id="plot-title">Plot (a)</title><desc>author@example.org</desc>
<g transform="translate(1 1)"><path d="M0 0 L18 0 L18 8 Z" fill="navy"/></g>
</svg>""",
    ],
    ids=["matplotlib", "inkscape", "arxiv-html"],
)
def test_representative_academic_svg_dialects_are_sanitized_and_rendered(source: bytes) -> None:
    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert payload.content.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "stylesheet",
    [
        "rect{fill:red}",
        "rect{}",
        ".plot, #mark{fill:red}",
        "g > rect.plot{fill:red}",
    ],
    ids=["declaration", "empty", "selector-list", "child-selector"],
)
def test_svg_stylesheet_allows_safe_selector_property_and_value(stylesheet: str) -> None:
    source = f"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<style>{stylesheet}</style><rect width="20" height="10"/>
</svg>""".encode()

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert payload.content.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "stylesheet",
    [
        "rect[href]{fill:red}",
        "rect:hover{fill:red}",
        "rect{fill:url(https://example.org/tracker.png)}",
        "rect{fill:image-set(url(#gradient) 1x)}",
        "rect{fill:var(--author-color)}",
        "rect{fill:r\\65 d}",
        "rect{fill:red[unexpected]}",
        "rect{fill:rgb(0,0,0}",
    ],
)
def test_svg_stylesheet_rejects_non_allowlisted_selectors_and_values(
    stylesheet: str,
) -> None:
    source = f"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<style>{stylesheet}</style><rect width="20" height="10"/>
</svg>""".encode()

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "unsafe_vector"


def test_svg_enforces_dedicated_byte_limit_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(figure_assets, "MAX_SVG_BYTES", len(SVG_BYTES) - 1)
    monkeypatch.setattr(
        figure_assets,
        "_render_document",
        lambda _data, filetype: rendered.append(filetype),
    )

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(SVG_BYTES, source_name="figure.svg")

    assert caught.value.code == "asset_too_large"
    assert rendered == []


def test_inline_svg_enforces_byte_limit_before_html_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed: list[str] = []

    def unexpected_parser(_raw: str) -> Any:
        parsed.append("called")
        raise AssertionError("Lexbor must not receive oversized inline HTML")

    monkeypatch.setattr(figure_assets, "MAX_INLINE_SVG_HTML_BYTES", 4)
    monkeypatch.setattr(figure_assets, "LexborHTMLParser", unexpected_parser)

    with pytest.raises(FigureAssetError) as caught:
        figure_assets.extract_inline_svg("<svg/>")

    assert caught.value.code == "asset_too_large"
    assert parsed == []


@pytest.mark.parametrize(
    ("limit_name", "limit", "source"),
    [
        (
            "MAX_SVG_ELEMENTS",
            2,
            b'<svg xmlns="http://www.w3.org/2000/svg"><g/><path/></svg>',
        ),
        (
            "MAX_SVG_DEPTH",
            2,
            b'<svg xmlns="http://www.w3.org/2000/svg"><g><path/></g></svg>',
        ),
        (
            "MAX_SVG_TEXT_CHARS",
            3,
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>long</text></svg>',
        ),
    ],
)
def test_svg_enforces_streaming_complexity_limits_before_render(
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit: int,
    source: bytes,
) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(figure_assets, limit_name, limit)
    monkeypatch.setattr(
        figure_assets,
        "_render_document",
        lambda _data, filetype: rendered.append(filetype),
    )

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "vector_too_complex"
    assert rendered == []


@pytest.mark.parametrize(
    "source",
    [b" ", b'<svg xmlns="http://www.w3.org/2000/svg"><g></svg>'],
    ids=["missing-root", "unbalanced-events"],
)
def test_streaming_svg_parser_rejects_missing_root_or_unbalanced_events(source: bytes) -> None:
    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "invalid_vector"


def test_inline_svg_rejects_parser_without_document_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fragment = SimpleNamespace(
        root=None,
        css=lambda _selector: [SimpleNamespace(html="<svg/>")],
    )
    monkeypatch.setattr(figure_assets, "LexborHTMLParser", lambda _raw: fragment)

    with pytest.raises(FigureAssetError) as caught:
        figure_assets.extract_inline_svg("<svg/>")

    assert caught.value.code == "unsafe_inline_figure"


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


def test_html_asset_url_preserves_configured_base_path() -> None:
    assert (
        html_asset_url("https://mirror.example/arxiv", "2401.00001v2", "figures/x1.png")
        == "https://mirror.example/arxiv/html/2401.00001v2/figures/x1.png"
    )
    assert (
        html_asset_url("https://mirror.example/arxiv", "2401.00001v2", "html/2401.00001v2/x1.png")
        == "https://mirror.example/arxiv/html/2401.00001v2/x1.png"
    )


@pytest.mark.parametrize(
    "source",
    [
        "/html/2401.00001v2/x1.png",
        "/arxiv/html/2401.00001v2/x1.png",
    ],
)
def test_html_asset_url_normalizes_mirror_absolute_html_paths(source: str) -> None:
    assert (
        html_asset_url("https://mirror.example/arxiv", "2401.00001v2", source)
        == "https://mirror.example/arxiv/html/2401.00001v2/x1.png"
    )


def test_html_asset_url_rejects_same_origin_absolute_url_outside_base_path() -> None:
    with pytest.raises(FigureAssetError) as caught:
        html_asset_url(
            "https://mirror.example/arxiv",
            "2401.00001v2",
            "https://mirror.example/html/2401.00001v2/x1.png",
        )

    assert caught.value.code == "unsafe_asset_url"


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


async def test_fetch_html_asset_calls_hook_once_immediately_before_each_get() -> None:
    events: list[str] = []
    png = _raster_bytes("PNG")

    async def redirect(_request: Any) -> RedirectResponse:
        events.append("get:redirect")
        return RedirectResponse("final.png", status_code=302)

    async def final(_request: Any) -> Response:
        events.append("get:final")
        return Response(png, media_type="image/png")

    async def before_request() -> None:
        events.append("throttle")

    app = Starlette(
        routes=[
            Route("/html/2401.00001v2/redirect.png", redirect),
            Route("/html/2401.00001v2/final.png", final),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://arxiv.org"
    ) as client:
        payload = await fetch_html_asset(
            client,
            base="https://arxiv.org",
            versioned="2401.00001v2",
            source="redirect.png",
            before_request=before_request,
        )

    assert payload.ext == "png"
    assert events == ["throttle", "get:redirect", "throttle", "get:final"]


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


async def test_fetch_html_asset_applies_one_wall_deadline_across_redirects() -> None:
    png = _raster_bytes("PNG")

    async def first(_request: Any) -> RedirectResponse:
        await asyncio.sleep(0.03)
        return RedirectResponse("second.png", status_code=302)

    async def second(_request: Any) -> Response:
        await asyncio.sleep(0.03)
        return Response(png, media_type="image/png")

    app = Starlette(
        routes=[
            Route("/html/2401.00001v2/first.png", first),
            Route("/html/2401.00001v2/second.png", second),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://arxiv.org"
    ) as client:
        with pytest.raises(FigureAssetError) as caught:
            await fetch_html_asset(
                client,
                base="https://arxiv.org",
                versioned="2401.00001v2",
                source="first.png",
                total_timeout_s=0.05,
            )

    assert caught.value.code == "asset_fetch_timeout"


class _RecordingStorage:
    assets_bucket = "assets"

    def __init__(self) -> None:
        self.puts: list[tuple[str, str, bytes, str]] = []
        self.deletes: list[tuple[str, list[str]]] = []
        self.fail_delete = False
        self.fail_put_at: int | None = None

    async def put(
        self, bucket: str, key: str, body: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        self.puts.append((bucket, key, body, content_type))
        if self.fail_put_at == len(self.puts):
            raise RuntimeError("put failed")

    async def delete_many(self, bucket: str, keys: Any) -> None:
        self.deletes.append((bucket, list(keys)))
        if self.fail_delete:
            raise RuntimeError("cleanup failed")


class _StructuringSession:
    def __init__(self, paper: Any, commit_error: Exception | None) -> None:
        self.paper = paper
        self.commit_error = commit_error
        self.added: list[Any] = []

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.added[-1].id = "revision-new"

    async def get(self, _model: Any, _key: Any) -> Any:
        return self.paper

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error


async def test_staged_assets_cleanup_after_index_failure_preserves_original() -> None:
    error = RuntimeError("search index failed")
    storage = _RecordingStorage()
    new_key = "figures/paper/revision/one.png"

    with pytest.raises(RuntimeError) as caught:
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            uploaded_keys.append(new_key)
            await storage.put("assets", new_key, b"figure", content_type="image/png")
            raise error

    assert caught.value is error
    assert storage.deletes == [("assets", [new_key])]


async def test_staged_assets_cleanup_after_cancellation_preserves_cancellation() -> None:
    storage = _RecordingStorage()
    new_key = "figures/paper/revision/one.png"
    entered = asyncio.Event()

    async def publish() -> None:
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            uploaded_keys.append(new_key)
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(publish())
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    assert storage.deletes == [("assets", [new_key])]


async def test_cleanup_failure_does_not_replace_commit_failure() -> None:
    error = RuntimeError("database commit failed")
    storage = _RecordingStorage()
    storage.fail_delete = True
    new_key = "figures/paper/revision/one.png"

    with pytest.raises(RuntimeError) as caught:
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            uploaded_keys.append(new_key)
            raise error

    assert caught.value is error
    assert storage.deletes == [("assets", [new_key])]


@pytest.mark.parametrize("failure_phase", ["index", "commit"])
async def test_structure_stage_cleans_uploads_and_restores_pointer_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    parsed = parse_arxiv_html(
        """<article class="ltx_document"><section class="ltx_section">
<h2 class="ltx_title">Result</h2><figure class="ltx_figure">
<img class="ltx_graphics" src="plot.png"/></figure></section></article>"""
    )
    figure = parsed.figures[0]
    storage = _RecordingStorage()
    old_thumbnail = "thumbnails/paper-id/revision-old/card.webp"
    paper = SimpleNamespace(thumbnail_key=old_thumbnail)
    error = RuntimeError(f"{failure_phase} failed")
    run = object.__new__(IngestRun)
    run.parsed = parsed
    run.paper_id = "paper-id"
    run.source_version = "v1"
    run.source_format = "arxiv_html"
    run.is_pdf_upload = False
    run._candidate_failures = []
    run._candidate_completeness = {}
    run.revision_id = None
    run.content = None
    run.session = _StructuringSession(
        paper,
        error if failure_phase == "commit" else None,
    )
    run.deps = SimpleNamespace(s3=storage)
    figure_key = "figures/paper-id/revision-new/figure.png"

    async def save_figures(
        _run: IngestRun,
        _revision_id: str,
        *,
        uploaded_keys: list[str] | None = None,
        deadline: Any = None,
    ) -> tuple[dict[str, bytes], list[str], list[dict[str, str]]]:
        del deadline
        assert uploaded_keys is not None
        uploaded_keys.append(figure_key)
        await storage.put("assets", figure_key, _raster_bytes("PNG"), content_type="image/png")
        figure.asset_key = figure_key
        return {figure.id: _raster_bytes("PNG")}, [], []

    async def rebuild_index(*_args: Any, **_kwargs: Any) -> None:
        if failure_phase == "index":
            raise error

    async def render_thumbnail_isolated(
        _png: bytes, **_kwargs: Any
    ) -> figure_assets.ThumbnailPayload:
        return figure_assets.ThumbnailPayload(card=b"card", retina=b"retina")

    monkeypatch.setattr(IngestRun, "_save_figures", save_figures)
    monkeypatch.setattr(worker_pipeline, "rebuild_block_search_index", rebuild_index)
    monkeypatch.setattr(
        worker_pipeline,
        "isolated_thumbnail_payload",
        render_thumbnail_isolated,
    )

    with pytest.raises(RuntimeError) as caught:
        await run._structure()

    assert caught.value is error
    assert paper.thumbnail_key == old_thumbnail
    expected_keys = [figure_key]
    if failure_phase == "commit":
        expected_keys.extend(
            [
                "thumbnails/paper-id/revision-new/card.webp",
                "thumbnails/paper-id/revision-new/card@2x.webp",
            ]
        )
    assert storage.deletes == [("assets", expected_keys)]


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


async def test_first_figure_put_failure_is_cleaned_by_revision_stage() -> None:
    fig = Block(id="fig-1", type="figure", asset_key="plot.png")
    run, storage = _figure_run(fig, {"plot.png": _raster_bytes("PNG")})
    storage.fail_put_at = 1

    with pytest.raises(RuntimeError, match="put failed"):
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            await run._save_figures("revision-id", uploaded_keys=uploaded_keys)

    expected_key = StorageKeys.figure("paper-id", "revision-id", "fig-1", "png")
    assert storage.deletes == [("assets", [expected_key])]


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


async def test_pipeline_routes_known_raster_through_isolated_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    png = _raster_bytes("PNG")
    calls: list[str] = []

    async def isolated(
        data: bytes,
        *,
        source_name: str,
        content_type: str | None = None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        del content_type
        calls.append(source_name)
        return validate_image_payload(data)

    monkeypatch.setattr(worker_pipeline, "isolated_figure_asset_payload", isolated)
    fig = Block(id="fig-raster", type="figure", asset_key="plot.png")
    run, _storage = _figure_run(fig, {"plot.png": png})

    output, warnings, failures = await run._save_figures("revision-id")

    assert calls == ["plot.png"]
    assert output[fig.id] == png
    assert warnings == []
    assert failures == []


async def test_document_deadline_stops_later_figure_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    deadline = worker_pipeline.MaterializationDeadline.start(timeout_s=1.0, clock=lambda: now[0])
    calls: list[str] = []
    png = _raster_bytes("PNG")

    async def isolated(
        data: bytes,
        *,
        source_name: str,
        content_type: str | None = None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        del content_type
        calls.append(source_name)
        now[0] += 1.1
        return validate_image_payload(data)

    monkeypatch.setattr(worker_pipeline, "isolated_figure_asset_payload", isolated)
    figures = [
        Block(id=f"fig-{index}", type="figure", asset_key=f"plot-{index}.png") for index in range(3)
    ]
    run, _storage = _figure_run(figures[0], {f"plot-{index}.png": png for index in range(3)})
    run.parsed.figures = figures

    output, _warnings, failures = await run._save_figures("revision-id", deadline=deadline)

    assert calls == ["plot-0.png"]
    assert set(output) == {"fig-0"}
    assert [failure["code"] for failure in failures] == [
        "materialization_timeout",
        "materialization_timeout",
    ]


@pytest.mark.parametrize(
    ("document_timeout_s", "raised_code", "expected_code"),
    [
        (
            worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S - 1.0,
            "conversion_timeout",
            "materialization_timeout",
        ),
        (
            worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S,
            "conversion_timeout",
            "conversion_timeout",
        ),
        (
            worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S + 1.0,
            "conversion_timeout",
            "conversion_timeout",
        ),
        (
            worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S - 1.0,
            "conversion_crashed",
            "conversion_crashed",
        ),
    ],
    ids=[
        "document-limited",
        "equal-operation-limit",
        "operation-limited",
        "non-timeout-error",
    ],
)
async def test_conversion_timeout_maps_only_when_document_deadline_shortens_limit(
    monkeypatch: pytest.MonkeyPatch,
    document_timeout_s: float,
    raised_code: str,
    expected_code: str,
) -> None:
    observed_timeouts: list[float] = []

    async def fail_conversion(
        _data: bytes,
        *,
        source_name: str,
        content_type: str | None = None,
        timeout_s: float,
    ) -> FigureAssetPayload:
        del source_name, content_type
        observed_timeouts.append(timeout_s)
        raise FigureAssetError(raised_code, "conversion failed")

    monkeypatch.setattr(
        worker_pipeline,
        "isolated_figure_asset_payload",
        fail_conversion,
    )
    deadline = worker_pipeline.MaterializationDeadline.start(
        timeout_s=document_timeout_s,
        clock=lambda: 0.0,
    )

    with pytest.raises(FigureAssetError) as caught:
        await worker_pipeline._materialize_figure_payload(
            _raster_bytes("PNG"),
            "figure.png",
            deadline=deadline,
        )

    assert caught.value.code == expected_code
    assert observed_timeouts == [
        min(document_timeout_s, worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S)
    ]


@pytest.mark.parametrize(
    ("document_timeout_s", "raised_code", "expected_code"),
    [
        (
            worker_pipeline.MAX_HTML_ASSET_FETCH_SECONDS - 1.0,
            "asset_fetch_timeout",
            "materialization_timeout",
        ),
        (
            worker_pipeline.MAX_HTML_ASSET_FETCH_SECONDS,
            "asset_fetch_timeout",
            "asset_fetch_timeout",
        ),
        (
            worker_pipeline.MAX_HTML_ASSET_FETCH_SECONDS + 1.0,
            "asset_fetch_timeout",
            "asset_fetch_timeout",
        ),
        (
            worker_pipeline.MAX_HTML_ASSET_FETCH_SECONDS - 1.0,
            "asset_too_large",
            "asset_too_large",
        ),
    ],
    ids=[
        "document-limited",
        "equal-operation-limit",
        "operation-limited",
        "non-timeout-error",
    ],
)
async def test_asset_fetch_timeout_maps_only_when_document_deadline_shortens_limit(
    monkeypatch: pytest.MonkeyPatch,
    document_timeout_s: float,
    raised_code: str,
    expected_code: str,
) -> None:
    observed_timeouts: list[float] = []

    async def fail_fetch(
        *_args: Any,
        total_timeout_s: float,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        observed_timeouts.append(total_timeout_s)
        raise FigureAssetError(raised_code, "asset fetch failed")

    monkeypatch.setattr(worker_pipeline, "fetch_html_asset", fail_fetch)
    figure = Block(id="fig-html-timeout", type="figure", asset_key="plot.png")
    run, storage = _figure_run(figure, {}, source_format="arxiv_html")
    run.ref = SimpleNamespace(versioned="test-version")
    run.deps = SimpleNamespace(
        s3=storage,
        http=object(),
        redis=None,
        settings=SimpleNamespace(alinea_arxiv_base_url="https://arxiv.org"),
    )
    deadline = worker_pipeline.MaterializationDeadline.start(
        timeout_s=document_timeout_s,
        clock=lambda: 0.0,
    )

    output, _warnings, failures = await run._save_figures(
        "revision-id",
        deadline=deadline,
    )

    assert output == {}
    assert failures[0]["code"] == expected_code
    assert observed_timeouts == [
        min(document_timeout_s, worker_pipeline.MAX_HTML_ASSET_FETCH_SECONDS)
    ]


@pytest.mark.parametrize(
    ("document_timeout_s", "raised_code", "expected_code"),
    [
        (
            worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S - 1.0,
            "thumbnail_timeout",
            "materialization_timeout",
        ),
        (
            worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S,
            "thumbnail_timeout",
            "thumbnail_timeout",
        ),
        (
            worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S + 1.0,
            "thumbnail_timeout",
            "thumbnail_timeout",
        ),
        (
            worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S - 1.0,
            "thumbnail_crashed",
            "thumbnail_crashed",
        ),
    ],
    ids=[
        "document-limited",
        "equal-operation-limit",
        "operation-limited",
        "non-timeout-error",
    ],
)
async def test_thumbnail_timeout_maps_only_when_document_deadline_shortens_limit(
    monkeypatch: pytest.MonkeyPatch,
    document_timeout_s: float,
    raised_code: str,
    expected_code: str,
) -> None:
    observed_timeouts: list[float] = []

    async def fail_thumbnail(
        _png: bytes,
        *,
        timeout_s: float,
    ) -> figure_assets.ThumbnailPayload:
        observed_timeouts.append(timeout_s)
        raise FigureAssetError(raised_code, "thumbnail failed")

    monkeypatch.setattr(worker_pipeline, "isolated_thumbnail_payload", fail_thumbnail)
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.paper_id = "paper-id"
    run.revision_id = "revision-id"
    run.deps = SimpleNamespace(s3=storage)
    figure = Block(id="fig-thumbnail", type="figure", asset_key="figure.png")
    paper = SimpleNamespace(thumbnail_key=None)
    deadline = worker_pipeline.MaterializationDeadline.start(
        timeout_s=document_timeout_s,
        clock=lambda: 0.0,
    )

    warnings = await run._make_thumbnail(
        paper,
        {figure.id: _raster_bytes("PNG")},
        [figure],
        deadline=deadline,
    )

    assert warnings == [f"サムネイル生成に失敗(続行): [{expected_code}]"]
    assert observed_timeouts == [
        min(document_timeout_s, worker_pipeline.DEFAULT_CONVERSION_TIMEOUT_S)
    ]
    assert storage.puts == []


def test_operation_timeout_reads_deadline_clock_once() -> None:
    clock_reads = 0

    def clock() -> float:
        nonlocal clock_reads
        clock_reads += 1
        return 2.0

    deadline = worker_pipeline.MaterializationDeadline(expires_at=12.0, clock=clock)

    timeout = worker_pipeline._operation_timeout(deadline, operation_limit_s=15.0)

    assert timeout.seconds == 10.0
    assert timeout.document_limited is True
    assert clock_reads == 1


@pytest.mark.parametrize(
    "code",
    [
        "asset_too_large",
        "conversion_crashed",
        "conversion_lifecycle",
        "conversion_oversize",
        "conversion_timeout",
        "figure_bytes_exceeded",
        "image_too_large",
        "materialization_timeout",
        "thumbnail_crashed",
        "thumbnail_lifecycle",
        "thumbnail_oversize",
        "thumbnail_timeout",
    ],
)
async def test_inline_svg_preserves_resource_failure_codes(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    async def fail_materialization(*_args: Any, **_kwargs: Any) -> FigureAssetPayload:
        raise FigureAssetError(code, "bounded resource failure")

    monkeypatch.setattr(
        worker_pipeline,
        "_materialize_figure_payload",
        fail_materialization,
    )

    with pytest.raises(FigureAssetError) as caught:
        await worker_pipeline._materialize_inline_svg(
            '<svg width="10" height="10"><rect width="10" height="10"/></svg>',
        )

    assert caught.value.code == code


async def test_pipeline_throttles_each_html_get_without_double_throttling_initial_request(
    figure_http: httpx.AsyncClient,
) -> None:
    fig = Block(id="fig-html", type="figure", asset_key="redirect.png")
    run, storage = _figure_run(fig, {}, source_format="arxiv_html")
    throttle_calls: list[object] = []
    redis = object()

    async def throttle(received_redis: object) -> None:
        throttle_calls.append(received_redis)

    run.ref = SimpleNamespace(versioned="2401.00001v2")
    run.deps = SimpleNamespace(
        s3=storage,
        http=figure_http,
        redis=redis,
        throttle=throttle,
        settings=SimpleNamespace(alinea_arxiv_base_url="https://arxiv.org"),
    )

    output, warnings, failures = await run._save_figures("revision-id")

    assert throttle_calls == [redis, redis]
    assert output[fig.id].startswith(b"\x89PNG")
    assert warnings == []
    assert failures == []


@pytest.mark.parametrize(
    ("source_name", "source"),
    [("plot.pdf", _pdf_bytes()), ("plot.svg", SVG_BYTES), ("plot.eps", EPS_BYTES)],
)
async def test_pipeline_routes_document_conversions_through_isolated_process(
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
    source: bytes,
) -> None:
    calls: list[str] = []

    async def isolated(
        data: bytes,
        *,
        source_name: str,
        content_type: str | None = None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        del data, content_type
        calls.append(source_name)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(worker_pipeline, "isolated_figure_asset_payload", isolated)
    fig = Block(id="fig-convert", type="figure", asset_key=source_name)
    run, storage = _figure_run(fig, {source_name: source})

    output, warnings, failures = await run._save_figures("revision-id")

    assert calls == [source_name]
    assert output[fig.id].startswith(b"\x89PNG")
    assert len(storage.puts) == 1
    assert warnings == []
    assert failures == []


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


async def test_pipeline_limits_figure_count_per_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    figures = [
        Block(id=f"fig-{index}", type="figure", asset_key=f"plot-{index}.png") for index in range(3)
    ]
    binary_files = {
        f"plot-{index}.png": _raster_bytes("PNG", color=color)
        for index, color in enumerate(("navy", "white", "red"))
    }
    run, storage = _figure_run(figures[0], binary_files)
    run.parsed.figures = figures
    monkeypatch.setattr(worker_pipeline, "MAX_FIGURES_PER_DOCUMENT", 2)

    output, _warnings, failures = await run._save_figures("revision-id")

    assert set(output) == {"fig-0", "fig-1"}
    assert len(storage.puts) == 2
    assert figures[2].asset_key is None
    assert failures == [
        {
            "code": "figure_limit_exceeded",
            "figure_id": "fig-2",
            "source": "latex",
        }
    ]


async def test_pipeline_limits_aggregate_retained_and_uploaded_figure_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    png = _raster_bytes("PNG")
    figures = [
        Block(id=f"fig-{index}", type="figure", asset_key=f"plot-{index}.png") for index in range(2)
    ]
    run, storage = _figure_run(
        figures[0],
        {"plot-0.png": png, "plot-1.png": png},
    )
    run.parsed.figures = figures
    monkeypatch.setattr(
        worker_pipeline,
        "MAX_TOTAL_FIGURE_MATERIALIZED_BYTES",
        len(png) * 2 + 1,
    )

    output, _warnings, failures = await run._save_figures("revision-id")

    assert set(output) == {"fig-0"}
    assert len(storage.puts) == 1
    assert figures[1].asset_key is None
    assert failures == [
        {
            "code": "figure_bytes_exceeded",
            "figure_id": "fig-1",
            "source": "latex",
        }
    ]


async def test_pipeline_stops_converting_after_aggregate_cap_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"author-source"
    png = _raster_bytes("PNG")
    figures = [
        Block(id=f"fig-{index}", type="figure", asset_key=f"plot-{index}.svg") for index in range(2)
    ]
    run, storage = _figure_run(
        figures[0],
        {"plot-0.svg": source, "plot-1.svg": source},
    )
    run.parsed.figures = figures
    calls: list[str] = []

    async def materialize(
        _data: bytes,
        source_name: str,
        _content_type: str | None = None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        calls.append(source_name)
        return FigureAssetPayload(
            png,
            "png",
            "image/png",
            16,
            12,
            source_size=len(source),
        )

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", materialize)
    monkeypatch.setattr(
        worker_pipeline,
        "MAX_TOTAL_FIGURE_MATERIALIZED_BYTES",
        len(source) + len(png),
    )

    output, _warnings, failures = await run._save_figures("revision-id")

    assert calls == ["plot-0.svg"]
    assert set(output) == {"fig-0"}
    assert len(storage.puts) == 1
    assert failures[0]["code"] == "figure_bytes_exceeded"


async def test_pdf_pipeline_applies_document_figure_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    png = _raster_bytes("PNG")
    blocks = [Block(id=f"fig-{index}", type="figure") for index in range(2)]
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.paper_id = "paper-id"
    run.parsed_pdf = SimpleNamespace(
        blocks=blocks,
        figure_images={block.id: png for block in blocks},
    )
    run.deps = SimpleNamespace(s3=storage)
    monkeypatch.setattr(worker_pipeline, "MAX_FIGURES_PER_DOCUMENT", 1)

    output, warnings, failures = await run._save_pdf_assets("revision-id")

    assert set(output) == {"fig-0"}
    assert len(storage.puts) == 1
    assert blocks[0].asset_key is not None
    assert blocks[1].asset_key is None
    assert warnings and "fig-1" in warnings[0]
    assert failures == [
        {
            "code": "figure_limit_exceeded",
            "figure_id": "fig-1",
            "source": "pdf",
        }
    ]


async def test_pdf_pipeline_applies_aggregate_figure_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    png = _raster_bytes("PNG")
    block = Block(id="fig-0", type="figure")
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.paper_id = "paper-id"
    run.parsed_pdf = SimpleNamespace(blocks=[block], figure_images={block.id: png})
    run.deps = SimpleNamespace(s3=storage)
    monkeypatch.setattr(
        worker_pipeline,
        "MAX_TOTAL_FIGURE_MATERIALIZED_BYTES",
        len(png) * 2 - 1,
    )

    output, _warnings, failures = await run._save_pdf_assets("revision-id")

    assert output == {}
    assert storage.puts == []
    assert block.asset_key is None
    assert failures[0]["code"] == "figure_bytes_exceeded"


async def test_thumbnail_upload_switches_pointer_and_retains_previous_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.paper_id = "paper-id"
    run.revision_id = "revision-new"
    run.deps = SimpleNamespace(s3=storage)
    figure = Block(
        id="fig-1",
        type="figure",
        asset_key="figures/paper-id/revision-id/fig-1.png",
    )
    old_key = "thumbnails/paper-id/revision-old/card.webp"
    paper = SimpleNamespace(thumbnail_key=old_key)
    uploaded_keys: list[str] = []

    async def render_thumbnail_isolated(
        _png: bytes, **_kwargs: Any
    ) -> figure_assets.ThumbnailPayload:
        return figure_assets.ThumbnailPayload(card=b"card", retina=b"retina")

    monkeypatch.setattr(
        worker_pipeline,
        "isolated_thumbnail_payload",
        render_thumbnail_isolated,
    )

    warnings = await run._make_thumbnail(
        paper,
        {figure.id: _raster_bytes("PNG")},
        [figure],
        uploaded_keys=uploaded_keys,
    )

    assert warnings == []
    expected_keys = [
        "thumbnails/paper-id/revision-new/card.webp",
        "thumbnails/paper-id/revision-new/card@2x.webp",
    ]
    assert uploaded_keys == expected_keys
    assert [key for _bucket, key, _body, _content_type in storage.puts] == expected_keys
    assert paper.thumbnail_key == expected_keys[0]
    assert old_key not in expected_keys
    assert storage.deletes == []


async def test_second_thumbnail_put_failure_cleans_new_keys_and_keeps_previous_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    storage.fail_put_at = 2
    run.paper_id = "paper-id"
    run.revision_id = "revision-new"
    run.deps = SimpleNamespace(s3=storage)
    figure = Block(id="fig-1", type="figure", asset_key="figure.png")
    old_key = "thumbnails/paper-id/revision-old/card.webp"
    paper = SimpleNamespace(thumbnail_key=old_key)

    async def render_thumbnail_isolated(
        _png: bytes, **_kwargs: Any
    ) -> figure_assets.ThumbnailPayload:
        return figure_assets.ThumbnailPayload(card=b"card", retina=b"retina")

    monkeypatch.setattr(
        worker_pipeline,
        "isolated_thumbnail_payload",
        render_thumbnail_isolated,
    )

    with pytest.raises(RuntimeError, match="put failed"):
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            await run._make_thumbnail(
                paper,
                {figure.id: _raster_bytes("PNG")},
                [figure],
                uploaded_keys=uploaded_keys,
            )

    expected_keys = [
        "thumbnails/paper-id/revision-new/card.webp",
        "thumbnails/paper-id/revision-new/card@2x.webp",
    ]
    assert paper.thumbnail_key == old_key
    assert storage.deletes == [("assets", expected_keys)]


async def test_commit_failure_restores_previous_thumbnail_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.paper_id = "paper-id"
    run.revision_id = "revision-new"
    run.deps = SimpleNamespace(s3=storage)
    figure = Block(id="fig-1", type="figure", asset_key="figure.png")
    old_key = "thumbnails/paper-id/revision-old/card.webp"
    paper = SimpleNamespace(thumbnail_key=old_key)
    error = RuntimeError("database commit failed")

    async def render_thumbnail_isolated(
        _png: bytes, **_kwargs: Any
    ) -> figure_assets.ThumbnailPayload:
        return figure_assets.ThumbnailPayload(card=b"card", retina=b"retina")

    monkeypatch.setattr(
        worker_pipeline,
        "isolated_thumbnail_payload",
        render_thumbnail_isolated,
    )

    with pytest.raises(RuntimeError) as caught:
        async with worker_pipeline._staged_revision_assets(
            storage, restore_thumbnail_on_failure=paper
        ) as uploaded_keys:
            await run._make_thumbnail(
                paper,
                {figure.id: _raster_bytes("PNG")},
                [figure],
                uploaded_keys=uploaded_keys,
            )
            raise error

    assert caught.value is error
    assert paper.thumbnail_key == old_key
    assert storage.deletes == [
        (
            "assets",
            [
                "thumbnails/paper-id/revision-new/card.webp",
                "thumbnails/paper-id/revision-new/card@2x.webp",
            ],
        )
    ]


def test_thumbnail_retina_sibling_parser_accepts_only_strict_current_keys() -> None:
    paper_id = "paper-id"
    valid = {
        "thumbnails/paper-id/card.webp": "thumbnails/paper-id/card@2x.webp",
        "thumbnails/paper-id/revision-id/card.webp": (
            "thumbnails/paper-id/revision-id/card@2x.webp"
        ),
    }
    invalid = [
        "thumbnails/other-paper/revision-id/card.webp",
        "thumbnails/paper-id/revision-id/card@2x.webp",
        "thumbnails/paper-id/revision-id/other.webp",
        "thumbnails/paper-id/../card.webp",
        "figures/paper-id/revision-id/card.webp",
    ]

    assert {
        key: StorageKeys.thumbnail_retina_sibling(key, paper_id=paper_id) for key in valid
    } == valid
    assert all(
        StorageKeys.thumbnail_retina_sibling(key, paper_id=paper_id) is None for key in invalid
    )


async def test_pipeline_rasterizes_safe_inline_html_figure_and_clears_raw() -> None:
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

    expected_key = StorageKeys.figure("paper-id", "revision-id", fig.id, "png")
    assert fig.asset_key == expected_key
    assert fig.raw is None
    assert output[fig.id].startswith(b"\x89PNG")
    assert [(key, content_type) for _bucket, key, _body, content_type in storage.puts] == [
        (expected_key, "image/png")
    ]
    assert warnings == []
    assert failures == []


@pytest.mark.parametrize(
    "svg_body",
    [
        '<rect width="10" height="10" style="fill:red"/>',
        '<style>rect{fill:red}</style><rect width="10" height="10"/>',
    ],
    ids=["style-attribute", "style-element"],
)
async def test_pipeline_rasterizes_safe_inline_css_and_drops_raw(svg_body: str) -> None:
    parsed = parse_arxiv_html(
        f"""<article class="ltx_document"><section class="ltx_section">
<h2 class="ltx_title">Result</h2><figure id="fig-inline-css" class="ltx_figure">
<svg width="10" height="10">{svg_body}</svg>
</figure></section></article>"""
    )
    fig = parsed.figures[0]
    assert fig.raw is not None
    run, storage = _figure_run(fig, {}, source_format="arxiv_html")

    output, warnings, failures = await run._save_figures("revision-id")

    assert fig.raw is None
    assert fig.asset_key is not None and fig.asset_key.endswith(".png")
    assert output[fig.id].startswith(b"\x89PNG")
    assert len(storage.puts) == 1
    assert warnings == []
    assert failures == []


async def test_pipeline_falls_back_to_img_when_inline_svg_is_rejected(
    figure_http: httpx.AsyncClient,
) -> None:
    parsed = parse_arxiv_html(
        """<article class="ltx_document"><section class="ltx_section">
<h2 class="ltx_title">Result</h2><figure id="fig-fallback" class="ltx_figure">
<svg width="10" height="10"><style>@import url(https://example.org/a.css);</style>
<rect width="10" height="10"/></svg>
<img class="ltx_graphics" src="good.png" alt="fallback"/>
</figure></section></article>"""
    )
    fig = parsed.figures[0]
    assert fig.raw is not None
    assert fig.asset_key == "good.png"
    run, storage = _figure_run(fig, {}, source_format="arxiv_html")
    run.ref = SimpleNamespace(versioned="2401.00001v2")
    run.deps = SimpleNamespace(
        s3=storage,
        http=figure_http,
        redis=None,
        settings=SimpleNamespace(alinea_arxiv_base_url="https://arxiv.org"),
    )

    output, warnings, failures = await run._save_figures("revision-id")

    assert fig.raw is None
    assert fig.asset_key is not None and fig.asset_key.endswith(".png")
    assert output[fig.id].startswith(b"\x89PNG")
    assert len(storage.puts) == 1
    assert warnings == []
    assert failures == []


async def test_pipeline_rejects_unsafe_inline_stylesheet_and_drops_raw() -> None:
    fig = Block(
        id="fig-unsafe-css",
        type="figure",
        raw="<svg><style>@import url(https://example.org/tracker.css);</style><rect/></svg>",
    )
    run, storage = _figure_run(fig, {}, source_format="arxiv_html")

    output, _warnings, failures = await run._save_figures("revision-id")

    assert fig.raw is None
    assert fig.asset_key is None
    assert output == {}
    assert storage.puts == []
    assert failures[0]["code"] == "unsafe_inline_figure"


async def test_pipeline_rejects_legacy_active_inline_html_without_upload() -> None:
    fig = Block(
        id="fig-active",
        type="figure",
        asset_key=None,
        raw='<svg><foreignObject><iframe srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;"></iframe></foreignObject></svg>',
    )
    run, storage = _figure_run(fig, {}, source_format="arxiv_html")

    output, warnings, failures = await run._save_figures("revision-id")

    assert fig.asset_key is None
    assert fig.raw is None
    assert output == {}
    assert storage.puts == []
    assert warnings and "fig-active" in warnings[0]
    assert failures == [
        {
            "code": "unsafe_inline_figure",
            "figure_id": "fig-active",
            "source": "arxiv_html",
        }
    ]
