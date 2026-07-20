"""Bounded S3 reads used for integrity verification."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from alinea_core.storage.s3 import S3ObjectTooLargeError, S3Storage


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0
        self.read_sizes: list[int] = []

    async def __aenter__(self) -> _Body:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class _Client:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    async def get_object(self, **_kwargs: Any) -> dict[str, Any]:
        return self.response


class _ClientResponseBody:
    """aioboto/moto shape: sized reads live on ``content``, not ``read``."""

    def __init__(self, data: bytes) -> None:
        self.content = _Body(data)
        self.unbounded_read_called = False

    async def __aenter__(self) -> _ClientResponseBody:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def read(self) -> bytes:
        self.unbounded_read_called = True
        return self.content.data


def _storage(response: dict[str, Any]) -> S3Storage:
    storage = object.__new__(S3Storage)

    @asynccontextmanager
    async def client_ctx(**_kwargs: Any) -> AsyncIterator[_Client]:
        yield _Client(response)

    storage._client_ctx = client_ctx  # type: ignore[method-assign]
    return storage


async def test_get_bounded_rejects_declared_oversize_before_stream_read() -> None:
    body = _Body(b"oversized")
    storage = _storage({"ContentLength": 9, "Body": body})

    with pytest.raises(S3ObjectTooLargeError) as error:
        await storage.get_bounded("assets", "figure.png", max_bytes=8)

    assert error.value.max_bytes == 8
    assert body.read_sizes == []


async def test_get_bounded_enforces_max_plus_one_when_length_header_lies() -> None:
    body = _Body(b"123456")
    storage = _storage({"ContentLength": 1, "Body": body})

    with pytest.raises(S3ObjectTooLargeError) as error:
        await storage.get_bounded("assets", "figure.png", max_bytes=4)

    assert error.value.max_bytes == 4
    assert body.read_sizes
    assert max(body.read_sizes) <= 5


async def test_get_bounded_uses_client_response_content_reader() -> None:
    body = _ClientResponseBody(b"1234")
    storage = _storage({"ContentLength": 4, "Body": body})

    result = await storage.get_bounded("assets", "figure.png", max_bytes=4)

    assert result == b"1234"
    assert body.content.read_sizes
    assert body.unbounded_read_called is False
