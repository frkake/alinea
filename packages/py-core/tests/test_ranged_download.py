"""Ranged-download fallback for proxies that truncate large bodies.

A corporate proxy can close a large streamed response early
(``httpx.RemoteProtocolError``).  ``read_bounded_http_body_ranged`` recovers
the full body with HTTP ``Range`` requests, tolerating both mid-chunk
truncation and transient ``429`` rate limiting.  Servers advertise support
with ``Accept-Ranges: bytes``.
"""

from __future__ import annotations

import httpx
import pytest
from alinea_core.arxiv.limits import (
    HttpSourceTooLargeError,
    RangedDownloadUnsupportedError,
    read_bounded_http_body_ranged,
)

BODY = bytes(range(256)) * 400  # 102_400 deterministic bytes


def _range_bounds(request: httpx.Request, total: int) -> tuple[int, int]:
    header = request.headers.get("range", "")
    assert header.startswith("bytes="), header
    start_s, _, end_s = header[len("bytes=") :].partition("-")
    start = int(start_s)
    end = int(end_s) if end_s else total - 1
    return start, min(end, total - 1)


async def test_ranged_download_reassembles_full_body() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"accept-ranges": "bytes", "content-length": str(len(BODY))},
            )
        start, end = _range_bounds(request, len(BODY))
        return httpx.Response(
            206,
            headers={"content-range": f"bytes {start}-{end}/{len(BODY)}"},
            content=BODY[start : end + 1],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        data = await read_bounded_http_body_ranged(
            client,
            "http://arxiv.test/e-print/x",
            max_bytes=len(BODY) * 2,
            chunk_bytes=16_384,
        )

    assert data == BODY


async def test_ranged_download_recovers_from_truncated_chunks() -> None:
    # The proxy returns fewer bytes than requested for the first response of
    # each range; the loop must resume from where it actually stopped.
    truncate_next = {"flag": True}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"accept-ranges": "bytes", "content-length": str(len(BODY))},
            )
        start, end = _range_bounds(request, len(BODY))
        payload = BODY[start : end + 1]
        if truncate_next["flag"] and len(payload) > 100:
            truncate_next["flag"] = False
            payload = payload[:100]  # short read (proxy cut the connection)
        return httpx.Response(
            206,
            headers={"content-range": f"bytes {start}-{start + len(payload) - 1}/{len(BODY)}"},
            content=payload,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        data = await read_bounded_http_body_ranged(
            client,
            "http://arxiv.test/e-print/x",
            max_bytes=len(BODY) * 2,
            chunk_bytes=16_384,
        )

    assert data == BODY


async def test_ranged_download_retries_on_rate_limit() -> None:
    seen = {"count": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={"accept-ranges": "bytes", "content-length": str(len(BODY))},
            )
        seen["count"] += 1
        if seen["count"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        start, end = _range_bounds(request, len(BODY))
        return httpx.Response(
            206,
            headers={"content-range": f"bytes {start}-{end}/{len(BODY)}"},
            content=BODY[start : end + 1],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        data = await read_bounded_http_body_ranged(
            client,
            "http://arxiv.test/e-print/x",
            max_bytes=len(BODY) * 2,
            chunk_bytes=len(BODY),
        )

    assert data == BODY


async def test_ranged_download_rejects_oversize_declared_length() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"accept-ranges": "bytes", "content-length": str(len(BODY))},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(HttpSourceTooLargeError):
            await read_bounded_http_body_ranged(
                client,
                "http://arxiv.test/e-print/x",
                max_bytes=len(BODY) - 1,
                chunk_bytes=16_384,
            )


async def test_ranged_download_unsupported_without_accept_ranges() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(len(BODY))})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RangedDownloadUnsupportedError):
            await read_bounded_http_body_ranged(
                client,
                "http://arxiv.test/e-print/x",
                max_bytes=len(BODY) * 2,
                chunk_bytes=16_384,
            )
