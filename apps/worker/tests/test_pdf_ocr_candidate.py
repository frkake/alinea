# mypy: disable-error-code="attr-defined"
"""Killable isolation contract for the final PDF OCR candidate.

This module intentionally monkeypatches private subprocess seams on ``source_candidates``.
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import inspect
import json
import multiprocessing as mp
import os
import pickle
import struct
import time
from pathlib import Path
from typing import Any

import pytest
from alinea_core.parsing.pdf_parser import ParsedPdfDocument, PdfParseError, parse_pdf
from alinea_core.settings import CoreSettings
from alinea_worker import source_candidates as source_candidate_module
from alinea_worker.source_candidates import (
    PDF_OCR_CANDIDATE_VERSION,
    CandidateUnavailable,
    count_pdf_text_evidence_isolated,
    parse_pdf_candidate,
    parse_pdf_candidate_async,
    parse_pdf_ocr_candidate,
)

_FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "py-core" / "tests" / "fixtures"


def _sleeping_ocr_worker(
    _data: bytes,
    *,
    ocr_language: str,
) -> Any:
    del ocr_language
    time.sleep(5)
    raise AssertionError("deadline should terminate this child first")


def _slow_successful_ocr_worker(
    data: bytes,
    *,
    ocr_language: str,
) -> Any:
    time.sleep(0.25)
    return _successful_ocr_worker(data, ocr_language=ocr_language)


def _successful_ocr_worker(
    data: bytes,
    *,
    ocr_language: str,
) -> Any:
    del ocr_language
    parsed = parse_pdf(data)
    parsed.stats["ocr"] = True
    parsed.stats["extracted_chars"] = sum(
        len(block.inlines[0].v)
        for block in parsed.blocks
        if block.type == "paragraph" and block.inlines
    )
    return parsed


def _inconsistent_ocr_worker(
    data: bytes,
    *,
    ocr_language: str,
) -> Any:
    del ocr_language
    return parse_pdf(data)


def _known_ocr_failure_worker(
    _data: bytes,
    *,
    ocr_language: str,
) -> Any:
    del ocr_language
    raise CandidateUnavailable(
        "pdf_ocr",
        "ocr_language_unavailable",
        "PDF OCR language data is unavailable",
    )


def _known_pdf_parse_failure_worker(
    _data: bytes,
    *,
    ocr_language: str,
) -> Any:
    del ocr_language
    raise PdfParseError("ocr_engine_unavailable", "PDF OCR engine is unavailable")


def _crashing_ocr_worker(
    _data: bytes,
    *,
    ocr_language: str,
) -> Any:
    del ocr_language
    os._exit(17)


def _sleeping_pdf_evidence_worker(_data: bytes) -> Any:
    time.sleep(5)
    raise AssertionError("cancellation should terminate this child first")


def _protocol_success_frames() -> tuple[bytes, list[bytes]]:
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()
    parsed = _successful_ocr_worker(data, ocr_language="eng")
    return source_candidate_module._prepare_pdf_ocr_success_frames(
        parsed,
        max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
    )


def _missing_image_frame_child(connection: Any, *_args: Any) -> None:
    metadata, _frames = _protocol_success_frames()
    connection.send_bytes(metadata)
    connection.close()


def _truncated_image_frame_child(connection: Any, *_args: Any) -> None:
    metadata, frames = _protocol_success_frames()
    connection.send_bytes(metadata)
    connection.send_bytes(frames[0][:-1])
    connection.close()


def _hash_mismatch_image_frame_child(connection: Any, *_args: Any) -> None:
    metadata, frames = _protocol_success_frames()
    connection.send_bytes(metadata)
    corrupted = bytes([frames[0][0] ^ 1]) + frames[0][1:]
    connection.send_bytes(corrupted)
    connection.close()


def _oversized_image_frame_child(connection: Any, *_args: Any) -> None:
    metadata, frames = _protocol_success_frames()
    connection.send_bytes(metadata)
    connection.send_bytes(frames[0] + b"x")
    connection.close()


def _extra_image_frame_child(connection: Any, *_args: Any) -> None:
    metadata, frames = _protocol_success_frames()
    connection.send_bytes(metadata)
    for frame in frames:
        connection.send_bytes(frame)
    connection.send_bytes(b"extra")
    connection.close()


def _delayed_image_frame_child(connection: Any, *_args: Any) -> None:
    metadata, _frames = _protocol_success_frames()
    connection.send_bytes(metadata)
    time.sleep(5)
    connection.close()


def _write_partial_frame(
    connection: Any,
    *,
    payload: bytes,
    header_bytes: int = 4,
    body_bytes: int = 0,
) -> None:
    header = struct.pack("!i", len(payload))
    os.write(connection.fileno(), header[:header_bytes])
    if header_bytes == 4 and body_bytes:
        os.write(connection.fileno(), payload[:body_bytes])
    time.sleep(0.3)
    connection.close()


def _partial_metadata_header_child(connection: Any, *_args: Any) -> None:
    _write_partial_frame(connection, payload=b"metadata", header_bytes=2)


def _partial_metadata_body_child(connection: Any, *_args: Any) -> None:
    _write_partial_frame(connection, payload=b"metadata", body_bytes=2)


def _partial_image_body_child(connection: Any, *_args: Any) -> None:
    metadata, frames = _protocol_success_frames()
    connection.send_bytes(metadata)
    _write_partial_frame(connection, payload=frames[0], body_bytes=1)


def _partial_evidence_body_child(connection: Any, *_args: Any) -> None:
    _write_partial_frame(connection, payload=b'{"protocol":"partial"}', body_bytes=3)


async def _new_child_pids(before: set[int]) -> set[int]:
    for _ in range(50):
        remaining = {
            process.pid
            for process in mp.active_children()
            if process.pid is not None and process.pid not in before
        }
        if not remaining:
            return set()
        await asyncio.sleep(0.02)
    return remaining


async def test_pdf_ocr_timeout_terminates_and_reaps_isolated_child() -> None:
    before = {process.pid for process in mp.active_children() if process.pid is not None}

    with pytest.raises(CandidateUnavailable) as exc_info:
        await parse_pdf_ocr_candidate(
            b"%PDF- synthetic",
            pdf_text="",
            timeout_s=0.05,
            worker=_sleeping_ocr_worker,
        )

    assert exc_info.value.source_format == "pdf_ocr"
    assert exc_info.value.code == "ocr_timeout"
    assert str(exc_info.value) == "PDF OCR deadline was exceeded"
    assert await _new_child_pids(before) == set()


async def test_pdf_ocr_rejects_invalid_language_before_spawning_child() -> None:
    with pytest.raises(CandidateUnavailable) as exc_info:
        await parse_pdf_ocr_candidate(
            b"%PDF- synthetic",
            pdf_text="",
            ocr_language="../eng",
            worker=_sleeping_ocr_worker,
        )

    assert exc_info.value.code == "ocr_language_invalid"


async def test_pdf_ocr_cancellation_terminates_and_reaps_isolated_child() -> None:
    before = {process.pid for process in mp.active_children() if process.pid is not None}
    task = asyncio.create_task(
        parse_pdf_ocr_candidate(
            b"%PDF- synthetic",
            pdf_text="",
            timeout_s=5.0,
            worker=_sleeping_ocr_worker,
        )
    )
    await asyncio.sleep(0.1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _new_child_pids(before) == set()


async def test_pdf_ocr_isolated_success_preserves_parsed_figures_and_identity() -> None:
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()

    candidate = await parse_pdf_ocr_candidate(
        data,
        pdf_text="",
        timeout_s=10.0,
        worker=_successful_ocr_worker,
    )

    assert isinstance(candidate.parsed, ParsedPdfDocument)
    assert candidate.parsed.stats["ocr"] is True
    assert candidate.parsed.figure_images
    assert candidate.diagnostics == [
        {
            "kind": "pdf_ocr",
            "version": PDF_OCR_CANDIDATE_VERSION,
            "language": "eng",
        }
    ]


async def test_pdf_ocr_rejects_child_result_without_ocr_stats_identity() -> None:
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()

    with pytest.raises(CandidateUnavailable) as exc_info:
        await parse_pdf_ocr_candidate(
            data,
            pdf_text="",
            timeout_s=10.0,
            worker=_inconsistent_ocr_worker,
        )

    assert exc_info.value.source_format == "pdf_ocr"
    assert exc_info.value.code == "ocr_crashed"


@pytest.mark.parametrize(
    ("worker", "expected_code"),
    [
        (_known_ocr_failure_worker, "ocr_language_unavailable"),
        (_known_pdf_parse_failure_worker, "ocr_engine_unavailable"),
        (_crashing_ocr_worker, "ocr_lifecycle"),
    ],
)
async def test_pdf_ocr_isolation_returns_stable_child_diagnostics(
    worker: Any,
    expected_code: str,
) -> None:
    with pytest.raises(CandidateUnavailable) as exc_info:
        await parse_pdf_ocr_candidate(
            b"%PDF- synthetic",
            pdf_text="",
            timeout_s=5.0,
            worker=worker,
        )

    assert exc_info.value.source_format == "pdf_ocr"
    assert exc_info.value.code == expected_code


async def test_pdf_ocr_isolation_rejects_oversized_child_output() -> None:
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()

    with pytest.raises(CandidateUnavailable) as exc_info:
        await parse_pdf_ocr_candidate(
            data,
            pdf_text="",
            timeout_s=10.0,
            max_output_bytes=128,
            worker=_successful_ocr_worker,
        )

    assert exc_info.value.code == "ocr_output_too_large"


def _valid_success_metadata(*, figures: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    parsed = _successful_ocr_worker(
        (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes(),
        ocr_language="eng",
    )
    return {
        "protocol": "pdf-ocr-ipc-1",
        "status": "ok",
        "document": {
            "quality_level": parsed.quality_level,
            "source_format": parsed.source_format,
            "parser_version": parsed.parser_version,
            "sections": [section.model_dump(mode="json") for section in parsed.sections],
            "warnings": list(parsed.warnings),
            "stats": dict(parsed.stats),
        },
        "figures": figures if figures is not None else [],
    }


def _metadata_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _metadata_bytes_with_escapes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def _metadata_blocks(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    def walk(section: dict[str, Any]) -> None:
        blocks.extend(section["blocks"])
        for child in section["sections"]:
            walk(child)

    for section in metadata["document"]["sections"]:
        walk(section)
    return blocks


def _first_metadata_block(
    metadata: dict[str, Any],
    block_type: str,
) -> dict[str, Any]:
    return next(block for block in _metadata_blocks(metadata) if block["type"] == block_type)


def test_pdf_ocr_child_sends_bounded_json_metadata_then_raw_image_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()
    payloads: list[bytes] = []

    class FakeConnection:
        def send_bytes(self, payload: bytes) -> None:
            payloads.append(payload)

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        source_candidate_module,
        "_apply_pdf_ocr_resource_limits",
        lambda *_args: None,
    )

    source_candidate_module._pdf_ocr_child_entry(
        FakeConnection(),  # type: ignore[arg-type]
        data,
        "eng",
        10.0,
        source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        _successful_ocr_worker,
    )

    metadata = json.loads(payloads[0].decode("utf-8"))
    assert metadata["protocol"] == "pdf-ocr-ipc-1"
    assert metadata["status"] == "ok"
    assert "figure_images" not in metadata["document"]
    assert len(payloads) == 1 + len(metadata["figures"])
    assert [len(payload) for payload in payloads[1:]] == [
        entry["size"] for entry in metadata["figures"]
    ]
    assert [hashlib.sha256(payload).hexdigest() for payload in payloads[1:]] == [
        entry["sha256"] for entry in metadata["figures"]
    ]


def test_pdf_ocr_transport_implementation_contains_no_pickle() -> None:
    source = inspect.getsource(source_candidate_module)

    assert "pickle" not in source.lower()


def _write_attack_marker(path: str) -> None:
    Path(path).write_text("executed", encoding="utf-8")


class _MarkerReduce:
    def __init__(self, path: str) -> None:
        self.path = path

    def __reduce__(self) -> tuple[Any, tuple[str]]:
        return (_write_attack_marker, (self.path,))


class _AllocationReduce:
    def __reduce__(self) -> tuple[Any, tuple[int]]:
        return (bytearray, (16 * 1024 * 1024,))


def test_pdf_ocr_metadata_rejects_pickle_reduce_without_parent_execution(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "parent-rce-marker"
    payload = pickle.dumps(_MarkerReduce(str(marker)), protocol=4)

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            payload,
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"
    assert not marker.exists()


def test_pdf_ocr_metadata_rejects_short_pickle_allocation_gadget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = pickle.dumps(_AllocationReduce(), protocol=4)
    allocations: list[int] = []

    def unexpected_bytearray(size: int = 0) -> bytearray:
        allocations.append(size)
        raise AssertionError("parent must not execute pickle allocation gadgets")

    assert len(payload) <= 64
    monkeypatch.setattr(builtins, "bytearray", unexpected_bytearray)

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            payload,
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"
    assert allocations == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"[" * 80 + b"0" + b"]" * 80, id="deep-nesting"),
        pytest.param(b"[" + b"0," * 300_000 + b"0]", id="list-bomb"),
        pytest.param(b"{" + b'"n":' + b"1" * 100 + b"}", id="number-bomb"),
    ],
)
def test_pdf_ocr_metadata_lexical_preflight_rejects_json_bombs(payload: bytes) -> None:
    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            payload,
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda value: value.update({"unknown": True}), id="root-unknown-key"),
        pytest.param(
            lambda value: value["document"]["stats"].update({"unknown": 1}),
            id="stats-unknown-key",
        ),
        pytest.param(
            lambda value: value["document"]["stats"].update(
                {"figure_caption_match_rate": float("nan")}
            ),
            id="nan",
        ),
        pytest.param(
            lambda value: value["document"].update({"parser_version": "pdf-0.0.0"}),
            id="parser-version",
        ),
        pytest.param(
            lambda value: value["document"]["stats"].update({"ocr": False}),
            id="ocr-stats",
        ),
    ],
)
def test_pdf_ocr_metadata_rejects_unknown_nonfinite_or_inconsistent_fields(
    mutate: Any,
) -> None:
    metadata = _valid_success_metadata()
    mutate(metadata)

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            _metadata_bytes(metadata),
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"


@pytest.mark.parametrize(
    "unsafe_text",
    ["\ud800", "\u0001", "\u0085", "\u202e"],
)
def test_pdf_ocr_metadata_rejects_non_scalar_control_and_bidi_text(
    unsafe_text: str,
) -> None:
    metadata = _valid_success_metadata()
    paragraph = _first_metadata_block(metadata, "paragraph")
    paragraph["inlines"][0]["v"] = unsafe_text

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            _metadata_bytes_with_escapes(metadata),
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda block: block["inlines"][0].update({"t": "url", "href": "javascript:alert(1)"}),
            id="javascript-url-inline",
        ),
        pytest.param(
            lambda block: block["inlines"][0].update({"ref": "unexpected"}),
            id="text-inline-reference",
        ),
        pytest.param(
            lambda block: block.update({"code": "irrelevant"}),
            id="paragraph-code-field",
        ),
        pytest.param(
            lambda block: block.update({"type": "code", "language": "python", "code": "x"}),
            id="unsupported-pdf-block-type",
        ),
    ],
)
def test_pdf_ocr_metadata_rejects_semantically_impossible_inline_and_block_fields(
    mutate: Any,
) -> None:
    metadata = _valid_success_metadata()
    mutate(_first_metadata_block(metadata, "paragraph"))

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            _metadata_bytes(metadata),
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"


def test_pdf_ocr_metadata_rejects_child_asset_key() -> None:
    metadata = _valid_success_metadata()
    figure = _first_metadata_block(metadata, "figure")
    figure["asset_key"] = "javascript:unexpected"

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            _metadata_bytes(metadata),
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"


def test_pdf_ocr_error_envelope_rejects_unsafe_unicode_message() -> None:
    payload = _metadata_bytes_with_escapes(
        {
            "protocol": source_candidate_module.PDF_OCR_IPC_VERSION,
            "status": "error",
            "source_format": "pdf_ocr",
            "code": "ocr_failed",
            "message": "\ud800",
        }
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            payload,
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"


@pytest.mark.parametrize(
    ("source_format", "expected_ocr", "impossible_code", "expected_code"),
    [
        ("pdf_ocr", True, "source_not_found", "ocr_crashed"),
        ("pdf", False, "ocr_engine_unavailable", "pdf_crashed"),
    ],
)
def test_pdf_parser_error_envelope_rejects_operation_impossible_code(
    source_format: str,
    expected_ocr: bool,
    impossible_code: str,
    expected_code: str,
) -> None:
    payload = _metadata_bytes(
        {
            "protocol": source_candidate_module.PDF_OCR_IPC_VERSION,
            "status": "error",
            "source_format": source_format,
            "code": impossible_code,
            "message": "synthetic impossible child error",
        }
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            payload,
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            expected_ocr=expected_ocr,
            error_source_format=source_format,
        )

    assert exc_info.value.code == expected_code


def test_pdf_text_evidence_error_envelope_rejects_impossible_code() -> None:
    payload = _metadata_bytes(
        {
            "protocol": source_candidate_module.PDF_TEXT_EVIDENCE_IPC_VERSION,
            "status": "error",
            "source_format": "pdf",
            "code": "source_not_found",
            "message": "synthetic impossible child error",
        }
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_text_evidence_frame(payload)

    assert exc_info.value.code == "pdf_crashed"


@pytest.mark.parametrize(
    ("source_format", "expected_ocr", "allowed_code"),
    [
        ("pdf", False, "pdf_page_limit"),
        ("pdf_ocr", True, "ocr_language_unavailable"),
    ],
)
def test_pdf_parser_error_envelope_preserves_operation_allowed_code(
    source_format: str,
    expected_ocr: bool,
    allowed_code: str,
) -> None:
    payload = _metadata_bytes(
        {
            "protocol": source_candidate_module.PDF_OCR_IPC_VERSION,
            "status": "error",
            "source_format": source_format,
            "code": allowed_code,
            "message": "synthetic allowed child error",
        }
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            payload,
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            expected_ocr=expected_ocr,
            error_source_format=source_format,
        )

    assert exc_info.value.code == allowed_code


def test_pdf_ocr_metadata_rejects_duplicate_figure_ids() -> None:
    payload = b"figure"
    entry = {
        "id": "blk-1-fig1-abcd",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    metadata = _valid_success_metadata(figures=[entry, dict(entry)])

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            _metadata_bytes(metadata),
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_crashed"


def test_pdf_ocr_metadata_rejects_oversized_frame_before_json_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_candidate_module, "MAX_PDF_OCR_METADATA_BYTES", 64)
    payload = b"{" + b" " * 64 + b"}"

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            payload,
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_output_too_large"


@pytest.mark.parametrize(
    "declared_size",
    [source_candidate_module.MAX_ASSET_BYTES + 1, 10**63],
)
def test_pdf_ocr_metadata_classifies_declared_image_size_overflow(
    declared_size: int,
) -> None:
    metadata_frame, _images = _protocol_success_frames()
    metadata = json.loads(metadata_frame)
    metadata["figures"][0]["size"] = declared_size

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._decode_pdf_ocr_metadata_frame(
            _metadata_bytes(metadata),
            max_output_bytes=source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
        )

    assert exc_info.value.code == "ocr_output_too_large"


@pytest.mark.parametrize(
    ("payload", "declared_size", "declared_hash", "expected_code"),
    [
        pytest.param(b"x", 2, hashlib.sha256(b"x").hexdigest(), "ocr_crashed", id="size"),
        pytest.param(b"x", 1, hashlib.sha256(b"y").hexdigest(), "ocr_crashed", id="hash"),
    ],
)
def test_pdf_ocr_image_frame_requires_exact_declared_size_and_hash(
    payload: bytes,
    declared_size: int,
    declared_hash: str,
    expected_code: str,
) -> None:
    entry = source_candidate_module._PdfOcrImageManifestEntry(
        block_id="blk-1-fig1-abcd",
        size=declared_size,
        sha256=declared_hash,
    )
    receive_connection, send_connection = mp.Pipe(duplex=False)
    send_connection.send_bytes(payload)
    send_connection.close()

    try:
        with pytest.raises(CandidateUnavailable) as exc_info:
            source_candidate_module._receive_pdf_ocr_image_frame(
                receive_connection,
                entry,
            )
    finally:
        receive_connection.close()

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    ("child_entry", "expected_code"),
    [
        pytest.param(_missing_image_frame_child, "ocr_lifecycle", id="missing"),
        pytest.param(_truncated_image_frame_child, "ocr_crashed", id="truncated"),
        pytest.param(_hash_mismatch_image_frame_child, "ocr_crashed", id="hash"),
        pytest.param(_oversized_image_frame_child, "ocr_crashed", id="oversized-declaration"),
        pytest.param(_extra_image_frame_child, "ocr_crashed", id="extra"),
    ],
)
async def test_pdf_ocr_protocol_rejects_missing_truncated_or_extra_frames(
    child_entry: Any,
    expected_code: str,
) -> None:
    cancellation_event = source_candidate_module.threading.Event()

    with pytest.raises(CandidateUnavailable) as exc_info:
        await asyncio.to_thread(
            source_candidate_module._run_isolated_pdf_ocr,
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=cancellation_event,
            child_entry=child_entry,
        )

    assert exc_info.value.code == expected_code


async def test_pdf_ocr_deadline_applies_while_waiting_for_image_frames() -> None:
    before = {process.pid for process in mp.active_children() if process.pid is not None}

    with pytest.raises(CandidateUnavailable) as exc_info:
        await asyncio.to_thread(
            source_candidate_module._run_isolated_pdf_ocr,
            b"%PDF- synthetic",
            "eng",
            0.2,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=source_candidate_module.threading.Event(),
            child_entry=_delayed_image_frame_child,
        )

    assert exc_info.value.code == "ocr_timeout"
    assert await _new_child_pids(before) == set()


async def test_pdf_ocr_cancellation_applies_while_waiting_for_image_frames() -> None:
    before = {process.pid for process in mp.active_children() if process.pid is not None}
    cancellation_event = source_candidate_module.threading.Event()
    task = asyncio.create_task(
        asyncio.to_thread(
            source_candidate_module._run_isolated_pdf_ocr,
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=cancellation_event,
            child_entry=_delayed_image_frame_child,
        )
    )
    await asyncio.sleep(0.2)

    cancellation_event.set()
    result = await task

    assert result is source_candidate_module._CANCELLED_PDF_OCR_RESULT
    assert await _new_child_pids(before) == set()


@pytest.mark.parametrize(
    "child_entry",
    [
        _partial_metadata_header_child,
        _partial_metadata_body_child,
        _partial_image_body_child,
    ],
)
async def test_pdf_ocr_deadline_covers_partial_frame_header_and_body(
    child_entry: Any,
) -> None:
    before = {process.pid for process in mp.active_children() if process.pid is not None}

    with pytest.raises(CandidateUnavailable) as exc_info:
        await asyncio.to_thread(
            source_candidate_module._run_isolated_pdf_ocr,
            b"%PDF- synthetic",
            "eng",
            0.05,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=source_candidate_module.threading.Event(),
            child_entry=child_entry,
        )

    assert exc_info.value.code == "ocr_timeout"
    assert await _new_child_pids(before) == set()


async def test_pdf_text_evidence_deadline_covers_partial_frame_body() -> None:
    before = {process.pid for process in mp.active_children() if process.pid is not None}

    with pytest.raises(CandidateUnavailable) as exc_info:
        await asyncio.to_thread(
            source_candidate_module._run_isolated_pdf_text_evidence,
            b"%PDF- synthetic",
            0.05,
            _sleeping_pdf_evidence_worker,
            cancellation_event=source_candidate_module.threading.Event(),
            child_entry=_partial_evidence_body_child,
        )

    assert exc_info.value.code == "pdf_timeout"
    assert await _new_child_pids(before) == set()


class _UnstartedConnection:
    def __init__(self, *, close_error: bool = False) -> None:
        self.closed = False
        self.closed_event = source_candidate_module.threading.Event()
        self.close_calls = 0
        self.close_error = close_error

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        self.closed_event.set()
        if self.close_error:
            raise OSError("synthetic connection close failure")


class _UnstartedProcess:
    pid: int | None = None

    def __init__(self, *, start_error: bool) -> None:
        self.start_error = start_error
        self.started = False
        self.closed = False
        self.close_calls = 0
        self.closed_event = source_candidate_module.threading.Event()

    def start(self) -> None:
        self.started = True
        if self.start_error:
            raise OSError("synthetic process start failure")

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        self.closed_event.set()


class _UnstartedContext:
    def __init__(self, *, start_error: bool) -> None:
        self.process = _UnstartedProcess(start_error=start_error)
        self.connections = (_UnstartedConnection(), _UnstartedConnection())
        self.pipe_calls = 0
        self.process_calls = 0

    def Pipe(self, *, duplex: bool) -> tuple[Any, Any]:  # noqa: N802
        assert duplex is False
        self.pipe_calls += 1
        return self.connections

    def Process(self, **_kwargs: Any) -> _UnstartedProcess:  # noqa: N802
        self.process_calls += 1
        return self.process


class _ConstructorFailureContext:
    def __init__(self, operation: str) -> None:
        self.operation = operation
        self.connections = (_UnstartedConnection(), _UnstartedConnection())

    def Pipe(self, *, duplex: bool) -> tuple[Any, Any]:  # noqa: N802
        assert duplex is False
        if self.operation == "pipe":
            raise OSError("synthetic pipe construction failure")
        return self.connections

    def Process(self, **_kwargs: Any) -> _UnstartedProcess:  # noqa: N802
        if self.operation == "process":
            raise OSError("synthetic process construction failure")
        return _UnstartedProcess(start_error=False)


class _ExpiredStartProcess(_UnstartedProcess):
    pid: int | None = None

    def __init__(self) -> None:
        super().__init__(start_error=False)
        self.alive = False
        self.exitcode: int | None = None
        self.start_completed = source_candidate_module.threading.Event()
        self.terminated = source_candidate_module.threading.Event()

    def start(self) -> None:
        self.started = True
        time.sleep(0.30)
        self.pid = 12345
        self.alive = True
        self.exitcode = None
        self.start_completed.set()

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def kill(self) -> None:
        self.alive = False
        self.exitcode = -9

    def terminate(self) -> None:
        self.terminated.set()
        self.alive = False
        self.exitcode = -15


class _ExpiredStartContext:
    def __init__(self) -> None:
        self.process = _ExpiredStartProcess()
        self.connections = (_UnstartedConnection(), _UnstartedConnection())

    def Pipe(self, *, duplex: bool) -> tuple[Any, Any]:  # noqa: N802
        assert duplex is False
        return self.connections

    def Process(self, **_kwargs: Any) -> _ExpiredStartProcess:  # noqa: N802
        return self.process


class _CountingStartGate:
    def __init__(self) -> None:
        self.acquisitions = 0
        self.releases = 0
        self.held = False

    def acquire(self, *, timeout: float | None = None) -> bool:
        del timeout
        assert self.held is False
        self.held = True
        self.acquisitions += 1
        return True

    def release(self) -> None:
        assert self.held is True
        self.held = False
        self.releases += 1


class _BlockingStartProcess(_UnstartedProcess):
    pid: int | None = None

    def __init__(
        self,
        *,
        start_release: Any,
        cleanup_release: Any,
    ) -> None:
        super().__init__(start_error=False)
        self.start_release = start_release
        self.cleanup_release = cleanup_release
        self.start_entered = source_candidate_module.threading.Event()
        self.cleanup_entered = source_candidate_module.threading.Event()
        self.alive = False
        self.exitcode: int | None = None

    def start(self) -> None:
        self.started = True
        self.start_entered.set()
        self.start_release.wait()
        self.pid = 12345
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.cleanup_entered.set()
        self.cleanup_release.wait()
        self.alive = False
        self.exitcode = -15

    def kill(self) -> None:
        self.alive = False
        self.exitcode = -9

    def join(self, timeout: float | None = None) -> None:
        del timeout


class _ImmediateStartProcess(_BlockingStartProcess):
    def start(self) -> None:
        self.started = True
        self.pid = 12346
        self.alive = False
        self.exitcode = 0


class _BoundedStartContext:
    def __init__(self) -> None:
        self.start_release = source_candidate_module.threading.Event()
        self.cleanup_release = source_candidate_module.threading.Event()
        self.normal_mode = False
        self.pipe_calls = 0
        self.process_calls = 0
        self.connections: list[tuple[_UnstartedConnection, _UnstartedConnection]] = []
        self.processes: list[_BlockingStartProcess] = []

    def Pipe(self, *, duplex: bool) -> tuple[Any, Any]:  # noqa: N802
        assert duplex is False
        self.pipe_calls += 1
        pair = (_UnstartedConnection(), _UnstartedConnection())
        self.connections.append(pair)
        return pair

    def Process(self, **_kwargs: Any) -> _BlockingStartProcess:  # noqa: N802
        self.process_calls += 1
        process_type = _ImmediateStartProcess if self.normal_mode else _BlockingStartProcess
        process = process_type(
            start_release=self.start_release,
            cleanup_release=self.cleanup_release,
        )
        self.processes.append(process)
        return process


def _pdf_start_thread_count() -> int:
    return sum(
        thread.name == "alinea-pdf-subprocess-start" and thread.is_alive()
        for thread in source_candidate_module.threading.enumerate()
    )


def test_pdf_ocr_start_failure_is_normalized_and_closes_unstarted_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _UnstartedContext(start_error=True)
    gate = _CountingStartGate()
    monkeypatch.setattr(source_candidate_module.mp, "get_context", lambda _kind: context)
    monkeypatch.setattr(
        source_candidate_module,
        "_PDF_SUBPROCESS_START_GATE",
        gate,
        raising=False,
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._run_isolated_pdf_ocr(
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=source_candidate_module.threading.Event(),
        )

    assert exc_info.value.code == "ocr_lifecycle"
    assert context.process.started is True
    assert context.process.closed is True
    assert all(connection.closed for connection in context.connections)
    assert (gate.acquisitions, gate.releases, gate.held) == (1, 1, False)


@pytest.mark.parametrize("operation", ["ocr", "normal", "evidence"])
def test_pdf_pre_cancel_does_not_acquire_or_construct_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    context = _UnstartedContext(start_error=False)
    gate = _CountingStartGate()
    get_context_calls = 0

    def get_context(_kind: str) -> Any:
        nonlocal get_context_calls
        get_context_calls += 1
        return context

    monkeypatch.setattr(source_candidate_module.mp, "get_context", get_context)
    monkeypatch.setattr(source_candidate_module, "_PDF_SUBPROCESS_START_GATE", gate)
    cancellation_event = source_candidate_module.threading.Event()
    cancellation_event.set()

    if operation == "evidence":
        result = source_candidate_module._run_isolated_pdf_text_evidence(
            b"%PDF- synthetic",
            5.0,
            _sleeping_pdf_evidence_worker,
            cancellation_event=cancellation_event,
        )
    else:
        result = source_candidate_module._run_isolated_pdf_ocr(
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=cancellation_event,
            expected_ocr=operation == "ocr",
            error_source_format="pdf_ocr" if operation == "ocr" else "pdf",
        )

    assert result is source_candidate_module._CANCELLED_PDF_OCR_RESULT
    assert (gate.acquisitions, gate.releases, gate.held) == (0, 0, False)
    assert get_context_calls == 0
    assert context.pipe_calls == 0
    assert context.process_calls == 0
    assert context.process.started is False
    assert context.process.closed is False
    assert all(connection.closed is False for connection in context.connections)


@pytest.mark.parametrize("race", ["cancel", "deadline"])
def test_pdf_start_lease_rechecks_operation_before_constructor(
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    context = _UnstartedContext(start_error=False)
    cancellation_event = source_candidate_module.threading.Event()

    class StateChangingGate(_CountingStartGate):
        def acquire(self, *, timeout: float | None = None) -> bool:
            acquired = super().acquire(timeout=timeout)
            if race == "cancel":
                cancellation_event.set()
            else:
                time.sleep(0.02)
            return acquired

    gate = StateChangingGate()
    get_context_calls = 0

    def get_context(_kind: str) -> Any:
        nonlocal get_context_calls
        get_context_calls += 1
        return context

    monkeypatch.setattr(source_candidate_module.mp, "get_context", get_context)
    monkeypatch.setattr(source_candidate_module, "_PDF_SUBPROCESS_START_GATE", gate)

    if race == "cancel":
        result = source_candidate_module._run_isolated_pdf_ocr(
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=cancellation_event,
        )
        assert result is source_candidate_module._CANCELLED_PDF_OCR_RESULT
    else:
        with pytest.raises(CandidateUnavailable) as exc_info:
            source_candidate_module._run_isolated_pdf_ocr(
                b"%PDF- synthetic",
                "eng",
                0.01,
                source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
                _successful_ocr_worker,
                cancellation_event=cancellation_event,
            )
        assert exc_info.value.code == "ocr_timeout"

    assert (gate.acquisitions, gate.releases, gate.held) == (1, 1, False)
    assert get_context_calls == 0
    assert context.pipe_calls == 0
    assert context.process_calls == 0


@pytest.mark.parametrize("operation", ["context", "pipe", "process"])
def test_pdf_ocr_constructor_failures_are_lifecycle_and_cleanup_partial_state(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    context = _ConstructorFailureContext(operation)
    gate = _CountingStartGate()

    def get_context(_kind: str) -> Any:
        if operation == "context":
            raise OSError("synthetic context construction failure")
        return context

    monkeypatch.setattr(source_candidate_module.mp, "get_context", get_context)
    monkeypatch.setattr(
        source_candidate_module,
        "_PDF_SUBPROCESS_START_GATE",
        gate,
        raising=False,
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._run_isolated_pdf_ocr(
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=source_candidate_module.threading.Event(),
        )

    assert exc_info.value.code == "ocr_lifecycle"
    assert (gate.acquisitions, gate.releases, gate.held) == (1, 1, False)
    if operation == "process":
        assert all(connection.closed for connection in context.connections)


def test_pdf_process_thread_start_failure_releases_start_lease_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _UnstartedContext(start_error=False)
    gate = _CountingStartGate()

    class FailingThread:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            raise OSError("synthetic thread start failure")

    monkeypatch.setattr(source_candidate_module.mp, "get_context", lambda _kind: context)
    monkeypatch.setattr(source_candidate_module.threading, "Thread", FailingThread)
    monkeypatch.setattr(
        source_candidate_module,
        "_PDF_SUBPROCESS_START_GATE",
        gate,
        raising=False,
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._run_isolated_pdf_ocr(
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=source_candidate_module.threading.Event(),
        )

    assert exc_info.value.code == "ocr_lifecycle"
    assert context.process.closed is True
    assert all(connection.closed for connection in context.connections)
    assert (gate.acquisitions, gate.releases, gate.held) == (1, 1, False)


def test_pdf_ocr_cleanup_attempts_both_connection_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _UnstartedContext(start_error=True)
    context.connections = (
        _UnstartedConnection(close_error=True),
        _UnstartedConnection(close_error=True),
    )
    monkeypatch.setattr(source_candidate_module.mp, "get_context", lambda _kind: context)
    cancellation_event = source_candidate_module.threading.Event()

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._run_isolated_pdf_ocr(
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=cancellation_event,
        )

    assert exc_info.value.code == "ocr_lifecycle"
    assert [connection.close_calls for connection in context.connections] == [1, 1]


def test_pdf_ocr_process_start_is_inside_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _ExpiredStartContext()
    monkeypatch.setattr(source_candidate_module.mp, "get_context", lambda _kind: context)

    started_at = time.monotonic()
    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._run_isolated_pdf_ocr(
            b"%PDF- synthetic",
            "eng",
            0.01,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=source_candidate_module.threading.Event(),
        )
    elapsed = time.monotonic() - started_at

    assert exc_info.value.code == "ocr_timeout"
    assert elapsed < 0.10
    assert context.process.closed_event.wait(timeout=1.0)
    assert context.process.start_completed.is_set()
    assert context.process.terminated.is_set()
    assert context.process.close_calls == 1
    assert all(connection.closed_event.wait(timeout=1.0) for connection in context.connections)
    assert [connection.close_calls for connection in context.connections] == [1, 1]


def test_pdf_ocr_cancellation_during_process_start_skips_receive_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _ExpiredStartContext()
    monkeypatch.setattr(source_candidate_module.mp, "get_context", lambda _kind: context)
    cancellation_event = source_candidate_module.threading.Event()
    cancellation_timer = source_candidate_module.threading.Timer(
        0.01,
        cancellation_event.set,
    )

    cancellation_timer.start()
    started_at = time.monotonic()
    try:
        result = source_candidate_module._run_isolated_pdf_ocr(
            b"%PDF- synthetic",
            "eng",
            5.0,
            source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
            _successful_ocr_worker,
            cancellation_event=cancellation_event,
        )
    finally:
        cancellation_timer.cancel()
        cancellation_timer.join()
    elapsed = time.monotonic() - started_at

    assert result is source_candidate_module._CANCELLED_PDF_OCR_RESULT
    assert elapsed < 0.10
    assert context.process.closed_event.wait(timeout=1.0)
    assert context.process.start_completed.is_set()
    assert context.process.terminated.is_set()
    assert context.process.close_calls == 1
    assert all(connection.closed_event.wait(timeout=1.0) for connection in context.connections)
    assert [connection.close_calls for connection in context.connections] == [1, 1]


def test_pdf_process_start_hang_is_process_wide_bounded_until_cleanup_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _BoundedStartContext()
    monkeypatch.setattr(source_candidate_module.mp, "get_context", lambda _kind: context)
    baseline_threads = _pdf_start_thread_count()

    try:
        started_at = time.monotonic()
        with pytest.raises(CandidateUnavailable) as first_error:
            source_candidate_module._run_isolated_pdf_ocr(
                b"%PDF- first",
                "eng",
                0.01,
                source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
                _successful_ocr_worker,
                cancellation_event=source_candidate_module.threading.Event(),
            )
        assert first_error.value.code == "ocr_timeout"
        assert time.monotonic() - started_at < 0.10
        assert context.processes[0].start_entered.wait(timeout=0.10)

        started_at = time.monotonic()
        with pytest.raises(CandidateUnavailable) as normal_error:
            source_candidate_module._run_isolated_pdf_ocr(
                b"%PDF- second",
                "eng",
                0.01,
                source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
                _successful_ocr_worker,
                cancellation_event=source_candidate_module.threading.Event(),
                expected_ocr=False,
                error_source_format="pdf",
            )
        assert normal_error.value.code == "pdf_timeout"
        assert time.monotonic() - started_at < 0.10

        cancellation_event = source_candidate_module.threading.Event()
        cancellation_timer = source_candidate_module.threading.Timer(
            0.01,
            cancellation_event.set,
        )
        cancellation_timer.start()
        started_at = time.monotonic()
        try:
            evidence_result = source_candidate_module._run_isolated_pdf_text_evidence(
                b"%PDF- third",
                5.0,
                _sleeping_pdf_evidence_worker,
                cancellation_event=cancellation_event,
            )
        finally:
            cancellation_timer.cancel()
            cancellation_timer.join()
        assert evidence_result is source_candidate_module._CANCELLED_PDF_OCR_RESULT
        assert time.monotonic() - started_at < 0.10

        assert context.pipe_calls == 1
        assert context.process_calls == 1
        assert len(context.processes) == 1
        assert _pdf_start_thread_count() == baseline_threads + 1

        context.start_release.set()
        assert context.processes[0].cleanup_entered.wait(timeout=1.0)

        with pytest.raises(CandidateUnavailable) as cleanup_wait_error:
            source_candidate_module._run_isolated_pdf_ocr(
                b"%PDF- cleanup-wait",
                "eng",
                0.01,
                source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
                _successful_ocr_worker,
                cancellation_event=source_candidate_module.threading.Event(),
                expected_ocr=False,
                error_source_format="pdf",
            )
        assert cleanup_wait_error.value.code == "pdf_timeout"
        assert context.pipe_calls == 1
        assert context.process_calls == 1

        context.cleanup_release.set()
        assert context.processes[0].closed_event.wait(timeout=1.0)
        assert all(
            connection.closed_event.wait(timeout=1.0) for connection in context.connections[0]
        )
        assert context.processes[0].close_calls == 1
        assert [connection.close_calls for connection in context.connections[0]] == [1, 1]

        deadline = time.monotonic() + 1.0
        while _pdf_start_thread_count() != baseline_threads and time.monotonic() < deadline:
            time.sleep(0.005)
        assert _pdf_start_thread_count() == baseline_threads

        context.normal_mode = True
        with pytest.raises(CandidateUnavailable) as admitted_error:
            source_candidate_module._run_isolated_pdf_ocr(
                b"%PDF- admitted",
                "eng",
                1.0,
                source_candidate_module.MAX_PDF_OCR_OUTPUT_BYTES,
                _successful_ocr_worker,
                cancellation_event=source_candidate_module.threading.Event(),
                expected_ocr=False,
                error_source_format="pdf",
            )
        assert admitted_error.value.code == "pdf_lifecycle"
        assert context.pipe_calls == 2
        assert context.process_calls == 2
    finally:
        context.start_release.set()
        context.cleanup_release.set()
        for process in context.processes:
            process.closed_event.wait(timeout=1.0)


class _LifecycleProcess:
    def __init__(
        self,
        *,
        alive: bool = True,
        fail: str | None = None,
        terminate_stops: bool = True,
        kill_stops: bool = True,
    ) -> None:
        self.alive = alive
        self.fail = fail
        self.terminate_stops = terminate_stops
        self.kill_stops = kill_stops
        self.exitcode: int | None = None if alive else 0
        self.calls: list[str] = []

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise OSError(f"synthetic {name} failure")

    def is_alive(self) -> bool:
        self._call("is_alive")
        return self.alive

    def terminate(self) -> None:
        self._call("terminate")
        if self.terminate_stops:
            self.alive = False
            self.exitcode = -15

    def kill(self) -> None:
        self._call("kill")
        if self.kill_stops:
            self.alive = False
            self.exitcode = -9

    def join(self, timeout: float | None = None) -> None:
        del timeout
        self._call("join")

    def close(self) -> None:
        self._call("close")


@pytest.mark.parametrize(
    "failed_operation",
    ["is_alive", "terminate", "join", "kill", "close"],
)
def test_pdf_ocr_lifecycle_normalizes_process_operation_errors(
    failed_operation: str,
) -> None:
    process = _LifecycleProcess(
        fail=failed_operation,
        terminate_stops=failed_operation != "kill",
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._terminate_pdf_ocr_child(process)

    assert exc_info.value.code == "ocr_lifecycle"


def test_pdf_ocr_lifecycle_escalates_from_noop_terminate_to_kill() -> None:
    process = _LifecycleProcess(terminate_stops=False, kill_stops=True)

    source_candidate_module._terminate_pdf_ocr_child(process)

    assert "terminate" in process.calls
    assert "kill" in process.calls
    assert process.alive is False


def test_pdf_ocr_lifecycle_reports_unreaped_process() -> None:
    process = _LifecycleProcess(terminate_stops=False, kill_stops=False)

    with pytest.raises(CandidateUnavailable) as exc_info:
        source_candidate_module._terminate_pdf_ocr_child(process)

    assert exc_info.value.code == "ocr_lifecycle"


async def test_pdf_ocr_drain_retrieves_done_task_exception() -> None:
    async def fail() -> None:
        raise CandidateUnavailable("pdf_ocr", "ocr_lifecycle", "synthetic cleanup failure")

    task = asyncio.create_task(fail())
    await asyncio.sleep(0)
    assert task.done()

    error = await source_candidate_module._drain_pdf_ocr_supervisor(task)

    assert isinstance(error, CandidateUnavailable)
    assert error.code == "ocr_lifecycle"


async def test_pdf_ocr_cancellation_keeps_original_error_and_logs_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[dict[str, Any]] = []

    def slow_failure(*_args: Any, **_kwargs: Any) -> Any:
        time.sleep(0.2)
        raise CandidateUnavailable("pdf_ocr", "ocr_lifecycle", "synthetic cleanup failure")

    async def record_warning(_event: str, **kwargs: Any) -> None:
        logs.append(kwargs)

    monkeypatch.setattr(source_candidate_module, "_run_isolated_pdf_ocr", slow_failure)
    monkeypatch.setattr(source_candidate_module.log, "awarning", record_warning)
    task = asyncio.create_task(
        parse_pdf_ocr_candidate(
            b"%PDF- synthetic",
            pdf_text="",
            timeout_s=5.0,
            worker=_sleeping_ocr_worker,
        )
    )
    await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task

    assert isinstance(exc_info.value.__cause__, CandidateUnavailable)
    assert logs == [{"code": "ocr_lifecycle", "error_type": "CandidateUnavailable"}]


async def test_pdf_ocr_admission_limits_active_children_to_configured_value() -> None:
    source_candidate_module._clear_pdf_ocr_admission_for_tests()
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()
    before = {process.pid for process in mp.active_children() if process.pid is not None}
    tasks = [
        asyncio.create_task(
            parse_pdf_ocr_candidate(
                data,
                pdf_text="",
                timeout_s=10.0,
                admission_limit=1,
                worker=_slow_successful_ocr_worker,
            )
        )
        for _ in range(3)
    ]
    max_active = 0
    while not all(task.done() for task in tasks):
        active = {
            process.pid
            for process in mp.active_children()
            if process.pid is not None and process.pid not in before
        }
        max_active = max(max_active, len(active))
        await asyncio.sleep(0.01)

    await asyncio.gather(*tasks)
    assert max_active == 1
    assert await _new_child_pids(before) == set()
    source_candidate_module._clear_pdf_ocr_admission_for_tests()


async def test_pdf_ocr_admission_wait_consumes_deadline_and_releases_after_cancel() -> None:
    source_candidate_module._clear_pdf_ocr_admission_for_tests()
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()
    holder = asyncio.create_task(
        parse_pdf_ocr_candidate(
            data,
            pdf_text="",
            timeout_s=10.0,
            admission_limit=1,
            worker=_slow_successful_ocr_worker,
        )
    )
    await asyncio.sleep(0.05)

    with pytest.raises(CandidateUnavailable) as timeout_error:
        await parse_pdf_ocr_candidate(
            data,
            pdf_text="",
            timeout_s=0.05,
            admission_limit=1,
            worker=_successful_ocr_worker,
        )
    assert timeout_error.value.code == "ocr_timeout"

    waiter = asyncio.create_task(
        parse_pdf_ocr_candidate(
            data,
            pdf_text="",
            timeout_s=10.0,
            admission_limit=1,
            worker=_successful_ocr_worker,
        )
    )
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await holder

    candidate = await parse_pdf_ocr_candidate(
        data,
        pdf_text="",
        timeout_s=10.0,
        admission_limit=1,
        worker=_successful_ocr_worker,
    )
    assert candidate.report.accepted
    source_candidate_module._clear_pdf_ocr_admission_for_tests()


async def test_pdf_ocr_fails_closed_on_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_candidate_module.sys, "platform", "darwin")

    with pytest.raises(CandidateUnavailable) as exc_info:
        await parse_pdf_ocr_candidate(
            b"%PDF- synthetic",
            pdf_text="",
            worker=_successful_ocr_worker,
        )

    assert exc_info.value.code == "ocr_platform_unsupported"


def test_pdf_ocr_admission_limit_is_typed_environment_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALINEA_PDF_OCR_MAX_CONCURRENCY", "2")

    settings = CoreSettings(_env_file=None)

    assert settings.alinea_pdf_ocr_max_concurrency == 2


def test_normal_pdf_candidate_parses_only_inside_resource_limited_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()

    def parent_parse_forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("normal PDF parsing must not execute in the parent worker")

    monkeypatch.setattr(source_candidate_module, "parse_pdf", parent_parse_forbidden)

    candidate = parse_pdf_candidate(data, pdf_text="")

    assert candidate.report.accepted
    assert isinstance(candidate.parsed, ParsedPdfDocument)
    assert candidate.parsed.stats["ocr"] is False
    assert candidate.parsed.stats["extracted_chars"] > 0


def test_normal_pdf_candidate_unexpected_child_result_is_pdf_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        source_candidate_module,
        "_run_isolated_pdf_ocr",
        lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(CandidateUnavailable) as exc_info:
        parse_pdf_candidate(b"%PDF- synthetic", pdf_text="")

    assert exc_info.value.source_format == "pdf"
    assert exc_info.value.code == "pdf_lifecycle"


async def test_async_normal_pdf_cancellation_terminates_and_reaps_child() -> None:
    before = {process.pid for process in mp.active_children() if process.pid is not None}
    task = asyncio.create_task(
        parse_pdf_candidate_async(
            b"%PDF- synthetic",
            pdf_text="",
            timeout_s=5.0,
            worker=_sleeping_ocr_worker,
        )
    )
    await asyncio.sleep(0.1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _new_child_pids(before) == set()


async def test_pdf_text_evidence_uses_count_only_protocol() -> None:
    data = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()

    evidence = await count_pdf_text_evidence_isolated(data)

    assert evidence.pages == 2
    assert evidence.extracted_chars > 100
    assert not hasattr(evidence, "text")


async def test_pdf_text_evidence_cancellation_terminates_and_reaps_child() -> None:
    before = {process.pid for process in mp.active_children() if process.pid is not None}
    task = asyncio.create_task(
        count_pdf_text_evidence_isolated(
            b"%PDF- synthetic",
            timeout_s=5.0,
            worker=_sleeping_pdf_evidence_worker,
        )
    )
    await asyncio.sleep(0.1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _new_child_pids(before) == set()
