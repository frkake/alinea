"""Generic in-memory bounds for retained arXiv source formats."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

MAX_ARXIV_PDF_BYTES = 128 * 1024 * 1024
MAX_ARXIV_EPRINT_BYTES = 128 * 1024 * 1024
MAX_ARXIV_HTML_BYTES = 64 * 1024 * 1024
HTTP_SOURCE_READ_CHUNK_BYTES = 64 * 1024

# Ranged-download tuning.  A truncating proxy tends to cut connections beyond a
# few MiB, so each Range request stays well under that; on repeated stalls the
# chunk size is halved (down to a floor) to slip under the proxy's threshold.
# ``429`` responses are retried a bounded number of times with a capped backoff.
RANGED_DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024
_RANGED_DOWNLOAD_MIN_CHUNK_BYTES = 256 * 1024
_RANGED_DOWNLOAD_MAX_STALLS = 20
_RANGED_DOWNLOAD_MAX_RATE_LIMITS = 20
_RANGED_DOWNLOAD_RATE_LIMIT_BACKOFF_S = 5.0
_RANGED_DOWNLOAD_MAX_BACKOFF_S = 30.0


class HttpSourceTooLargeError(Exception):
    """An HTTP source exceeded its caller-selected decoded-byte limit."""

    def __init__(self, *, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__("HTTP source exceeds bounded read limit")


class RangedDownloadUnsupportedError(Exception):
    """The server does not advertise byte-range support for this resource."""


class RangedDownloadFailedError(Exception):
    """A ranged download could not make progress (repeated stalls/limits)."""


def _content_length(headers: Any) -> int | None:
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(str(raw), 10)
    except ValueError:
        return None
    return value if value >= 0 else None


async def read_bounded_http_body(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Read a streamed decoded response with declared and actual-length checks."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    declared = _content_length(response.headers)
    if declared is not None and declared > max_bytes:
        raise HttpSourceTooLargeError(max_bytes=max_bytes)

    data = bytearray()
    chunk_size = min(HTTP_SOURCE_READ_CHUNK_BYTES, max_bytes + 1) or 1
    async for chunk in response.aiter_bytes(chunk_size=chunk_size):
        remaining = max_bytes + 1 - len(data)
        if remaining <= 0:
            raise HttpSourceTooLargeError(max_bytes=max_bytes)
        data.extend(chunk[:remaining])
        if len(data) > max_bytes or len(chunk) > remaining:
            raise HttpSourceTooLargeError(max_bytes=max_bytes)
    return bytes(data)


def _retry_after_seconds(headers: Any) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = int(str(raw).strip(), 10)
    except ValueError:
        return None
    if value < 0:
        return None
    return float(min(value, int(_RANGED_DOWNLOAD_MAX_BACKOFF_S)))


async def read_bounded_http_body_ranged(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    headers: dict[str, str] | None = None,
    chunk_bytes: int = RANGED_DOWNLOAD_CHUNK_BYTES,
    throttle: Callable[[], Awaitable[None]] | None = None,
) -> bytes:
    """Download ``url`` in bounded ``Range`` chunks, reassembling the full body.

    Recovers a resource that a proxy truncates on a single streamed read.  The
    server must advertise ``Accept-Ranges: bytes`` (checked via ``HEAD``);
    otherwise :class:`RangedDownloadUnsupportedError` is raised so the caller can
    keep its original failure.  Mid-chunk truncation resumes from the actual
    stopping point, and ``429`` responses are retried with a capped backoff.
    """

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")

    base_headers = dict(headers or {})

    if throttle is not None:
        await throttle()
    head = await client.head(url, headers=base_headers, timeout=httpx.Timeout(30.0, connect=5.0))
    if head.status_code != 200:
        raise RangedDownloadUnsupportedError(f"HEAD returned {head.status_code}")
    if "bytes" not in head.headers.get("accept-ranges", "").lower():
        raise RangedDownloadUnsupportedError("server does not advertise byte ranges")
    total = _content_length(head.headers)
    if total is None:
        raise RangedDownloadUnsupportedError("missing content-length for ranged download")
    if total > max_bytes:
        raise HttpSourceTooLargeError(max_bytes=max_bytes)

    data = bytearray()
    stalls = 0
    rate_limits = 0
    while len(data) < total:
        start = len(data)
        end = min(start + chunk_bytes - 1, total - 1)
        request_headers = {**base_headers, "Range": f"bytes={start}-{end}"}
        if throttle is not None:
            await throttle()
        try:
            response = await client.get(
                url,
                headers=request_headers,
                timeout=httpx.Timeout(60.0, connect=5.0),
            )
        except httpx.HTTPError:
            # The proxy cut this range too; shrink the window and retry so the
            # next request slips under its truncation threshold.
            stalls += 1
            chunk_bytes = max(_RANGED_DOWNLOAD_MIN_CHUNK_BYTES, chunk_bytes // 2)
            if stalls > _RANGED_DOWNLOAD_MAX_STALLS:
                raise RangedDownloadFailedError("ranged download stalled") from None
            await asyncio.sleep(
                min(_RANGED_DOWNLOAD_RATE_LIMIT_BACKOFF_S, _RANGED_DOWNLOAD_MAX_BACKOFF_S)
            )
            continue

        if response.status_code == 429:
            rate_limits += 1
            if rate_limits > _RANGED_DOWNLOAD_MAX_RATE_LIMITS:
                raise RangedDownloadFailedError("ranged download rate limited") from None
            await asyncio.sleep(
                _retry_after_seconds(response.headers) or _RANGED_DOWNLOAD_RATE_LIMIT_BACKOFF_S
            )
            continue
        if response.status_code not in (200, 206):
            raise RangedDownloadFailedError(f"unexpected status {response.status_code}")

        # A 200 (non-partial) response means the server ignored Range and sent
        # the whole body; take at most what fits and stop.
        if response.status_code == 200:
            allowed = total - len(data)
            data.extend(response.content[:allowed])
            break

        payload = response.content[: total - len(data)]
        if not payload:
            stalls += 1
            chunk_bytes = max(_RANGED_DOWNLOAD_MIN_CHUNK_BYTES, chunk_bytes // 2)
            if stalls > _RANGED_DOWNLOAD_MAX_STALLS:
                raise RangedDownloadFailedError("ranged download made no progress")
            await asyncio.sleep(
                min(_RANGED_DOWNLOAD_RATE_LIMIT_BACKOFF_S, _RANGED_DOWNLOAD_MAX_BACKOFF_S)
            )
            continue

        data.extend(payload)
        stalls = 0
        if len(data) > max_bytes:
            raise HttpSourceTooLargeError(max_bytes=max_bytes)

    return bytes(data)


__all__ = [
    "HTTP_SOURCE_READ_CHUNK_BYTES",
    "MAX_ARXIV_EPRINT_BYTES",
    "MAX_ARXIV_HTML_BYTES",
    "MAX_ARXIV_PDF_BYTES",
    "RANGED_DOWNLOAD_CHUNK_BYTES",
    "HttpSourceTooLargeError",
    "RangedDownloadFailedError",
    "RangedDownloadUnsupportedError",
    "read_bounded_http_body",
    "read_bounded_http_body_ranged",
]
