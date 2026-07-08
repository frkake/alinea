"""arXiv 取得(e-print / HTTP クライアント)とレート制限(plans/05 §3.4・§3.5)。

- `make_arxiv_client`: ARXIV_USER_AGENT ヘッダ付き httpx クライアント(注入可能)。
- `arxiv_throttle`: arXiv 系ホストへの全リクエストを全ワーカー横断で 1req/3.1s に制限
  (Redis の SET NX PX スピン。docs/09 §5.3)。
- `probe_latex_available`: e-print の content-type で LaTeX ソース有無を判定し
  (=「品質レベル A 見込み」)、結果を Redis に 24h キャッシュ(plans/03 §3.1)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

import httpx

from yakudoku_core.arxiv.ids import ArxivId, eprint_url, pdf_url
from yakudoku_core.settings import CoreSettings, get_settings

_THROTTLE_KEY = "arxiv:throttle"


@runtime_checkable
class RedisLike(Protocol):
    """probe/throttle が使う Redis 操作の最小インターフェース(注入可能にするため)。"""

    async def get(self, name: str) -> bytes | None: ...

    async def set(
        self,
        name: str,
        value: bytes,
        *,
        ex: int | None = ...,
        px: int | None = ...,
        nx: bool = ...,
    ) -> bool | None: ...

    async def aclose(self) -> None: ...


Throttle = Callable[[RedisLike], Awaitable[None]]


class FetchError(Exception):
    """取得失敗。`kind` は plans/05 §2.4 の Problem code(リトライ分類の判定元)。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def make_arxiv_client(settings: CoreSettings | None = None) -> httpx.AsyncClient:
    """ARXIV_USER_AGENT 付きの httpx.AsyncClient を生成する(plans/01 §8.4)。

    base_url は付与しない(呼び出し側が絶対 URL を渡す。YAKUDOKU_ARXIV_BASE_URL の
    上書きは ids の URL ビルダが吸収する)。プロキシ設定は環境に委ねる(trust_env 既定)。
    """
    s = settings or get_settings()
    return httpx.AsyncClient(
        headers={"User-Agent": s.arxiv_user_agent},
        timeout=httpx.Timeout(30.0, connect=5.0),
        follow_redirects=True,
    )


def _make_redis(settings: CoreSettings) -> RedisLike:
    import redis.asyncio as redis_asyncio

    # redis-py の from_url は型注釈が無い(no-untyped-call)。戻り値は RedisLike に合致する。
    client: RedisLike = redis_asyncio.from_url(settings.redis_url)  # type: ignore[no-untyped-call]
    return client


async def arxiv_throttle(redis: RedisLike, *, interval_ms: int = 3100, sleep_ms: int = 200) -> None:
    """arXiv 系ホストへのアクセス間隔を 1req/interval に制限する(§3.5)。

    `SET arxiv:throttle 1 NX PX interval` をスピンで取得する。取得失敗時は sleep_ms
    スリープして再試行。スピンの打ち切りは呼び出し側(arq のジョブタイムアウト)に委ねる。
    """
    while True:
        acquired = await redis.set(_THROTTLE_KEY, b"1", nx=True, px=interval_ms)
        if acquired:
            return
        await asyncio.sleep(sleep_ms / 1000.0)


async def _head_eprint(http: httpx.AsyncClient, ref: ArxivId, base_url: str | None) -> bool:
    """e-print を HEAD し、PDF-only 投稿でなければ True(LaTeX ソースあり)。"""
    resp = await http.head(eprint_url(ref, base_url), follow_redirects=True, timeout=6.0)
    content_type = resp.headers.get("content-type", "")
    return resp.status_code == 200 and "application/pdf" not in content_type


async def fetch_pdf(
    ref: ArxivId,
    *,
    http: httpx.AsyncClient | None = None,
    settings: CoreSettings | None = None,
    max_bytes: int = 50 * 1024 * 1024,
) -> bytes:
    """arXiv の PDF を取得する。解析とは独立した即時表示用にも使う。"""
    s = settings or get_settings()
    base_url = s.yakudoku_arxiv_base_url or None
    url = pdf_url(ref, base_url)
    owns_http = http is None
    if http is None:
        http = make_arxiv_client(s)
    try:
        try:
            resp = await http.get(url, timeout=httpx.Timeout(120.0, connect=5.0))
        except httpx.HTTPError as exc:
            raise FetchError("network_error", f"arxiv pdf fetch failed: {exc}") from exc
        if resp.status_code == 404:
            raise FetchError("source_not_found", f"arxiv pdf 404: {url}")
        if resp.status_code >= 500:
            raise FetchError("upstream_5xx", f"arxiv pdf {resp.status_code}")
        if resp.status_code != 200:
            raise FetchError("source_not_found", f"arxiv pdf {resp.status_code}: {url}")
        data = resp.content
        if len(data) > max_bytes:
            raise FetchError("payload_too_large", "arxiv pdf exceeds size limit")
        if not data.startswith(b"%PDF-"):
            raise FetchError("source_not_found", "arxiv pdf response is not a PDF")
        return data
    finally:
        if owns_http:
            await http.aclose()


async def probe_latex_available(
    ref: ArxivId,
    *,
    redis: RedisLike | None = None,
    http: httpx.AsyncClient | None = None,
    settings: CoreSettings | None = None,
    throttle: Throttle = arxiv_throttle,
) -> bool:
    """LaTeX ソースの有無を判定する(§3.4)。結果は Redis に 24h キャッシュする。

    redis / http は注入可能。未指定なら設定から生成する(生成した場合は本関数内で閉じる)。
    """
    s = settings or get_settings()
    key = f"ingest:latex:{ref.id}:{ref.version if ref.version is not None else 'latest'}"
    owns_redis = redis is None
    r: RedisLike = _make_redis(s) if redis is None else redis
    try:
        cached = await r.get(key)
        if cached is not None:
            return cached == b"1"
        await throttle(r)
        base_url = s.yakudoku_arxiv_base_url or None
        if http is None:
            async with make_arxiv_client(s) as client:
                ok = await _head_eprint(client, ref, base_url)
        else:
            ok = await _head_eprint(http, ref, base_url)
        await r.set(key, b"1" if ok else b"0", ex=86_400)
        return ok
    finally:
        if owns_redis:
            await r.aclose()
