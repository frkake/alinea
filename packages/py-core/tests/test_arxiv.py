"""PY-ING-03: arXiv 解決の判定部(plans/05 §3)。

- ID/URL 正規化(新旧形式・URL 全パターン。§3.1)
- LaTeX ソース有無判定(e-print の content-type。§3.4)
- ライセンス URL 正規化(§3.3)
- メタデータ(Atom)+ ライセンス(OAI-PMH)取得(§3.2/§3.3)

外部ネットワークには一切接続しない。arXiv 系ホストは starlette の ASGI スタブ +
httpx.ASGITransport で決定的に差し替える(packages/llm のモックには依存しない)。
Redis も同ファイル内の in-memory フェイクで置換する。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from alinea_core.arxiv.fetch import (
    FetchError,
    RedisLike,
    arxiv_throttle,
    fetch_pdf,
    probe_latex_available,
)
from alinea_core.arxiv.ids import ArxivId, normalize_arxiv_id, parse_arxiv_url
from alinea_core.arxiv.licenses import normalize_license_url
from alinea_core.arxiv.metadata import fetch_metadata
from alinea_core.settings import CoreSettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

# --------------------------------------------------------------------------- #
# ID / URL 正規化(§3.1)
# --------------------------------------------------------------------------- #

# (入力, 期待 id(バージョン抜き), 期待 version)
_VALID_IDS: list[tuple[str, str, int | None]] = [
    # plans の Step 1 の 4 ケース(PY-ING-03)
    ("https://arxiv.org/abs/2209.03003", "2209.03003", None),
    ("arxiv.org/abs/2209.03003v2", "2209.03003", 2),
    ("2209.03003", "2209.03003", None),
    ("https://arxiv.org/pdf/2209.03003.pdf", "2209.03003", None),
    # abs — scheme / host / version / query / fragment の直積
    ("http://arxiv.org/abs/2209.03003", "2209.03003", None),
    ("https://www.arxiv.org/abs/2209.03003", "2209.03003", None),
    ("https://export.arxiv.org/abs/2209.03003v2", "2209.03003", 2),
    ("https://browse.arxiv.org/abs/2209.03003v11", "2209.03003", 11),
    ("https://arxiv.org/abs/2209.03003v3?context=cs.LG", "2209.03003", 3),
    ("https://arxiv.org/abs/2209.03003#S1", "2209.03003", None),
    # pdf
    ("https://arxiv.org/pdf/2209.03003", "2209.03003", None),
    ("https://arxiv.org/pdf/2209.03003v2.pdf", "2209.03003", 2),
    ("https://arxiv.org/pdf/2209.03003v2", "2209.03003", 2),
    # html / e-print / format
    ("https://arxiv.org/html/2209.03003v1", "2209.03003", 1),
    ("https://arxiv.org/e-print/2209.03003v3", "2209.03003", 3),
    ("https://arxiv.org/format/2209.03003", "2209.03003", None),
    # ar5iv ミラー
    ("https://ar5iv.labs.arxiv.org/html/2209.03003", "2209.03003", None),
    ("https://ar5iv.org/abs/2209.03003v2", "2209.03003", 2),
    # テキスト形式 "arXiv:..."(大文字小文字を問わない)
    ("arXiv:2209.03003v3", "2209.03003", 3),
    ("ARXIV:2209.03003", "2209.03003", None),
    # 5 桁 ID(2015-01 以降)
    ("2501.01234", "2501.01234", None),
    # 旧形式
    ("cs/9901002", "cs/9901002", None),
    ("math.GT/0309136", "math.GT/0309136", None),
    ("cond-mat/0207270", "cond-mat/0207270", None),
    ("https://arxiv.org/abs/math.GT/0309136", "math.GT/0309136", None),
    ("arXiv:cs/9901002v2", "cs/9901002", 2),
]


@pytest.mark.parametrize(
    "inp",
    [
        "https://arxiv.org/abs/2209.03003",
        "arxiv.org/abs/2209.03003v2",
        "2209.03003",
        "https://arxiv.org/pdf/2209.03003.pdf",
    ],
)
def test_normalize_arxiv_id(inp: str) -> None:
    """plans の Step 1 の逐語テスト(PY-ING-03)。"""
    assert normalize_arxiv_id(inp).id == "2209.03003"


@pytest.mark.parametrize(("inp", "expect_id", "expect_ver"), _VALID_IDS)
def test_normalize_arxiv_id_full(inp: str, expect_id: str, expect_ver: int | None) -> None:
    ref = normalize_arxiv_id(inp)
    assert ref.id == expect_id
    assert ref.version == expect_ver


def test_versioned_and_suffix() -> None:
    assert normalize_arxiv_id("2209.03003v3").versioned == "2209.03003v3"
    assert normalize_arxiv_id("2209.03003v3").version_suffix == "v3"
    assert normalize_arxiv_id("2209.03003").versioned == "2209.03003"
    assert normalize_arxiv_id("2209.03003").version_suffix == ""
    assert normalize_arxiv_id("2209.03003").arxiv_id == "2209.03003"


@pytest.mark.parametrize(
    "inp",
    [
        "",
        "   ",
        "not-an-id",
        "2209",
        "https://example.com/abs/2209.03003",
        "https://arxiv.org/abs/",
        "https://arxiv.org/abs/2209.03003.tar.gz",
        "arXiv:foo/bar",
    ],
)
def test_parse_arxiv_url_rejects(inp: str) -> None:
    assert parse_arxiv_url(inp) is None
    with pytest.raises(ValueError, match="arxiv"):
        normalize_arxiv_id(inp)


# --------------------------------------------------------------------------- #
# ライセンス URL 正規化(§3.3)
# --------------------------------------------------------------------------- #

_LICENSE_CASES: list[tuple[str | None, str]] = [
    ("http://creativecommons.org/licenses/by/4.0/", "cc-by-4.0"),
    ("https://creativecommons.org/licenses/by/4.0", "cc-by-4.0"),  # scheme/末尾スラッシュ非依存
    ("http://creativecommons.org/licenses/by-sa/4.0/", "cc-by-sa-4.0"),
    ("http://creativecommons.org/licenses/by-nc/4.0/", "cc-by-nc-4.0"),
    ("http://creativecommons.org/licenses/by-nc-sa/4.0/", "cc-by-nc-sa-4.0"),
    ("http://creativecommons.org/licenses/by-nd/4.0/", "cc-by-nd-4.0"),
    ("http://creativecommons.org/licenses/by-nc-nd/4.0/", "cc-by-nc-nd-4.0"),
    ("https://creativecommons.org/publicdomain/zero/1.0/", "cc0"),
    ("http://arxiv.org/licenses/nonexclusive-distrib/1.0/", "arxiv-nonexclusive"),
    # 未対応・取得失敗は unknown(安全側)
    ("http://creativecommons.org/licenses/by/3.0/", "unknown"),
    ("http://creativecommons.org/licenses/by-sa/2.5/", "unknown"),
    ("http://arxiv.org/licenses/assumed-1991-2003/", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
    ("garbage", "unknown"),
]


@pytest.mark.parametrize(("url", "expected"), _LICENSE_CASES)
def test_normalize_license_url(url: str | None, expected: str) -> None:
    assert normalize_license_url(url) == expected


# --------------------------------------------------------------------------- #
# ASGI スタブ + フェイク Redis
# --------------------------------------------------------------------------- #

_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2209.03003v3</id>
    <published>2022-09-07T13:00:00Z</published>
    <title>Flow Straight and Fast:
      Learning to Generate and Transfer Data with Rectified Flow</title>
    <summary>  We present rectified flow, a surprisingly simple approach.  </summary>
    <author><name>Xingchao Liu</name></author>
    <author><name>Chengyue Gong</name></author>
    <author><name>Qiang Liu</name></author>
    <arxiv:doi>10.48550/arXiv.2209.03003</arxiv:doi>
    <arxiv:comment>ICLR 2023 (spotlight)</arxiv:comment>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <category term="stat.ML"/>
  </entry>
</feed>
"""

_ATOM_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""

_OAI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <GetRecord><record><metadata>
    <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
      <id>2209.03003</id>
      <license>http://creativecommons.org/licenses/by/4.0/</license>
    </arXiv>
  </metadata></record></GetRecord>
</OAI-PMH>
"""


async def _query(request: Request) -> Response:
    id_list = request.query_params.get("id_list", "")
    if "0000.00000" in id_list:
        return Response(_ATOM_EMPTY, media_type="application/atom+xml")
    return Response(_ATOM_XML, media_type="application/atom+xml")


async def _oai2(request: Request) -> Response:
    return Response(_OAI_XML, media_type="text/xml")


async def _eprint(request: Request) -> Response:
    arxiv_id = request.path_params["arxiv_id"]
    if "2000.00002" in arxiv_id:  # ソース無し
        return Response(status_code=404)
    if "2000.00001" in arxiv_id:  # PDF-only 投稿
        return Response(b"", media_type="application/pdf")
    return Response(b"", media_type="application/x-eprint-tar")  # LaTeX ソースあり


def _make_stub() -> Starlette:
    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", _eprint, methods=["GET"]),
        ]
    )


class FakeRedis:
    """in-memory の最小 Redis(get / set NX PX EX / aclose)。TTL は無視する。"""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, name: str) -> bytes | None:
        return self._store.get(name)

    async def set(
        self,
        name: str,
        value: bytes,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and name in self._store:
            return None
        self._store[name] = value
        return True

    async def aclose(self) -> None:
        return None


async def _noop_throttle(redis: RedisLike) -> None:
    return None


class _RecordingByteStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: list[bytes],
        *,
        read_error: httpx.HTTPError | None = None,
    ) -> None:
        self.chunks = chunks
        self.read_error = read_error
        self.iterated = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterated = True
        for chunk in self.chunks:
            yield chunk
        if self.read_error is not None:
            raise self.read_error

    async def aclose(self) -> None:
        return None


@pytest.fixture
def settings() -> CoreSettings:
    # ASGITransport はホストを無視しパスでルーティングするため任意の URL でよい
    return CoreSettings(alinea_arxiv_base_url="http://arxiv.test")


@pytest_asyncio.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_make_stub())
    async with httpx.AsyncClient(transport=transport, base_url="http://arxiv.test") as client:
        yield client


@pytest.fixture
def redis() -> RedisLike:
    return FakeRedis()


# --------------------------------------------------------------------------- #
# PDF ストリーム上限・通信失敗
# --------------------------------------------------------------------------- #


async def test_fetch_pdf_rejects_declared_oversize_before_stream_read(
    settings: CoreSettings,
) -> None:
    stream = _RecordingByteStream([b"%PDF-small"])

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "9"},
            stream=stream,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(FetchError) as caught:
            await fetch_pdf(
                ArxivId("2401.00001", 1),
                http=client,
                settings=settings,
                max_bytes=8,
            )

    assert caught.value.kind == "payload_too_large"
    assert stream.iterated is False


async def test_fetch_pdf_rejects_actual_oversize_when_content_length_lies(
    settings: CoreSettings,
) -> None:
    stream = _RecordingByteStream([b"%PDF-", b"payload-over-limit"])

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "5"},
            stream=stream,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(FetchError) as caught:
            await fetch_pdf(
                ArxivId("2401.00001", 1),
                http=client,
                settings=settings,
                max_bytes=8,
            )

    assert caught.value.kind == "payload_too_large"
    assert stream.iterated is True


async def test_fetch_pdf_maps_stream_read_error_to_network_error(
    settings: CoreSettings,
) -> None:
    request = httpx.Request("GET", "http://arxiv.test/pdf/2401.00001v1")
    stream = _RecordingByteStream(
        [b"%PDF-"],
        read_error=httpx.ReadError("stream failed", request=request),
    )

    def respond(response_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=response_request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(FetchError) as caught:
            await fetch_pdf(
                ArxivId("2401.00001", 1),
                http=client,
                settings=settings,
                max_bytes=32,
            )

    assert caught.value.kind == "network_error"
    assert stream.iterated is True


# --------------------------------------------------------------------------- #
# LaTeX 有無判定(§3.4)
# --------------------------------------------------------------------------- #


async def test_probe_latex_available_true(
    settings: CoreSettings, http: httpx.AsyncClient, redis: RedisLike
) -> None:
    ref = ArxivId("2209.03003", 3)
    ok = await probe_latex_available(
        ref, redis=redis, http=http, settings=settings, throttle=_noop_throttle
    )
    assert ok is True


async def test_probe_latex_available_pdf_only(
    settings: CoreSettings, http: httpx.AsyncClient, redis: RedisLike
) -> None:
    ref = ArxivId("2000.00001", None)
    ok = await probe_latex_available(
        ref, redis=redis, http=http, settings=settings, throttle=_noop_throttle
    )
    assert ok is False


async def test_probe_latex_available_not_found(
    settings: CoreSettings, http: httpx.AsyncClient, redis: RedisLike
) -> None:
    ref = ArxivId("2000.00002", None)
    ok = await probe_latex_available(
        ref, redis=redis, http=http, settings=settings, throttle=_noop_throttle
    )
    assert ok is False


async def test_probe_latex_uses_cache(
    settings: CoreSettings, http: httpx.AsyncClient, redis: RedisLike
) -> None:
    ref = ArxivId("2209.03003", 3)
    # 先にキャッシュへ「0」を仕込むと HTTP を叩かず False を返す
    await redis.set("ingest:latex:2209.03003:3", b"0", ex=10)
    ok = await probe_latex_available(
        ref, redis=redis, http=http, settings=settings, throttle=_noop_throttle
    )
    assert ok is False
    # 2 回目呼び出し後もキャッシュ値が保持される
    assert await redis.get("ingest:latex:2209.03003:3") == b"0"


async def test_probe_latex_writes_cache(
    settings: CoreSettings, http: httpx.AsyncClient, redis: RedisLike
) -> None:
    ref = ArxivId("2209.03003", None)
    await probe_latex_available(
        ref, redis=redis, http=http, settings=settings, throttle=_noop_throttle
    )
    assert await redis.get("ingest:latex:2209.03003:latest") == b"1"


async def test_arxiv_throttle_acquires_free_lock(redis: RedisLike) -> None:
    # 空きロックは即座に取得でき、スピンしない
    await arxiv_throttle(redis, interval_ms=3100, sleep_ms=1)
    assert await redis.get("arxiv:throttle") == b"1"


# --------------------------------------------------------------------------- #
# メタデータ + ライセンス(§3.2 / §3.3)
# --------------------------------------------------------------------------- #


async def test_fetch_metadata(settings: CoreSettings, http: httpx.AsyncClient) -> None:
    ref = ArxivId("2209.03003", 3)
    meta = await fetch_metadata(ref, http=http, settings=settings)
    assert meta.arxiv_id == "2209.03003"
    assert meta.title == (
        "Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow"
    )
    assert meta.authors == [
        {"name": "Xingchao Liu"},
        {"name": "Chengyue Gong"},
        {"name": "Qiang Liu"},
    ]
    assert meta.abstract == "We present rectified flow, a surprisingly simple approach."
    assert meta.published_on == "2022-09-07"
    assert meta.arxiv_categories == ["cs.LG", "stat.ML"]  # primary 先頭・重複除去
    assert meta.doi == "10.48550/arXiv.2209.03003"
    assert meta.venue == "ICLR 2023"  # comment フォールバック(§3.2.1)
    assert meta.latest_version == "v3"
    assert meta.license == "cc-by-4.0"  # OAI-PMH 経由(§3.3)


async def test_fetch_metadata_not_found(settings: CoreSettings, http: httpx.AsyncClient) -> None:
    from alinea_core.arxiv.fetch import FetchError

    ref = ArxivId("0000.00000", None)
    with pytest.raises(FetchError) as exc:
        await fetch_metadata(ref, http=http, settings=settings)
    assert exc.value.kind == "source_not_found"
