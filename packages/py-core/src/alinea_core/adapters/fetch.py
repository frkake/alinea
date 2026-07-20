"""サイトアダプタ用の境界付き HTTP クライアント(S8。SSRF 対策付き)。

:mod:`alinea_core.adapters` の純粋アダプタ(URL 検出・メタ写像)に対し、本モジュールは
「landing HTML」と「本文 PDF」を **安全に** 取得する副作用層を担う。arXiv 取得
(:mod:`alinea_core.arxiv.fetch`・:mod:`alinea_core.arxiv.limits`)と同じ最大バイト数・
timeout・Content-Type 検証を再利用し、加えて以下の SSRF 防御を行う:

- **ホスト allow-list**: アダプタが ``landing_url`` / ``pdf_url`` で宣言したホストだけを許可する
  (それ以外のホストへは一切リクエストしない)。
- **リダイレクト後の再検証**: 自動リダイレクトを無効化し、各ホップの遷移先ホストを
  allow-list に照らして手動で辿る(``http(s)`` スキームのみ)。
- **バイト上限・timeout・Content-Type**: HTML は ``text/html``、PDF は ``%PDF-`` マジック
  および ``application/pdf`` を要求し、:func:`read_bounded_http_body` でストリーム読取中に
  上限を強制する。

ネットワーク非依存のテストは httpx.ASGITransport / MockTransport を注入して決定的にする。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from alinea_core.arxiv.limits import (
    MAX_ARXIV_HTML_BYTES,
    MAX_ARXIV_PDF_BYTES,
    HttpSourceTooLargeError,
    read_bounded_http_body,
)
from alinea_core.settings import CoreSettings, get_settings

if TYPE_CHECKING:
    from alinea_core.adapters.base import SiteAdapter, SiteRef
    from alinea_core.adapters.openreview import OpenReviewAdapter

# HTML / PDF の最大バイト数は arXiv 取得と同じ上限を再利用する(§limits)。
MAX_SITE_HTML_BYTES = MAX_ARXIV_HTML_BYTES
MAX_SITE_PDF_BYTES = MAX_ARXIV_PDF_BYTES
_HTML_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_PDF_TIMEOUT = httpx.Timeout(120.0, connect=5.0)
_MAX_REDIRECTS = 5
_PDF_MAGIC = b"%PDF-"


class SiteFetchError(Exception):
    """サイト取得失敗。``kind`` は plans/05 §2.4 の Problem code(リトライ分類の判定元)。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def adapter_allowed_hosts(adapter: SiteAdapter, ref: SiteRef) -> frozenset[str]:
    """アダプタが宣言する landing / pdf URL のホスト集合(allow-list)。

    アダプタは純粋なので ``landing_url`` / ``pdf_url`` のホストが「そのサイトの正規ホスト」を
    表す。ここから allow-list を導出し、実際の取得はこの集合に含まれるホストへのみ許可する。
    """

    hosts: set[str] = set()
    candidates = [adapter.landing_url(ref)]
    pdf = adapter.pdf_url(ref)
    if pdf is not None:
        candidates.append(pdf)
    for url in candidates:
        host = _host_of(url)
        if host is not None:
            hosts.add(host)
    if not hosts:
        raise SiteFetchError("source_not_found", "adapter declared no fetchable host")
    return frozenset(hosts)


def _host_of(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname
    return host.lower() if host else None


def _validate_host(url: str, allowed_hosts: frozenset[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SiteFetchError("source_not_found", f"disallowed url scheme: {parts.scheme!r}")
    host = parts.hostname
    if host is None or host.lower() not in allowed_hosts:
        raise SiteFetchError("source_not_found", f"host not in allow-list: {host!r}")


def make_site_client(settings: CoreSettings | None = None) -> httpx.AsyncClient:
    """User-Agent 付き httpx クライアント。リダイレクトは手動追跡のため無効化する。"""

    s = settings or get_settings()
    return httpx.AsyncClient(
        headers={"User-Agent": s.arxiv_user_agent},
        timeout=_HTML_TIMEOUT,
        follow_redirects=False,
    )


async def _request_following_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    allowed_hosts: frozenset[str],
    timeout_config: httpx.Timeout,
) -> httpx.Response:
    """allow-list を各ホップで再検証しながら手動でリダイレクトを辿る。

    自動リダイレクト(``follow_redirects``)ではホップ先ホストを検証できないため、
    ``follow_redirects=False`` のクライアントで 3xx を手動追跡し、``Location`` の遷移先
    ホストを毎回 allow-list に照らす(SSRF: 許可外ホストへは決して遷移しない)。
    レスポンス本文は呼び出し側が ``read_bounded_http_body`` で境界読取する前提で、
    ここではストリーム応答(未読)を返す。
    """

    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        _validate_host(current, allowed_hosts)
        try:
            response_context = client.stream("GET", current, timeout=timeout_config)
            resp = await response_context.__aenter__()
        except httpx.HTTPError as exc:
            raise SiteFetchError("network_error", f"site request failed: {exc}") from exc

        if resp.is_redirect:
            location = resp.headers.get("location")
            await response_context.__aexit__(None, None, None)
            if not location:
                raise SiteFetchError("source_not_found", "redirect without Location header")
            # 相対 Location は現在の URL を基準に絶対化する。
            current = str(httpx.URL(current).join(location))
            continue

        # 非リダイレクト応答: 呼び出し側が本文を読むため context を閉じずに返す。
        resp._alinea_ctx = response_context  # type: ignore[attr-defined]
        return resp

    raise SiteFetchError("network_error", "too many redirects")


async def _read_and_close(resp: httpx.Response, *, max_bytes: int, too_large_kind: str) -> bytes:
    context = getattr(resp, "_alinea_ctx", None)
    try:
        try:
            return await read_bounded_http_body(resp, max_bytes=max_bytes)
        except HttpSourceTooLargeError as exc:
            raise SiteFetchError(too_large_kind, "site source exceeds size limit") from exc
        except httpx.HTTPError as exc:
            raise SiteFetchError("network_error", f"site read failed: {exc}") from exc
    finally:
        if context is not None:
            await context.__aexit__(None, None, None)


def _status_error(resp: httpx.Response) -> SiteFetchError:
    code = resp.status_code
    if code == 429:
        return SiteFetchError("rate_limited", f"site returned {code}")
    if code == 408:
        return SiteFetchError("network_error", f"site returned {code}")
    if code == 404:
        return SiteFetchError("source_not_found", f"site returned {code}")
    if code >= 500:
        return SiteFetchError("upstream_5xx", f"site returned {code}")
    return SiteFetchError("source_not_found", f"site returned {code}")


async def fetch_html(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    settings: CoreSettings | None = None,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = MAX_SITE_HTML_BYTES,
) -> str:
    """landing HTML を allow-list・上限・Content-Type 検証付きで取得する。"""

    s = settings or get_settings()
    owns = client is None
    http = client or make_site_client(s)
    try:
        resp = await _request_following_redirects(
            http,
            url,
            allowed_hosts=allowed_hosts,
            timeout_config=_HTML_TIMEOUT,
        )
        if resp.status_code != 200:
            ctx = getattr(resp, "_alinea_ctx", None)
            if ctx is not None:
                await ctx.__aexit__(None, None, None)
            raise _status_error(resp)
        content_type = resp.headers.get("content-type", "").lower()
        if "html" not in content_type:
            ctx = getattr(resp, "_alinea_ctx", None)
            if ctx is not None:
                await ctx.__aexit__(None, None, None)
            raise SiteFetchError("source_not_found", f"landing is not HTML: {content_type!r}")
        data = await _read_and_close(resp, max_bytes=max_bytes, too_large_kind="source_too_large")
        return data.decode("utf-8", errors="replace")
    finally:
        if owns:
            await http.aclose()


async def fetch_pdf(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    settings: CoreSettings | None = None,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = MAX_SITE_PDF_BYTES,
) -> bytes:
    """本文 PDF を allow-list・上限・Content-Type/マジック検証付きで取得する。"""

    s = settings or get_settings()
    owns = client is None
    http = client or make_site_client(s)
    try:
        resp = await _request_following_redirects(
            http,
            url,
            allowed_hosts=allowed_hosts,
            timeout_config=_PDF_TIMEOUT,
        )
        if resp.status_code != 200:
            ctx = getattr(resp, "_alinea_ctx", None)
            if ctx is not None:
                await ctx.__aexit__(None, None, None)
            raise _status_error(resp)
        content_type = resp.headers.get("content-type", "").lower()
        # Content-Type が pdf でなくてもマジックで最終判定する(サイトが octet-stream を返す例あり)。
        data = await _read_and_close(resp, max_bytes=max_bytes, too_large_kind="source_too_large")
        if not data[:1024].lstrip().startswith(_PDF_MAGIC):
            raise SiteFetchError(
                "source_not_found",
                f"fetched source is not a PDF (content-type={content_type!r})",
            )
        return data
    finally:
        if owns:
            await http.aclose()


async def fetch_note(
    adapter: OpenReviewAdapter,
    ref: SiteRef,
    *,
    settings: CoreSettings | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, object] | None:
    """OpenReview API2 note を取得して最初の note オブジェクトを返す。

    ``GET https://openreview.net/api2/notes?id={external_id}`` は openreview.net 上にあり、
    adapter_allowed_hosts の allow-list 内に収まる。JSON を解析し ``notes`` 配列の先頭を返す。
    404・403・notes=[] の場合は ``None`` を返す(Citation メタへのフォールバックを指示)。

    :param adapter: OpenReviewAdapter インスタンス。
    :param ref: 対象論文の SiteRef。
    :param settings: CoreSettings(省略時は get_settings())。
    :param client: 注入する httpx.AsyncClient(省略時は make_site_client() を生成・破棄)。
    """
    import json

    s = settings or get_settings()
    owns = client is None
    http = client or make_site_client(s)
    allowed = adapter_allowed_hosts(adapter, ref)
    url = adapter.api2_note_url(ref)
    try:
        resp = await _request_following_redirects(
            http, url, allowed_hosts=allowed, timeout_config=_HTML_TIMEOUT
        )
        status = resp.status_code
        ctx = getattr(resp, "_alinea_ctx", None)
        if status in (403, 404):
            if ctx is not None:
                await ctx.__aexit__(None, None, None)
            return None
        if status != 200:
            if ctx is not None:
                await ctx.__aexit__(None, None, None)
            return None
        raw = await _read_and_close(
            resp, max_bytes=MAX_SITE_HTML_BYTES, too_large_kind="source_too_large"
        )
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return None
        notes = payload.get("notes") if isinstance(payload, dict) else None
        if not isinstance(notes, list) or len(notes) == 0:
            return None
        note = notes[0]
        return dict(note) if isinstance(note, dict) else None
    except SiteFetchError:
        return None
    finally:
        if owns:
            await http.aclose()


__all__ = [
    "MAX_SITE_HTML_BYTES",
    "MAX_SITE_PDF_BYTES",
    "SiteFetchError",
    "adapter_allowed_hosts",
    "fetch_html",
    "fetch_note",
    "fetch_pdf",
    "make_site_client",
]
