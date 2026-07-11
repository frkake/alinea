"""Generic in-memory bounds for retained arXiv source formats."""

from __future__ import annotations

from typing import Any

import httpx

MAX_ARXIV_PDF_BYTES = 128 * 1024 * 1024
MAX_ARXIV_EPRINT_BYTES = 128 * 1024 * 1024
MAX_ARXIV_HTML_BYTES = 64 * 1024 * 1024
HTTP_SOURCE_READ_CHUNK_BYTES = 64 * 1024


class HttpSourceTooLargeError(Exception):
    """An HTTP source exceeded its caller-selected decoded-byte limit."""

    def __init__(self, *, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__("HTTP source exceeds bounded read limit")


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


__all__ = [
    "HTTP_SOURCE_READ_CHUNK_BYTES",
    "MAX_ARXIV_EPRINT_BYTES",
    "MAX_ARXIV_HTML_BYTES",
    "MAX_ARXIV_PDF_BYTES",
    "HttpSourceTooLargeError",
    "read_bounded_http_body",
]
