# mypy: disable-error-code="arg-type,assignment,attr-defined,misc,union-attr,var-annotated"
"""Generic and hostile-input tests for paper figure materialization.

The adversarial tests intentionally inject structural fakes into private ``IngestRun`` seams.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import multiprocessing as mp
import tarfile
import threading
from collections.abc import AsyncIterator, Awaitable
from types import SimpleNamespace
from typing import Any

import fitz
import httpx
import pytest
from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.ingest import DocumentCompleteness
from alinea_core.parsing.html_parser import parse_arxiv_html
from alinea_core.parsing.pdf_parser import PARSER_VERSION as PDF_PARSER_VERSION
from alinea_core.parsing.pdf_parser import ParsedPdfDocument
from alinea_core.storage.s3 import S3ObjectTooLargeError, StorageKeys
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
    sanitize_svg_document,
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
from alinea_worker.source_candidates import SourceCandidate, parse_latex_candidate
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


async def _cancel_twice_and_require_isolated_reap(awaitable: Awaitable[Any]) -> None:
    existing_children = {child.pid for child in mp.active_children()}
    existing_tasks = asyncio.all_tasks()
    task = asyncio.create_task(awaitable)
    loop = asyncio.get_running_loop()
    start_deadline = loop.time() + 2.0
    while loop.time() < start_deadline:
        if {child.pid for child in mp.active_children()} - existing_children:
            break
        await asyncio.sleep(0.01)
    assert {child.pid for child in mp.active_children()} - existing_children

    promptly_reaped = False
    unexpected_pending: set[asyncio.Task[Any]] = set()
    second_cancel_delivered = False
    try:
        task.cancel("first cancellation")
        await asyncio.sleep(0)
        second_cancel_delivered = task.cancel("second cancellation")
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("first cancellation",)
        await asyncio.sleep(0.1)
        promptly_reaped = {child.pid for child in mp.active_children()} <= existing_children
        current = asyncio.current_task()
        unexpected_pending = {
            candidate
            for candidate in asyncio.all_tasks() - existing_tasks
            if candidate is not current and not candidate.done()
        }
    finally:
        cleanup_deadline = loop.time() + 2.0
        while loop.time() < cleanup_deadline:
            if {child.pid for child in mp.active_children()} <= existing_children:
                break
            await asyncio.sleep(0.02)

    assert second_cancel_delivered is True
    assert promptly_reaped is True
    assert unexpected_pending == set()


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


@pytest.mark.parametrize("path", ["figure", "thumbnail", "fetch"])
async def test_isolated_materialization_cancellation_reaps_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    if path == "figure":
        await _cancel_twice_and_require_isolated_reap(
            figure_assets.isolated_figure_asset_payload(
                SVG_BYTES,
                source_name="figure.svg",
                timeout_s=0.5,
                worker=_isolated_sleep_worker,
            )
        )
        return
    if path == "thumbnail":
        await _cancel_twice_and_require_isolated_reap(
            figure_assets.isolated_thumbnail_payload(
                _raster_bytes("PNG"),
                timeout_s=0.5,
                worker=_isolated_sleep_worker,
            )
        )
        return

    original_isolated = figure_assets.isolated_figure_asset_payload

    async def slow_isolated(
        data: bytes,
        *,
        source_name: str,
        content_type: str | None = None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        return await original_isolated(
            data,
            source_name=source_name,
            content_type=content_type,
            timeout_s=0.5,
            worker=_isolated_sleep_worker,
        )

    async def image_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=_raster_bytes("PNG"), headers={"content-type": "image/png"}
        )

    monkeypatch.setattr(figure_assets, "isolated_figure_asset_payload", slow_isolated)
    async with httpx.AsyncClient(transport=httpx.MockTransport(image_response)) as client:
        await _cancel_twice_and_require_isolated_reap(
            fetch_html_asset(
                client,
                base="https://assets.invalid",
                versioned="test-version",
                source="figure.png",
                total_timeout_s=2.0,
            )
        )


async def test_isolated_outer_timeout_remains_timeout_and_reaps_child() -> None:
    existing_children = {child.pid for child in mp.active_children()}

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await figure_assets.isolated_figure_asset_payload(
                SVG_BYTES,
                source_name="figure.svg",
                timeout_s=0.5,
                worker=_isolated_sleep_worker,
            )

    assert {child.pid for child in mp.active_children()} <= existing_children


async def test_external_cancel_wins_race_with_enclosing_isolated_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_children = {child.pid for child in mp.active_children()}
    original_reap = figure_assets._terminate_and_reap

    def delayed_reap(process: Any, *, failure_code: str) -> None:
        original_reap(process, failure_code=failure_code)
        import time

        time.sleep(0.1)

    monkeypatch.setattr(figure_assets, "_terminate_and_reap", delayed_reap)

    async def convert() -> FigureAssetPayload:
        async with asyncio.timeout(0.05):
            return await figure_assets.isolated_figure_asset_payload(
                SVG_BYTES,
                source_name="figure.svg",
                timeout_s=0.5,
                worker=_isolated_sleep_worker,
            )

    task = asyncio.create_task(convert())
    loop = asyncio.get_running_loop()
    start_deadline = loop.time() + 2.0
    while loop.time() < start_deadline:
        if {child.pid for child in mp.active_children()} - existing_children:
            break
        await asyncio.sleep(0.01)
    assert {child.pid for child in mp.active_children()} - existing_children
    task.cancel("external cancellation")

    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert caught.value.args == ("external cancellation",)
    assert {child.pid for child in mp.active_children()} <= existing_children


@pytest.mark.parametrize(
    ("supervisor_failure", "expected_warnings"),
    [
        (False, []),
        (
            True,
            [
                (
                    "isolated_worker_cancellation_cleanup_failed",
                    {
                        "code": "conversion_lifecycle",
                        "error_type": "FigureAssetError",
                    },
                )
            ],
        ),
    ],
    ids=["normal-cancellation", "lifecycle-failure"],
)
async def test_isolated_cancellation_logs_drained_supervisor_failure_only(
    monkeypatch: pytest.MonkeyPatch,
    supervisor_failure: bool,
    expected_warnings: list[tuple[str, dict[str, str]]],
) -> None:
    class RecordingLog:
        def __init__(self) -> None:
            self.warnings: list[tuple[str, dict[str, str]]] = []

        def warning(self, event: str, **fields: str) -> None:
            self.warnings.append((event, fields))

    started = threading.Event()

    def supervisor(
        *_args: Any,
        cancellation_event: threading.Event,
        **_kwargs: Any,
    ) -> object:
        started.set()
        cancellation_event.wait(timeout=1.0)
        if supervisor_failure:
            raise FigureAssetError(
                "conversion_lifecycle",
                "isolated worker could not be reaped",
            )
        return figure_assets._CANCELLED_ISOLATED_RESULT

    recording_log = RecordingLog()
    monkeypatch.setattr(figure_assets, "_run_isolated_worker", supervisor)
    monkeypatch.setattr(figure_assets, "log", recording_log, raising=False)
    task = asyncio.create_task(
        figure_assets.isolated_figure_asset_payload(
            SVG_BYTES,
            source_name="figure.svg",
        )
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel("external cancellation")

    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert caught.value.args == ("external cancellation",)
    assert recording_log.warnings == expected_warnings


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


# --------------------------------------------------------------------------- #
# Public SVG sanitizer (Task 29): reuses the existing validators without
# weakening them. Presentation SVGs go through this before ppt-master.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "source",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="/etc/passwd"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><p>x</p></foreignObject></svg>',
        b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
    ],
    ids=["script", "onload", "external-url", "path-traversal", "foreignObject", "doctype-entity"],
)
def test_sanitize_svg_document_rejects_active_or_external_content(source: bytes) -> None:
    with pytest.raises(FigureAssetError) as caught:
        sanitize_svg_document(source)
    assert caught.value.code == "unsafe_vector"


def test_sanitize_svg_document_rejects_oversized_xml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(figure_assets, "MAX_SVG_BYTES", 32)
    big = b'<svg xmlns="http://www.w3.org/2000/svg">' + b"<g/>" * 100 + b"</svg>"
    with pytest.raises(FigureAssetError) as caught:
        sanitize_svg_document(big)
    assert caught.value.code == "asset_too_large"


def test_sanitize_svg_document_returns_canonical_passive_svg() -> None:
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg"'
        ' xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"'
        ' viewBox="0 0 1280 720" width="1280" height="720" data-author="untrusted">'
        '<g inkscape:label="Plot"><rect width="1280" height="720" fill="#0b1021"/>'
        '<text x="80" y="200" fill="#ffffff">スライド</text></g>'
        "</svg>"
    ).encode()

    out = sanitize_svg_document(source)

    # Canonical bytes are returned; foreign/data metadata is stripped but the
    # legitimate rendering nodes survive (no weakening of the accept path).
    assert b"data-author" not in out
    assert b"inkscape" not in out
    assert b"<rect" in out
    assert "スライド".encode() in out
    # Re-sanitizing the output is idempotent (still safe SVG).
    assert sanitize_svg_document(out)


def test_sanitize_svg_document_is_the_render_gate() -> None:
    # The private render path must delegate to the public sanitizer (same object)
    # so figure SVG validation and presentation SVG validation cannot diverge.
    assert figure_assets._validate_svg_document is sanitize_svg_document


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
    "transform",
    [
        "translate(1e999 0)",
        "matrix(1 0 0 1 0)",
        "translate(1 2 3)",
        "scale(1 2 3)",
        "rotate(1 2)",
        "skewX(1 2)",
        "skewY(1 2)",
    ],
    ids=[
        "non-finite",
        "matrix-arity",
        "translate-arity",
        "scale-arity",
        "rotate-arity",
        "skew-x-arity",
        "skew-y-arity",
    ],
)
def test_svg_transform_requires_finite_arguments_and_function_arity_before_render(
    monkeypatch: pytest.MonkeyPatch,
    transform: str,
) -> None:
    source = f"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<g transform="{transform}"><rect width="5" height="5"/></g></svg>""".encode()
    rendered: list[bytes] = []

    def capture_render(data: bytes, _filetype: str) -> FigureAssetPayload:
        rendered.append(data)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", capture_render)

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "unsafe_vector"
    assert rendered == []


@pytest.mark.parametrize(
    "attribute",
    [
        'stroke-width="1e999"',
        'style="opacity:1e999"',
    ],
    ids=["presentation-attribute", "style-declaration"],
)
def test_svg_css_rejects_non_finite_numeric_tokens_before_render(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
) -> None:
    source = f"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<rect width="5" height="5" {attribute}/></svg>""".encode()
    rendered: list[bytes] = []

    def capture_render(data: bytes, _filetype: str) -> FigureAssetPayload:
        rendered.append(data)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", capture_render)

    with pytest.raises(FigureAssetError) as caught:
        figure_asset_payload(source, source_name="figure.svg")

    assert caught.value.code == "unsafe_vector"
    assert rendered == []


def test_svg_css_numeric_lexer_ignores_fragments_colors_and_identifier_digits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<defs><linearGradient id="plot-1e999"><stop offset="0" stop-color="#1e9999"/></linearGradient></defs>
<rect id="series1e999" width="5" height="5" fill="url(#plot-1e999)"
 stroke="#1e9999" stroke-width="1.5px" style="opacity:.5;transform:translate(2px, 1px)"/>
</svg>"""
    rendered: list[bytes] = []

    def capture_render(data: bytes, _filetype: str) -> FigureAssetPayload:
        rendered.append(data)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", capture_render)

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert len(rendered) == 1
    assert b"plot-1e999" in rendered[0]
    assert b"#1e9999" in rendered[0]
    assert b"series1e999" in rendered[0]


@pytest.mark.parametrize(
    ("style_markup", "kept"),
    [
        (
            '<rect style="font-variation-settings:normal;fill:red" width="5" height="5"/>',
            b"fill:red",
        ),
        (
            """<style>.plot{font-variation-settings:normal;-inkscape-font-specification:'Sans';
fill:#1e9999;stroke-width:1px}</style><rect class="plot" width="5" height="5"/>""",
            b"stroke-width:1px",
        ),
        (
            '<rect style="unknown-resource:url(https://resource.invalid/a);fill:navy" width="5" height="5"/>',
            b"fill:navy",
        ),
    ],
    ids=["common-unknown-attribute", "recent-inkscape-stylesheet", "unknown-external-url"],
)
def test_svg_css_rebuild_drops_unknown_inert_declarations_and_keeps_safe_ones(
    monkeypatch: pytest.MonkeyPatch,
    style_markup: str,
    kept: bytes,
) -> None:
    source = f"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
{style_markup}</svg>""".encode()
    rendered: list[bytes] = []

    def capture_render(data: bytes, _filetype: str) -> FigureAssetPayload:
        rendered.append(data)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", capture_render)

    payload = figure_asset_payload(source, source_name="figure.svg")

    assert payload.ext == "png"
    assert len(rendered) == 1
    assert kept in rendered[0]
    assert b"font-variation-settings" not in rendered[0]
    assert b"inkscape-font-specification" not in rendered[0]
    assert b"unknown-resource" not in rendered[0]
    assert b"resource.invalid" not in rendered[0]


@pytest.mark.parametrize(
    "style",
    [
        "unknown-property:javascript:alert(1);fill:red",
        "behavior:url(#internal);fill:red",
    ],
)
def test_svg_css_dangerous_precheck_runs_before_unknown_declarations_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
    style: str,
) -> None:
    source = f"""<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
<rect style="{style}" width="5" height="5"/></svg>""".encode()
    rendered: list[bytes] = []

    def capture_render(data: bytes, _filetype: str) -> FigureAssetPayload:
        rendered.append(data)
        return validate_image_payload(_raster_bytes("PNG"))

    monkeypatch.setattr(figure_assets, "_render_document", capture_render)

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


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (408, "asset_fetch_timeout"),
        (429, "rate_limited"),
        (500, "upstream_5xx"),
        (503, "upstream_5xx"),
        (404, "asset_http_status"),
    ],
)
async def test_fetch_html_asset_classifies_retryable_http_status(
    status_code: int,
    expected_code: str,
) -> None:
    async def figure(_request: Any) -> Response:
        return Response("unavailable", status_code=status_code)

    app = Starlette(routes=[Route("/html/2401.00001v2/figure.png", figure)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://arxiv.org"
    ) as client:
        with pytest.raises(FigureAssetError) as caught:
            await fetch_html_asset(
                client,
                base="https://arxiv.org",
                versioned="2401.00001v2",
                source="figure.png",
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


class _AbsentRevisionSession:
    async def __aenter__(self) -> _AbsentRevisionSession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, _model: Any, _key: Any) -> None:
        return None


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


@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["success", "failure"])
async def test_staged_cleanup_finishes_through_repeated_cancellation(
    cleanup_fails: bool,
) -> None:
    class BlockingStorage:
        assets_bucket = "assets"

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = False
            self.cleanup_task: asyncio.Task[Any] | None = None

        async def delete_many(self, _bucket: str, _keys: Any) -> None:
            self.cleanup_task = asyncio.current_task()
            self.started.set()
            await self.release.wait()
            self.finished = True
            if cleanup_fails:
                raise RuntimeError("cleanup failed")

    storage = BlockingStorage()
    new_key = "figures/test/revision/one.png"

    async def publish() -> None:
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            uploaded_keys.append(new_key)
            await asyncio.Event().wait()

    task = asyncio.create_task(publish())
    await asyncio.sleep(0)
    task.cancel("first cancellation")
    await storage.started.wait()
    task.cancel("second cancellation")
    await asyncio.sleep(0)
    still_waiting_after_second_cancel = not task.done()
    storage.release.set()

    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args == ("first cancellation",)
    if storage.cleanup_task is not None:
        await asyncio.gather(storage.cleanup_task, return_exceptions=True)

    assert still_waiting_after_second_cancel is True
    assert storage.finished is True
    assert storage.cleanup_task is not None and storage.cleanup_task.done()


async def test_staged_cleanup_deadline_cancels_and_drains_hanging_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HangingStorage:
        assets_bucket = "assets"

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = False
            self.cleanup_task: asyncio.Task[Any] | None = None

        async def delete_many(self, _bucket: str, _keys: Any) -> None:
            self.cleanup_task = asyncio.current_task()
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    monkeypatch.setattr(
        worker_pipeline,
        "REVISION_ASSET_CLEANUP_TIMEOUT_S",
        0.05,
        raising=False,
    )
    storage = HangingStorage()
    original = RuntimeError("publication failed")

    async def publish() -> None:
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            uploaded_keys.append("figures/test/revision/one.png")
            raise original

    task = asyncio.create_task(publish())
    await storage.started.wait()
    await asyncio.sleep(0.1)
    completed_within_deadline = task.done()
    if not task.done():
        task.cancel()
    with pytest.raises(RuntimeError) as caught:
        await task
    assert caught.value is original

    if storage.cleanup_task is not None and not storage.cleanup_task.done():
        storage.cleanup_task.cancel()
        await asyncio.gather(storage.cleanup_task, return_exceptions=True)

    assert completed_within_deadline is True
    assert storage.cancelled is True
    assert storage.cleanup_task is not None and storage.cleanup_task.done()


@pytest.mark.parametrize(
    ("terminal_state", "expected_error_type"),
    [
        ("error", "CleanupTerminalError"),
        ("cancelled", "TimeoutError"),
        ("success", "TimeoutError"),
    ],
)
async def test_staged_cleanup_grace_preserves_only_real_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
    expected_error_type: str,
) -> None:
    class CleanupTerminalError(RuntimeError):
        pass

    class TerminalStorage:
        assets_bucket = "assets"

        async def delete_many(self, _bucket: str, _keys: Any) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                if terminal_state == "error":
                    raise CleanupTerminalError("delete failed during cancellation") from exc
                if terminal_state == "success":
                    return
                raise

    class RecordingLog:
        def __init__(self) -> None:
            self.warnings: list[tuple[str, dict[str, Any]]] = []

        def warning(self, event: str, **fields: Any) -> None:
            self.warnings.append((event, fields))

    monkeypatch.setattr(worker_pipeline, "REVISION_ASSET_CLEANUP_TIMEOUT_S", 0.01)
    monkeypatch.setattr(worker_pipeline, "REVISION_ASSET_CLEANUP_CANCEL_GRACE_S", 0.05)
    recording_log = RecordingLog()
    monkeypatch.setattr(worker_pipeline, "log", recording_log)
    original = RuntimeError("publication failed")

    with pytest.raises(RuntimeError) as caught:
        async with worker_pipeline._staged_revision_assets(TerminalStorage()) as uploaded_keys:
            uploaded_keys.append("figures/test/revision/one.png")
            raise original

    assert caught.value is original
    assert (
        "revision_asset_cleanup_failed",
        {"error_type": expected_error_type, "key_count": 1},
    ) in recording_log.warnings


async def test_external_cancel_wins_race_with_staged_cleanup_timeout() -> None:
    class SlowStorage:
        assets_bucket = "assets"

        def __init__(self) -> None:
            self.cleanup_started = asyncio.Event()
            self.cleanup_finished = False

        async def delete_many(self, _bucket: str, _keys: Any) -> None:
            self.cleanup_started.set()
            await asyncio.sleep(0.1)
            self.cleanup_finished = True

    storage = SlowStorage()
    entered = asyncio.Event()

    async def publish() -> None:
        async with asyncio.timeout(0.05):
            async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
                uploaded_keys.append("figures/test/revision/one.png")
                entered.set()
                await asyncio.Event().wait()

    task = asyncio.create_task(publish())
    await entered.wait()
    task.cancel("external cancellation")

    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert caught.value.args == ("external cancellation",)
    assert storage.cleanup_finished is True


async def test_staged_cleanup_suppression_is_bounded_tracked_and_retrieved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CancellationSuppressingStorage:
        assets_bucket = "assets"

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.cleanup_task: asyncio.Task[Any] | None = None
            self.suppressed_cancellations = 0

        async def delete_many(self, _bucket: str, _keys: Any) -> None:
            self.cleanup_task = asyncio.current_task()
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.suppressed_cancellations += 1

    class RecordingLog:
        def __init__(self) -> None:
            self.warnings: list[tuple[str, dict[str, Any]]] = []

        def warning(self, event: str, **fields: Any) -> None:
            self.warnings.append((event, fields))

    monkeypatch.setattr(worker_pipeline, "REVISION_ASSET_CLEANUP_TIMEOUT_S", 0.02)
    monkeypatch.setattr(
        worker_pipeline,
        "REVISION_ASSET_CLEANUP_CANCEL_GRACE_S",
        0.03,
        raising=False,
    )
    recording_log = RecordingLog()
    monkeypatch.setattr(worker_pipeline, "log", recording_log)
    storage = CancellationSuppressingStorage()
    original = RuntimeError("publication failed")

    async def publish() -> None:
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            uploaded_keys.append("figures/test/revision/one.png")
            raise original

    task = asyncio.create_task(publish())
    await storage.started.wait()
    completed_within_bound = True
    caught_error: BaseException | None = None
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=0.3)
    except RuntimeError as exc:
        caught_error = exc
    except TimeoutError:
        completed_within_bound = False

    tracked_while_pending = storage.cleanup_task in getattr(
        worker_pipeline,
        "_BACKGROUND_REVISION_CLEANUPS",
        set(),
    )
    storage.release.set()
    if storage.cleanup_task is not None:
        await asyncio.gather(storage.cleanup_task, return_exceptions=True)
    if not task.done():
        await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert completed_within_bound is True
    assert caught_error is original
    assert storage.suppressed_cancellations >= 1
    assert tracked_while_pending is True
    assert storage.cleanup_task not in getattr(
        worker_pipeline,
        "_BACKGROUND_REVISION_CLEANUPS",
        set(),
    )
    assert (
        "revision_asset_cleanup_failed",
        {"error_type": "TimeoutError", "key_count": 1},
    ) in recording_log.warnings


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


@pytest.mark.parametrize(
    "failure_code",
    [
        "unsafe_inline_figure",
        "missing_asset_key",
        "asset_not_found",
        "asset_ambiguous",
        "image_invalid",
        "image_dimensions_exceeded",
        "figure_limit_exceeded",
        "figure_bytes_exceeded",
        "asset_too_large",
        "conversion_oversize",
    ],
)
def test_deterministic_nested_figure_failures_remain_terminal(
    failure_code: str,
) -> None:
    failures = [
        {
            "format": "latex",
            "code": "figure_asset_unresolved",
            "figure_asset_failures": [{"code": failure_code}],
        }
    ]

    assert worker_pipeline._candidate_failure_code(failures) == "figure_asset_unresolved"


@pytest.mark.parametrize(
    "failure_code",
    [
        "conversion_crashed",
        "conversion_lifecycle",
        "conversion_timeout",
        "materialization_timeout",
        "figure_asset_error",
        "renderer_crashed",
        "renderer_lifecycle",
        "renderer_timeout",
    ],
)
def test_operational_nested_figure_failures_are_retryable(failure_code: str) -> None:
    failures = [
        {
            "format": "latex",
            "code": "figure_asset_unresolved",
            "figure_asset_failures": [{"code": failure_code}],
        }
    ]

    assert worker_pipeline._candidate_failure_code(failures) == failure_code


@pytest.mark.parametrize(
    "reconciliation_error",
    [RuntimeError("database unavailable"), asyncio.CancelledError("reconciliation cancelled")],
    ids=["failed", "cancelled"],
)
async def test_ambiguous_commit_reconciliation_failure_preserves_assets_for_orphan_gc(
    reconciliation_error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingLog:
        def __init__(self) -> None:
            self.warnings: list[tuple[str, dict[str, Any]]] = []

        def warning(self, event: str, **fields: Any) -> None:
            self.warnings.append((event, fields))

    async def fail_reconciliation() -> bool:
        raise reconciliation_error

    storage = _RecordingStorage()
    paper = SimpleNamespace(thumbnail_key="thumbnails/paper/revision-new/card.webp")
    commit_state = worker_pipeline._RevisionCommitState("revision-new", attempted=True)
    recording_log = RecordingLog()
    monkeypatch.setattr(worker_pipeline, "log", recording_log)
    original = RuntimeError("commit outcome unknown")

    with pytest.raises(RuntimeError) as caught:
        async with worker_pipeline._staged_revision_assets(
            storage,
            restore_thumbnail_on_failure=paper,
            commit_state=commit_state,
            reconcile_commit=fail_reconciliation,
        ) as uploaded_keys:
            uploaded_keys.extend(
                [
                    "figures/paper/revision-new/one.png",
                    "thumbnails/paper/revision-new/card.webp",
                    "thumbnails/paper/revision-new/card@2x.webp",
                ]
            )
            raise original

    assert caught.value is original
    assert storage.deletes == []
    assert paper.thumbnail_key == "thumbnails/paper/revision-new/card.webp"
    assert (
        "revision_asset_orphan_gc_required",
        {
            "revision_id": "revision-new",
            "error_type": type(reconciliation_error).__name__,
            "key_count": 3,
        },
    ) in recording_log.warnings


async def test_ambiguous_commit_negative_reconciliation_preserves_assets_for_orphan_gc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingLog:
        def __init__(self) -> None:
            self.warnings: list[tuple[str, dict[str, Any]]] = []

        def warning(self, event: str, **fields: Any) -> None:
            self.warnings.append((event, fields))

        def info(self, _event: str, **_fields: Any) -> None:
            return None

    async def revision_not_visible() -> bool:
        return False

    storage = _RecordingStorage()
    new_thumbnail = "thumbnails/paper/revision-new/card.webp"
    paper = SimpleNamespace(thumbnail_key=new_thumbnail)
    commit_state = worker_pipeline._RevisionCommitState("revision-new", attempted=True)
    recording_log = RecordingLog()
    monkeypatch.setattr(worker_pipeline, "log", recording_log)
    original = RuntimeError("commit outcome unknown")
    keys = [
        "figures/paper/revision-new/one.png",
        new_thumbnail,
        "thumbnails/paper/revision-new/card@2x.webp",
    ]

    with pytest.raises(RuntimeError) as caught:
        async with worker_pipeline._staged_revision_assets(
            storage,
            restore_thumbnail_on_failure=paper,
            commit_state=commit_state,
            reconcile_commit=revision_not_visible,
        ) as uploaded_keys:
            uploaded_keys.extend(keys)
            raise original

    assert caught.value is original
    assert storage.deletes == []
    assert paper.thumbnail_key == new_thumbnail
    assert (
        "revision_asset_orphan_gc_required",
        {
            "revision_id": "revision-new",
            "error_type": "RevisionNotVisible",
            "key_count": 3,
        },
    ) in recording_log.warnings


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
    run._candidate_storage_key = "sources/paper-id/v1/arxiv.html"
    run._candidate_sha256 = "1" * 64
    run._candidate_parsed_content_sha256 = "2" * 64
    run.revision_id = None
    run.content = None
    run.session = _StructuringSession(
        paper,
        error if failure_phase == "commit" else None,
    )
    run.deps = SimpleNamespace(s3=storage, session_factory=_AbsentRevisionSession)
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
    if failure_phase == "index":
        assert paper.thumbnail_key == old_thumbnail
        assert storage.deletes == [("assets", [figure_key])]
    else:
        assert paper.thumbnail_key == "thumbnails/paper-id/revision-new/card.webp"
        assert storage.deletes == []


def _figure_run(
    fig: Block,
    binary_files: dict[str, bytes],
    *,
    source_format: str = "latex",
) -> tuple[IngestRun, _RecordingStorage]:
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.parsed = SimpleNamespace(figures=[fig], blocks=[fig])
    run.paper_id = "paper-id"
    run.source_format = source_format
    run.latex_binary_files = binary_files
    run.latex_main_tex_name = "paper/main.tex"
    run.latex_graphicspaths = ["../images/"]
    run.ref = None
    run.deps = SimpleNamespace(s3=storage, http=None, redis=None)
    return run, storage


def _pdf_figure_run(
    figure_images: dict[str, bytes],
) -> tuple[IngestRun, _RecordingStorage, list[Block]]:
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    blocks = [Block(id=block_id, type="figure") for block_id in figure_images]
    run.paper_id = "paper-id"
    run.parsed_pdf = SimpleNamespace(blocks=blocks, figure_images=figure_images)
    run.deps = SimpleNamespace(s3=storage)
    return run, storage, blocks


async def test_latex_image_backed_table_is_persisted_as_a_table_asset() -> None:
    png = _raster_bytes("PNG")
    table = Block(id="table-1", type="table", asset_key="runtime-table.png")
    run, storage = _figure_run(table, {"images/runtime-table.png": png})
    uploaded_keys: list[str] = []

    saved, warnings, failures = await run._save_figures(
        "revision-id",
        uploaded_keys=uploaded_keys,
        deadline=worker_pipeline.MaterializationDeadline.start(timeout_s=30.0),
    )

    expected_key = StorageKeys.figure("paper-id", "revision-id", table.id, "png")
    assert warnings == []
    assert failures == []
    assert saved == {table.id: png}
    assert table.asset_key == expected_key
    assert uploaded_keys == [expected_key]
    assert storage.puts == [("assets", expected_key, png, "image/png")]


def _two_paragraph_blocks() -> list[Block]:
    return [
        Block(
            id=f"p-{index}",
            type="paragraph",
            inlines=[{"t": "text", "v": f"Complete synthetic paragraph {index}."}],
        )
        for index in range(2)
    ]


async def test_latex_caption_only_table_degrades_without_fallback() -> None:
    """tblr など未対応環境で raw/structured grid が得られない caption-only な表も、

    図と同じルールでブロック単位に縮退し、候補全体は不採用にしない
    (P3: 黙って壊れない。§2.4 のスコープは figure/table 両方)。
    """
    table = Block(id="tbl-1", type="table")
    content = DocumentContent(
        quality_level="A",
        sections=[Section(id="sec-1", blocks=[*_two_paragraph_blocks(), table])],
    )
    candidate = SourceCandidate(
        source_format="latex",
        content=content,
        parsed=SimpleNamespace(),
        report=DocumentCompleteness(True, None, 0, 70, 2, 0),
        source_bytes=b"tex synthetic",
        diagnostics=[],
    )
    run = object.__new__(IngestRun)

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=worker_pipeline.MaterializationDeadline.start(timeout_s=30.0),
    )

    assert candidate.report.accepted is True
    assert candidate.report.code == "figure_assets_degraded"
    assert candidate.report.unresolved_figures == 1
    assert candidate.materialized_figures == {}
    assert candidate.figure_asset_failures == [
        {"code": "missing_asset_key", "figure_id": "tbl-1", "source": "latex"}
    ]


async def test_latex_retryable_table_asset_failure_rejects_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """表アセットの失敗でも一時的(リトライで直る可能性がある)なものは、図と同じく

    ブロック単位の縮退はせず候補全体を不採用にする(§2.4)。
    """
    table = Block(id="tbl-1", type="table", asset_key="table-panel.png")
    content = DocumentContent(
        quality_level="A",
        sections=[Section(id="sec-1", blocks=[*_two_paragraph_blocks(), table])],
    )
    candidate = SourceCandidate(
        source_format="latex",
        content=content,
        parsed=SimpleNamespace(),
        report=DocumentCompleteness(True, None, 0, 70, 2, 0),
        source_bytes=b"tex synthetic",
        diagnostics=[],
        latex_binary_files={"table-panel.png": _raster_bytes("PNG")},
    )

    async def fail_materialize(*_args: Any, **_kwargs: Any) -> FigureAssetPayload:
        raise FigureAssetError("asset_fetch_timeout", "synthetic transient table asset failure")

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", fail_materialize)
    run = object.__new__(IngestRun)

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=worker_pipeline.MaterializationDeadline.start(timeout_s=30.0),
    )

    assert candidate.report.accepted is False
    assert candidate.report.code == "figure_asset_unresolved"
    assert candidate.materialized_figures == {}
    assert candidate.figure_asset_failures == [
        {"code": "asset_fetch_timeout", "figure_id": "tbl-1", "source": "latex"}
    ]


async def test_latex_mixed_figure_and_table_failures_both_degrade() -> None:
    """恒久的に解決できない図と表が混在しても、両方がブロック単位で縮退し

    候補全体は不採用にしない(P3: 黙って壊れない。図・表の縮退は同一ルール)。
    """
    figure = Block(id="fig-1", type="figure", asset_key="missing-figure.png")
    table = Block(id="tbl-1", type="table")
    content = DocumentContent(
        quality_level="A",
        sections=[Section(id="sec-1", blocks=[*_two_paragraph_blocks(), figure, table])],
    )
    candidate = SourceCandidate(
        source_format="latex",
        content=content,
        parsed=SimpleNamespace(),
        report=DocumentCompleteness(True, None, 0, 70, 2, 0),
        source_bytes=b"tex synthetic",
        diagnostics=[],
    )
    run = object.__new__(IngestRun)

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=worker_pipeline.MaterializationDeadline.start(timeout_s=30.0),
    )

    assert candidate.report.accepted is True
    assert candidate.report.code == "figure_assets_degraded"
    assert candidate.report.unresolved_figures == 2
    assert candidate.materialized_figures == {}
    assert candidate.figure_asset_failures == [
        {"code": "asset_not_found", "figure_id": "fig-1", "source": "latex"},
        {"code": "missing_asset_key", "figure_id": "tbl-1", "source": "latex"},
    ]


async def test_figure_count_over_limit_degrades_block_wise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document with more figures than the per-document budget is accepted:

    the first MAX_FIGURES_PER_DOCUMENT are materialized and the remainder are
    marked ``figure_deferred`` so they can be loaded on demand (P3 — degrade,
    don't fail closed).
    """
    monkeypatch.setattr(worker_pipeline, "MAX_FIGURES_PER_DOCUMENT", 2)
    png = _raster_bytes("PNG")
    figures = [
        Block(id=f"fig-{index}", type="figure", asset_key=f"fig-{index}.png")
        for index in range(4)
    ]
    content = DocumentContent(
        quality_level="A",
        sections=[Section(id="sec-1", blocks=[*_two_paragraph_blocks(), *figures])],
    )
    candidate = SourceCandidate(
        source_format="latex",
        content=content,
        parsed=SimpleNamespace(),
        report=DocumentCompleteness(True, None, 0, 70, 2, 0),
        source_bytes=b"tex synthetic",
        diagnostics=[],
        latex_binary_files={f"fig-{index}.png": png for index in range(4)},
    )
    run = object.__new__(IngestRun)

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=worker_pipeline.MaterializationDeadline.start(timeout_s=30.0),
    )

    assert candidate.report.accepted is True
    assert candidate.report.code == "figure_assets_degraded"
    # First two figures materialized; the remaining two are deferred.
    assert set(candidate.materialized_figures) == {"fig-0", "fig-1"}
    assert candidate.figure_asset_failures == [
        {"code": "figure_deferred", "figure_id": "fig-2", "source": "latex"},
        {"code": "figure_deferred", "figure_id": "fig-3", "source": "latex"},
    ]
    assert run._candidate_deferred_figures == {
        "fig-2": "fig-2.png",
        "fig-3": "fig-3.png",
    }


async def test_pdf_candidate_rejects_orphan_extracted_asset_before_persistence() -> None:
    content = DocumentContent(
        quality_level="B",
        sections=[
            Section(
                id="sec-1",
                blocks=[
                    Block(
                        id="p-1",
                        type="paragraph",
                        inlines=[{"t": "text", "v": "First complete synthetic paragraph."}],
                    ),
                    Block(
                        id="p-2",
                        type="paragraph",
                        inlines=[{"t": "text", "v": "Second complete synthetic paragraph."}],
                    ),
                ],
            )
        ],
    )
    parsed = ParsedPdfDocument(
        sections=content.sections,
        figure_images={"orphan-image": _raster_bytes("PNG")},
    )
    candidate = SourceCandidate(
        source_format="pdf",
        content=content,
        parsed=parsed,
        report=DocumentCompleteness(True, None, 0, 70, 2, 0),
        source_bytes=b"%PDF synthetic",
        diagnostics=[],
    )
    run = object.__new__(IngestRun)
    run._pdf_text = ""
    run._pdf_bytes = b"%PDF synthetic"

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=worker_pipeline.MaterializationDeadline.start(timeout_s=30.0),
    )

    assert candidate.report.accepted is False
    assert candidate.report.code == "figure_asset_unresolved"
    assert candidate.figure_asset_failures == [
        {
            "code": "missing_figure_block",
            "figure_id": "orphan-image",
            "source": "pdf",
        }
    ]
    assert candidate.materialized_figures == {}


async def test_existing_revision_repairs_oversized_asset_with_bounded_reads() -> None:
    paper_id = "00000000-0000-0000-0000-000000000001"
    revision_id = "00000000-0000-0000-0000-000000000002"
    source_key = StorageKeys.original_pdf(paper_id, "v1")
    source_sha256 = "1" * 64
    png = _raster_bytes("PNG")
    payload = FigureAssetPayload(png, "png", "image/png", 16, 12, len(png))
    candidate_block = Block(id="fig-1", type="figure", asset_key="source.png")
    candidate_content = DocumentContent(
        quality_level="B",
        sections=[Section(id="sec-1", blocks=[candidate_block])],
    )
    candidate = SourceCandidate(
        source_format="pdf",
        content=candidate_content,
        parsed=SimpleNamespace(),
        report=DocumentCompleteness(True, None, 0, 70, 2, 1),
        source_bytes=b"%PDF synthetic",
        diagnostics=[],
        materialized_figures={candidate_block.id: payload},
        figure_materialization_validated=True,
    )
    canonical_key = StorageKeys.figure(paper_id, revision_id, candidate_block.id, payload.ext)
    revision_block = Block(id=candidate_block.id, type="figure", asset_key=canonical_key)
    revision_content = DocumentContent(
        quality_level="B",
        sections=[Section(id="sec-1", blocks=[revision_block])],
    ).model_dump()
    revision = worker_pipeline.DocumentRevision(
        id=revision_id,
        paper_id=paper_id,
        source_version="v1",
        parser_version=PDF_PARSER_VERSION,
        quality_level="B",
        source_format="pdf",
        content=revision_content,
        stats={
            "selected_source": {"storage_key": source_key, "sha256": source_sha256},
            "parsed_content_sha256": worker_pipeline._canonical_content_sha256(
                candidate_content.model_dump()
            ),
            "revision_content_sha256": worker_pipeline._canonical_content_sha256(revision_content),
            "figure_materialization_version": worker_pipeline.FIGURE_MATERIALIZATION_VERSION,
            "figure_asset_manifest": [
                {
                    "block_id": candidate_block.id,
                    "key": canonical_key,
                    "sha256": hashlib.sha256(png).hexdigest(),
                    "byte_size": len(png),
                }
            ],
        },
    )

    class OversizedStorage:
        assets_bucket = "assets"

        def __init__(self) -> None:
            self.objects = {canonical_key: png + b"oversized corruption"}
            self.read_limits: list[int] = []
            self.puts: list[bytes] = []

        async def get(self, _bucket: str, _key: str) -> bytes:
            raise AssertionError("integrity verification must never use unbounded get")

        async def get_bounded(self, _bucket: str, key: str, *, max_bytes: int) -> bytes:
            self.read_limits.append(max_bytes)
            value = self.objects[key]
            if len(value) > max_bytes:
                raise S3ObjectTooLargeError(max_bytes=max_bytes)
            return value

        async def put(
            self,
            _bucket: str,
            key: str,
            body: bytes,
            *,
            content_type: str = "application/octet-stream",
        ) -> None:
            del content_type
            self.puts.append(body)
            self.objects[key] = body

    storage = OversizedStorage()
    run = object.__new__(IngestRun)
    run._candidate_storage_key = source_key
    run._candidate_sha256 = source_sha256
    run.deps = SimpleNamespace(s3=storage)

    await run._verify_or_repair_existing_revision_assets(revision, candidate)

    assert storage.read_limits == [len(png), len(png)]
    assert storage.puts == [png]
    assert storage.objects[canonical_key] == png


async def test_pdf_candidate_rejects_extracted_asset_bound_to_paragraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paragraph = Block(
        id="p-image",
        type="paragraph",
        inlines=[{"t": "text", "v": "A complete synthetic paragraph."}],
    )
    content = DocumentContent(
        quality_level="B",
        sections=[
            Section(
                id="sec-1",
                blocks=[
                    paragraph,
                    Block(
                        id="p-2",
                        type="paragraph",
                        inlines=[{"t": "text", "v": "Another complete paragraph."}],
                    ),
                ],
            )
        ],
    )
    parsed = ParsedPdfDocument(
        sections=content.sections,
        figure_images={paragraph.id: _raster_bytes("PNG")},
    )
    candidate = SourceCandidate(
        source_format="pdf",
        content=content,
        parsed=parsed,
        report=DocumentCompleteness(True, None, 0, 70, 2, 0),
        source_bytes=b"%PDF synthetic",
        diagnostics=[],
    )
    materialized: list[str] = []

    async def unexpected_materialization(*_args: Any, **_kwargs: Any) -> FigureAssetPayload:
        materialized.append("called")
        raise AssertionError("invalid block type must be rejected before conversion")

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", unexpected_materialization)
    run = object.__new__(IngestRun)
    run._pdf_text = ""
    run._pdf_bytes = b"%PDF synthetic"

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=worker_pipeline.MaterializationDeadline.start(timeout_s=30.0),
    )

    assert materialized == []
    assert candidate.report.code == "figure_asset_unresolved"
    assert candidate.materialized_figures == {}
    assert candidate.figure_asset_failures == [
        {
            "code": "invalid_figure_block_type",
            "figure_id": paragraph.id,
            "source": "pdf",
        }
    ]


async def test_save_pdf_assets_rejects_extracted_asset_bound_to_paragraph() -> None:
    png = _raster_bytes("PNG")
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    paragraph = Block(
        id="p-image",
        type="paragraph",
        inlines=[{"t": "text", "v": "A paragraph cannot own a display image."}],
    )
    run.paper_id = "paper-id"
    run.parsed_pdf = SimpleNamespace(blocks=[paragraph], figure_images={paragraph.id: png})
    run.deps = SimpleNamespace(s3=storage)

    output, warnings, failures = await run._save_pdf_assets("revision-id")

    assert output == {}
    assert storage.puts == []
    assert failures == [
        {
            "code": "invalid_figure_block_type",
            "figure_id": paragraph.id,
            "source": "pdf",
        }
    ]
    assert warnings == [
        f"図/表アセットの保存に失敗(続行): {paragraph.id} [invalid_figure_block_type]"
    ]


async def test_over_limit_candidate_is_rejected_before_any_asset_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paragraphs = [
        Block(
            id=f"p-{index}",
            type="paragraph",
            inlines=[{"t": "text", "v": f"Complete synthetic paragraph {index}."}],
        )
        for index in range(2)
    ]
    asset_blocks = [
        Block(id=f"asset-{index:03d}", type="equation", latex="x")
        for index in range(worker_pipeline.MAX_FIGURES_PER_DOCUMENT + 1)
    ]
    content = DocumentContent(
        quality_level="B",
        sections=[Section(id="sec-1", blocks=[*paragraphs, *asset_blocks])],
    )
    png = _raster_bytes("PNG")
    parsed = ParsedPdfDocument(
        sections=content.sections,
        figure_images={block.id: png for block in asset_blocks},
    )
    candidate = SourceCandidate(
        source_format="pdf",
        content=content,
        parsed=parsed,
        report=DocumentCompleteness(True, None, 0, 70, 2, 0),
        source_bytes=b"%PDF synthetic",
        diagnostics=[],
    )
    calls: list[str] = []

    async def unexpected_materialization(
        _data: bytes,
        source_name: str,
        _content_type: str | None = None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        calls.append(source_name)
        return FigureAssetPayload(png, "png", "image/png", 16, 12, len(png))

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", unexpected_materialization)
    run = object.__new__(IngestRun)
    run._pdf_text = ""
    run._pdf_bytes = b"%PDF synthetic"

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=worker_pipeline.MaterializationDeadline.start(timeout_s=30.0),
    )

    assert calls == []
    assert candidate.report.code == "figure_asset_unresolved"
    assert candidate.materialized_figures == {}
    assert candidate.figure_asset_failures == [
        {
            "code": "figure_limit_exceeded",
            "figure_id": asset_blocks[worker_pipeline.MAX_FIGURES_PER_DOCUMENT].id,
            "source": "pdf",
        }
    ]


class _ExpireOnSecondAsset:
    def __init__(self) -> None:
        self.calls = 0

    def remaining(self, _operation_limit_s: float | None = None) -> float:
        self.calls += 1
        if self.calls == 2:
            raise FigureAssetError("materialization_timeout", "synthetic document deadline expired")
        return 30.0


async def test_validated_latex_cache_timeout_is_fatal_and_cleans_staged_asset() -> None:
    png = _raster_bytes("PNG")
    figures = [
        Block(id="fig-first", type="figure", asset_key="first.png"),
        Block(id="fig-second", type="figure", asset_key="second.png"),
    ]
    run, storage = _figure_run(figures[0], {})
    run.parsed = SimpleNamespace(figures=figures, blocks=figures)
    run._candidate_materialization_validated = True
    run._candidate_figure_failures = []
    run._candidate_materialized_figures = {
        figure.id: FigureAssetPayload(png, "png", "image/png", 16, 12, len(png))
        for figure in figures
    }

    with pytest.raises(FigureAssetError, match="deadline expired"):
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            await run._save_figures(
                "revision-id",
                uploaded_keys=uploaded_keys,
                deadline=_ExpireOnSecondAsset(),
            )

    first_key = StorageKeys.figure("paper-id", "revision-id", "fig-first", "png")
    assert storage.deletes == [("assets", [first_key])]


async def test_validated_pdf_cache_timeout_is_fatal_and_cleans_staged_asset() -> None:
    png = _raster_bytes("PNG")
    figure_images = {"pdf-first": png, "pdf-second": png}
    run, storage, blocks = _pdf_figure_run(figure_images)
    run.parsed_pdf.figures = blocks
    run._candidate_materialization_validated = True
    run._candidate_figure_failures = []
    run._candidate_materialized_figures = {
        block.id: FigureAssetPayload(png, "png", "image/png", 16, 12, len(png)) for block in blocks
    }

    with pytest.raises(FigureAssetError, match="deadline expired"):
        async with worker_pipeline._staged_revision_assets(storage) as uploaded_keys:
            await run._save_pdf_assets(
                "revision-id",
                uploaded_keys=uploaded_keys,
                deadline=_ExpireOnSecondAsset(),
            )

    first_key = StorageKeys.figure("paper-id", "revision-id", "pdf-first", "png")
    assert storage.deletes == [("assets", [first_key])]


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
    run.parsed.blocks = figures

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


async def test_pipeline_failure_clears_public_asset_key_and_records_diagnostic() -> None:
    fig = Block(id="fig-bad", type="figure", asset_key=r"\iftoggle{largefigures")
    run, storage = _figure_run(fig, {"images/plot.png": _raster_bytes("PNG")})

    output, warnings, failures = await run._save_figures("revision-id")

    assert fig.asset_key is None
    assert output == {}
    assert storage.puts == []
    assert warnings and "fig-bad" in warnings[0]
    assert failures == [
        {
            "code": "invalid_asset_path",
            "figure_id": "fig-bad",
            "source": "latex",
        }
    ]


@pytest.mark.parametrize("asset_key", [None, ""], ids=["none", "blank"])
async def test_figure_without_declared_source_requires_no_asset(asset_key: str | None) -> None:
    """\\includegraphics の無い figure(コードリスティング等)は素材化を要求しない(P3)。

    asset_key が最初から未設定の図は、コード/表のみを収めたキャプション付き
    コンテンツブロックであり、失敗としてカウントしてはならない。
    """
    fig = Block(id="fig-listing", type="figure", asset_key=asset_key)
    run, storage = _figure_run(fig, {})

    output, warnings, failures = await run._save_figures("revision-id")

    assert fig.asset_key == asset_key
    assert output == {}
    assert storage.puts == []
    assert warnings == []
    assert failures == []


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
    run.parsed.blocks = figures
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
    run.parsed.blocks = figures
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
    run.parsed.blocks = figures
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


async def test_pdf_assets_use_isolated_payload_bytes_extension_and_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"raw extracted figure"
    canonical = b"canonical validated payload"
    run, storage, blocks = _pdf_figure_run({"fig-derived": source})
    calls: list[tuple[bytes, str, str | None]] = []

    async def isolated(
        data: bytes,
        *,
        source_name: str,
        content_type: str | None = None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        calls.append((data, source_name, content_type))
        return FigureAssetPayload(
            canonical,
            "jpg",
            "image/jpeg",
            4,
            3,
            source_size=len(data),
        )

    monkeypatch.setattr(worker_pipeline, "isolated_figure_asset_payload", isolated)

    output, warnings, failures = await run._save_pdf_assets("revision-id")

    expected_key = StorageKeys.figure("paper-id", "revision-id", "fig-derived", "jpg")
    assert calls == [(source, "fig-derived.png", "image/png")]
    assert output == {"fig-derived": canonical}
    assert blocks[0].asset_key == expected_key
    assert storage.puts == [("assets", expected_key, canonical, "image/jpeg")]
    assert warnings == []
    assert failures == []


async def test_pdf_asset_rejects_malformed_png_without_upload() -> None:
    run, storage, blocks = _pdf_figure_run({"fig-malformed": b"\x89PNG\r\n\x1a\ntruncated"})

    output, _warnings, failures = await run._save_pdf_assets("revision-id")

    assert output == {}
    assert storage.puts == []
    assert blocks[0].asset_key is None
    assert failures[0]["code"] == "invalid_image"


async def test_pdf_asset_applies_individual_source_byte_limit_before_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _raster_bytes("PNG")
    run, storage, blocks = _pdf_figure_run({"fig-oversize": source})
    monkeypatch.setattr(figure_assets, "MAX_ASSET_BYTES", len(source) - 1)

    output, _warnings, failures = await run._save_pdf_assets("revision-id")

    assert output == {}
    assert storage.puts == []
    assert blocks[0].asset_key is None
    assert failures[0]["code"] == "asset_too_large"


async def test_pdf_asset_records_isolated_conversion_timeout_without_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, storage, blocks = _pdf_figure_run({"fig-timeout": _raster_bytes("PNG")})

    async def timeout(*_args: Any, **_kwargs: Any) -> FigureAssetPayload:
        raise FigureAssetError("conversion_timeout", "conversion timed out")

    monkeypatch.setattr(worker_pipeline, "isolated_figure_asset_payload", timeout)

    output, _warnings, failures = await run._save_pdf_assets("revision-id")

    assert output == {}
    assert storage.puts == []
    assert blocks[0].asset_key is None
    assert failures[0]["code"] == "conversion_timeout"


async def test_pdf_asset_aggregate_budget_counts_source_and_canonical_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"source-ten"
    canonical = b"canonical-output-is-longer"
    run, storage, blocks = _pdf_figure_run({"fig-budget": source})

    async def isolated(data: bytes, **_kwargs: Any) -> FigureAssetPayload:
        return FigureAssetPayload(
            canonical,
            "png",
            "image/png",
            4,
            3,
            source_size=1,
        )

    monkeypatch.setattr(worker_pipeline, "isolated_figure_asset_payload", isolated)
    monkeypatch.setattr(
        worker_pipeline,
        "MAX_TOTAL_FIGURE_MATERIALIZED_BYTES",
        len(source) + len(canonical) - 1,
    )

    output, _warnings, failures = await run._save_pdf_assets("revision-id")

    assert output == {}
    assert storage.puts == []
    assert blocks[0].asset_key is None
    assert failures[0]["code"] == "figure_bytes_exceeded"


async def test_pdf_asset_document_deadline_stops_later_isolated_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [10.0]
    png = _raster_bytes("PNG")
    run, storage, blocks = _pdf_figure_run({"fig-first": png, "fig-later": png})
    calls: list[str] = []

    async def isolated(
        data: bytes,
        *,
        source_name: str,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        calls.append(source_name)
        now[0] += 1.1
        return FigureAssetPayload(
            data,
            "png",
            "image/png",
            16,
            12,
            source_size=len(data),
        )

    monkeypatch.setattr(worker_pipeline, "isolated_figure_asset_payload", isolated)
    deadline = worker_pipeline.MaterializationDeadline.start(
        timeout_s=1.0,
        clock=lambda: now[0],
    )

    output, _warnings, failures = await run._save_pdf_assets(
        "revision-id",
        deadline=deadline,
    )

    assert calls == ["fig-first.png"]
    assert set(output) == {"fig-first"}
    assert len(storage.puts) == 1
    assert blocks[1].asset_key is None
    assert failures[0]["code"] == "materialization_timeout"


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


async def test_verified_figureless_revision_clears_previous_thumbnail_pointer() -> None:
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.paper_id = "paper-id"
    run.revision_id = "revision-new"
    run.deps = SimpleNamespace(s3=storage)
    old_key = "thumbnails/paper-id/revision-old/card.webp"
    paper = SimpleNamespace(thumbnail_key=old_key)

    async with worker_pipeline._staged_revision_assets(
        storage, restore_thumbnail_on_failure=paper
    ) as uploaded_keys:
        warnings = await run._make_thumbnail(
            paper,
            {},
            [],
            uploaded_keys=uploaded_keys,
        )

    assert warnings == []
    assert paper.thumbnail_key is None
    assert storage.puts == []
    assert storage.deletes == []


async def test_failed_figureless_revision_restores_previous_thumbnail_pointer() -> None:
    run = object.__new__(IngestRun)
    storage = _RecordingStorage()
    run.paper_id = "paper-id"
    run.revision_id = "revision-new"
    run.deps = SimpleNamespace(s3=storage)
    old_key = "thumbnails/paper-id/revision-old/card.webp"
    paper = SimpleNamespace(thumbnail_key=old_key)
    failure = RuntimeError("index failed after thumbnail selection")

    with pytest.raises(RuntimeError) as caught:
        async with worker_pipeline._staged_revision_assets(
            storage, restore_thumbnail_on_failure=paper
        ) as uploaded_keys:
            await run._make_thumbnail(
                paper,
                {},
                [],
                uploaded_keys=uploaded_keys,
            )
            raise failure

    assert caught.value is failure
    assert paper.thumbnail_key == old_key
    assert storage.puts == []
    assert storage.deletes == []


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
<picture>
<svg width="10" height="10"><style>@import url(https://example.org/a.css);</style>
<rect width="10" height="10"/></svg>
<img class="ltx_graphics" src="good.png" alt="fallback"/>
</picture>
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
