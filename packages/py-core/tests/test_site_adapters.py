"""他サイトアダプタ 純粋コアの単体テスト(S8 フェーズ1)。

URL 検出・citation_* メタ抽出・SiteMeta 写像・registry 解決を fixture 駆動で検証する。
外部ネットワークには一切接続しない(arXiv アダプタと同方針)。
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import httpx
import pytest
from alinea_core.adapters import (
    AclAnthologyAdapter,
    SiteFetchError,
    SiteRef,
    adapter_allowed_hosts,
    extract_citation_meta,
    fetch_html,
    fetch_pdf,
    normalize_scholar_author,
    resolve_adapter,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "acl_anthology_landing.html"


def _fixture_html() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# citation_* 汎用抽出
# --------------------------------------------------------------------------- #


def test_extract_citation_meta_from_fixture() -> None:
    meta = extract_citation_meta(_fixture_html())
    assert meta.title == (
        "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
    )
    assert meta.authors == [
        "Devlin, Jacob",
        "Chang, Ming-Wei",
        "Lee, Kenton",
        "Toutanova, Kristina",
    ]
    assert meta.publication_date == "2019/06"
    assert meta.conference_title is not None and "North American Chapter" in meta.conference_title
    assert meta.pdf_url == "https://aclanthology.org/N19-1423.pdf"
    assert meta.doi == "10.18653/v1/N19-1423"
    assert meta.abstract is not None and meta.abstract.startswith("We introduce a new language")


def test_extract_citation_meta_missing_fields() -> None:
    meta = extract_citation_meta("<html><head><title>x</title></head><body></body></html>")
    assert meta.title is None
    assert meta.authors == []
    assert meta.pdf_url is None
    assert meta.doi is None


def test_normalize_scholar_author() -> None:
    assert normalize_scholar_author("Devlin, Jacob") == "Jacob Devlin"
    assert normalize_scholar_author("Chang, Ming-Wei") == "Ming-Wei Chang"
    # 姓のみ / カンマなしはそのまま
    assert normalize_scholar_author("Aristotle") == "Aristotle"
    assert normalize_scholar_author("Jacob Devlin") == "Jacob Devlin"


# --------------------------------------------------------------------------- #
# ACL Anthology URL 検出
# --------------------------------------------------------------------------- #

_VALID_ACL = [
    ("https://aclanthology.org/2023.acl-long.123/", "2023.acl-long.123"),
    ("https://aclanthology.org/2023.acl-long.123", "2023.acl-long.123"),
    ("https://aclanthology.org/2023.acl-long.123.pdf", "2023.acl-long.123"),
    ("aclanthology.org/2023.acl-long.123/", "2023.acl-long.123"),
    ("http://aclanthology.org/2023.emnlp-main.45/", "2023.emnlp-main.45"),
    # 旧式 ID(2020 以前)
    ("https://aclanthology.org/N19-1423/", "N19-1423"),
    ("https://aclanthology.org/P19-1001.pdf", "P19-1001"),
]

_INVALID_ACL = [
    "https://aclanthology.org/volumes/2023.acl-long/",
    "https://aclanthology.org/events/acl-2023/",
    "https://arxiv.org/abs/2209.03003",
    "https://openreview.net/forum?id=abc",
    "2209.03003",
    "",
    "not a url",
]


def test_acl_match_valid() -> None:
    adapter = AclAnthologyAdapter()
    for url, external_id in _VALID_ACL:
        ref = adapter.match(url)
        assert ref is not None, url
        assert ref.site == "acl_anthology"
        assert ref.external_id == external_id, url


def test_acl_match_invalid() -> None:
    adapter = AclAnthologyAdapter()
    for url in _INVALID_ACL:
        assert adapter.match(url) is None, url


def test_acl_url_builders() -> None:
    adapter = AclAnthologyAdapter()
    ref = SiteRef(site="acl_anthology", external_id="2023.acl-long.123")
    assert adapter.pdf_url(ref) == "https://aclanthology.org/2023.acl-long.123.pdf"
    assert adapter.landing_url(ref) == "https://aclanthology.org/2023.acl-long.123/"


def test_acl_parse_metadata() -> None:
    adapter = AclAnthologyAdapter()
    ref = adapter.match("https://aclanthology.org/N19-1423/")
    assert ref is not None
    meta = adapter.parse_metadata(_fixture_html(), ref)
    assert meta.site == "acl_anthology"
    assert meta.external_id == "N19-1423"
    assert meta.title.startswith("BERT: Pre-training")
    assert meta.authors == [
        {"name": "Jacob Devlin"},
        {"name": "Ming-Wei Chang"},
        {"name": "Kenton Lee"},
        {"name": "Kristina Toutanova"},
    ]
    assert meta.abstract.startswith("We introduce a new language")
    assert meta.published_on == "2019-06-01"
    assert meta.venue is not None and "North American Chapter" in meta.venue
    assert meta.doi == "10.18653/v1/N19-1423"
    assert meta.pdf_url == "https://aclanthology.org/N19-1423.pdf"
    assert meta.license == "unknown"


# --------------------------------------------------------------------------- #
# registry 解決
# --------------------------------------------------------------------------- #


def test_resolve_adapter_acl() -> None:
    resolved = resolve_adapter("https://aclanthology.org/2023.acl-long.123/")
    assert resolved is not None
    adapter, ref = resolved
    assert adapter.site == "acl_anthology"
    assert ref.external_id == "2023.acl-long.123"


def test_resolve_adapter_none() -> None:
    assert resolve_adapter("https://arxiv.org/abs/2209.03003") is None
    assert resolve_adapter("https://example.com/paper") is None
    assert resolve_adapter("") is None


# --------------------------------------------------------------------------- #
# 境界付き HTTP クライアント + SSRF 対策(adapters/fetch.py)
# --------------------------------------------------------------------------- #

_ACL_HOSTS = frozenset({"aclanthology.org"})
_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def test_adapter_allowed_hosts_from_declared_urls() -> None:
    adapter = AclAnthologyAdapter()
    ref = SiteRef(site="acl_anthology", external_id="2023.acl-long.42")
    assert adapter_allowed_hosts(adapter, ref) == frozenset({"aclanthology.org"})


async def test_fetch_html_returns_landing_on_allowed_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>ok</body></html>",
                              headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        html = await fetch_html(
            "https://aclanthology.org/2023.acl-long.42/",
            allowed_hosts=_ACL_HOSTS,
            client=client,
        )
    finally:
        await client.aclose()
    assert "ok" in html


async def test_fetch_html_rejects_host_not_in_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("request should be blocked before sending")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SiteFetchError) as exc:
            await fetch_html(
                "https://evil.example/steal",
                allowed_hosts=_ACL_HOSTS,
                client=client,
            )
    finally:
        await client.aclose()
    assert exc.value.kind == "source_not_found"


async def test_fetch_pdf_revalidates_host_after_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "aclanthology.org":
            # allow-list 外ホストへ 302 リダイレクトする(SSRF 試行)。
            return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/meta"})
        raise AssertionError("must not follow redirect to disallowed host")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SiteFetchError) as exc:
            await fetch_pdf(
                "https://aclanthology.org/2023.acl-long.42.pdf",
                allowed_hosts=_ACL_HOSTS,
                client=client,
            )
    finally:
        await client.aclose()
    assert exc.value.kind == "source_not_found"


async def test_fetch_pdf_follows_redirect_within_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf") and "final" not in request.url.path:
            return httpx.Response(
                302, headers={"location": "https://aclanthology.org/final.pdf"}
            )
        return httpx.Response(
            200, content=_MINIMAL_PDF, headers={"content-type": "application/pdf"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        data = await fetch_pdf(
            "https://aclanthology.org/2023.acl-long.42.pdf",
            allowed_hosts=_ACL_HOSTS,
            client=client,
        )
    finally:
        await client.aclose()
    assert data.startswith(b"%PDF-")


async def test_fetch_pdf_rejects_non_pdf_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a pdf</html>",
                              headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SiteFetchError) as exc:
            await fetch_pdf(
                "https://aclanthology.org/2023.acl-long.42.pdf",
                allowed_hosts=_ACL_HOSTS,
                client=client,
            )
    finally:
        await client.aclose()
    assert exc.value.kind == "source_not_found"


# --------------------------------------------------------------------------- #
# OpenReview アダプタ
# --------------------------------------------------------------------------- #

_OR_FIXTURE = Path(__file__).parent / "fixtures" / "openreview_note.json"
_OR_HOSTS = frozenset({"openreview.net"})

_VALID_OR = [
    # forum URL → 同一 SiteRef
    ("https://openreview.net/forum?id=abc123XYZ", "abc123XYZ"),
    ("http://openreview.net/forum?id=abc123XYZ", "abc123XYZ"),
    # pdf URL → 同一 SiteRef
    ("https://openreview.net/pdf?id=abc123XYZ", "abc123XYZ"),
    # 特殊文字を含む ID (URL エンコード済み)
    ("https://openreview.net/forum?id=Abc_1-2%2F3", "Abc_1-2/3"),
]

_INVALID_OR = [
    "https://openreview.net/",                          # ルートだけ
    "https://openreview.net/group?id=ICLR.cc/2024",    # group URL
    "https://openreview.net/revisions?id=abc",          # revisions URL
    "https://aclanthology.org/2023.acl-long.123/",
    "https://arxiv.org/abs/2209.03003",
    "",
    "not a url",
]


# --------------------------------------------------------------------------- #
# PubMed / PMC アダプタ(Task 17)
# --------------------------------------------------------------------------- #

from alinea_core.adapters import (  # noqa: E402
    PmcAdapter,
    PubMedAdapter,
)

_VALID_PUBMED = [
    ("https://pubmed.ncbi.nlm.nih.gov/31000000/", "31000000"),
    ("https://pubmed.ncbi.nlm.nih.gov/31000000", "31000000"),
    ("http://www.ncbi.nlm.nih.gov/pubmed/31000000", "31000000"),
    ("pubmed.ncbi.nlm.nih.gov/31000000/", "31000000"),
]

_INVALID_PUBMED = [
    "https://pubmed.ncbi.nlm.nih.gov/",
    "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6543210/",
    "https://arxiv.org/abs/2209.03003",
    "",
    "not a url",
]


def test_openreview_match_valid() -> None:
    from alinea_core.adapters.openreview import OpenReviewAdapter

    adapter = OpenReviewAdapter()
    for url, external_id in _VALID_OR:
        ref = adapter.match(url)
        assert ref is not None, f"should match: {url}"
        assert ref.site == "openreview"
        assert ref.external_id == external_id, f"id mismatch for {url}"


def test_openreview_match_invalid() -> None:
    from alinea_core.adapters.openreview import OpenReviewAdapter

    adapter = OpenReviewAdapter()
    for url in _INVALID_OR:
        assert adapter.match(url) is None, f"should not match: {url}"


def test_openreview_forum_and_pdf_url_normalize_to_same_ref() -> None:
    """forum?id=X と /pdf?id=X は同一 SiteRef になる。"""
    from alinea_core.adapters.openreview import OpenReviewAdapter

    adapter = OpenReviewAdapter()
    forum_ref = adapter.match("https://openreview.net/forum?id=abc123XYZ")
    pdf_ref = adapter.match("https://openreview.net/pdf?id=abc123XYZ")
    assert forum_ref is not None
    assert pdf_ref is not None
    assert forum_ref == pdf_ref


def test_openreview_url_builders() -> None:
    from alinea_core.adapters.openreview import OpenReviewAdapter

    adapter = OpenReviewAdapter()
    ref = SiteRef(site="openreview", external_id="abc123XYZ")
    assert adapter.pdf_url(ref) == "https://openreview.net/pdf?id=abc123XYZ"
    assert adapter.landing_url(ref) == "https://openreview.net/forum?id=abc123XYZ"


def test_openreview_adapter_allowed_hosts() -> None:
    """アダプタ宣言ホストは openreview.net のみ(SSRF allow-list)。"""
    from alinea_core.adapters.openreview import OpenReviewAdapter

    adapter = OpenReviewAdapter()
    ref = SiteRef(site="openreview", external_id="abc123XYZ")
    hosts = adapter_allowed_hosts(adapter, ref)
    assert hosts == frozenset({"openreview.net"})


def test_openreview_parse_note_from_fixture() -> None:
    """API2 note JSON → SiteMeta 写像を検証する。"""
    from alinea_core.adapters.openreview import OpenReviewAdapter

    adapter = OpenReviewAdapter()
    ref = SiteRef(site="openreview", external_id="abc123XYZ")
    payload = _json.loads(_OR_FIXTURE.read_text())
    note = payload["notes"][0]
    meta = adapter.parse_note(note, ref)

    assert meta.site == "openreview"
    assert meta.external_id == "abc123XYZ"
    assert meta.title == "Attention Is All You Need (Mock)"
    assert meta.authors == [{"name": "Alice Author"}, {"name": "Bob Builder"}]
    assert meta.abstract.startswith("We introduce a new mock architecture")
    assert meta.venue == "ICLR 2024"
    # pdate 1704153600000 ms → 2024-01-02
    assert meta.published_on == "2024-01-02"
    assert meta.pdf_url == "https://openreview.net/pdf?id=abc123XYZ"
    assert meta.license == "cc-by-4.0"
    assert meta.doi is None


def test_openreview_parse_note_fallback_to_citation_meta() -> None:
    """note が空(notes=[])の場合は citation_* メタへフォールバックする。"""
    from alinea_core.adapters.openreview import OpenReviewAdapter

    adapter = OpenReviewAdapter()
    ref = SiteRef(site="openreview", external_id="abc123XYZ")
    # note が None → citation_* 経由
    meta = adapter.parse_metadata_from_note_and_citation(
        note=None,
        citation_html="<html><head>"
        '<meta name="citation_title" content="Fallback Title">'
        '<meta name="citation_author" content="Doe, John">'
        '<meta name="citation_publication_date" content="2023/05">'
        "</head></html>",
        ref=ref,
    )
    assert meta.title == "Fallback Title"
    assert meta.authors == [{"name": "John Doe"}]
    assert meta.published_on == "2023-05-01"
    assert meta.pdf_url == "https://openreview.net/pdf?id=abc123XYZ"


def test_openreview_resolve_adapter() -> None:
    """registry 経由で OpenReview URL が解決される。"""
    resolved = resolve_adapter("https://openreview.net/forum?id=abc123XYZ")
    assert resolved is not None
    adapter, ref = resolved
    assert adapter.site == "openreview"
    assert ref.external_id == "abc123XYZ"


def test_openreview_resolve_adapter_pdf_url() -> None:
    """PDF URL も registry で解決される。"""
    resolved = resolve_adapter("https://openreview.net/pdf?id=abc123XYZ")
    assert resolved is not None
    adapter, ref = resolved
    assert adapter.site == "openreview"
    assert ref.external_id == "abc123XYZ"


def test_openreview_citation_fallback_ignores_html_pdf_url() -> None:
    """citation_html に citation_pdf_url があっても adapter URL を使う(SSRF 対策)。"""
    from alinea_core.adapters.openreview import OpenReviewAdapter

    adapter = OpenReviewAdapter()
    ref = SiteRef(site="openreview", external_id="abc123XYZ")
    citation_html = (
        "<html><head>"
        '<meta name="citation_title" content="Attack Paper">'
        '<meta name="citation_author" content="Hacker, Evil">'
        # 悪意のある citation_pdf_url (SSRF 試行)。
        '<meta name="citation_pdf_url" content="https://169.254.169.254/latest/meta-data/">'
        "</head></html>"
    )
    meta = adapter.parse_metadata_from_note_and_citation(
        note=None, citation_html=citation_html, ref=ref
    )
    # HTML 由来の悪性 URL ではなくアダプタ宣言 URL が使われていること。
    assert meta.pdf_url == "https://openreview.net/pdf?id=abc123XYZ"


async def test_fetch_note_returns_note_on_success() -> None:
    """fetch_note は API2 notes[0] を返す(MockTransport)。"""
    from alinea_core.adapters.fetch import fetch_note
    from alinea_core.adapters.openreview import OpenReviewAdapter

    note_payload = _json.loads(_OR_FIXTURE.read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api2/notes" in request.url.path
        return httpx.Response(
            200,
            json=note_payload,
            headers={"content-type": "application/json"},
        )

    adapter = OpenReviewAdapter()
    ref = SiteRef(site="openreview", external_id="abc123XYZ")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        note = await fetch_note(adapter, ref, client=client)
    finally:
        await client.aclose()

    assert note is not None
    assert note["id"] == "abc123XYZ"
    content = note["content"]
    assert isinstance(content, dict)
    assert content["title"]["value"] == "Attention Is All You Need (Mock)"


async def test_fetch_note_returns_none_on_403() -> None:
    """403 は note 不在として None を返す(in-tab PDF fallback シグナル)。"""
    from alinea_core.adapters.fetch import fetch_note
    from alinea_core.adapters.openreview import OpenReviewAdapter

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    adapter = OpenReviewAdapter()
    ref = SiteRef(site="openreview", external_id="abc123XYZ")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        note = await fetch_note(adapter, ref, client=client)
    finally:
        await client.aclose()
    assert note is None


async def test_fetch_note_returns_none_on_empty_notes() -> None:
    """notes=[] は note 不在として None を返す。"""
    from alinea_core.adapters.fetch import fetch_note
    from alinea_core.adapters.openreview import OpenReviewAdapter

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"notes": [], "count": 0})

    adapter = OpenReviewAdapter()
    ref = SiteRef(site="openreview", external_id="abc123XYZ")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        note = await fetch_note(adapter, ref, client=client)
    finally:
        await client.aclose()
    assert note is None


_VALID_PMC = [
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6543210/", "PMC6543210"),
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6543210", "PMC6543210"),
    ("https://pmc.ncbi.nlm.nih.gov/articles/PMC6543210/", "PMC6543210"),
    ("ncbi.nlm.nih.gov/pmc/articles/PMC6543210/", "PMC6543210"),
]

_INVALID_PMC = [
    "https://pubmed.ncbi.nlm.nih.gov/31000000/",
    "https://www.ncbi.nlm.nih.gov/pmc/",
    "https://arxiv.org/abs/2209.03003",
    "",
]


def test_pubmed_match_valid() -> None:
    adapter = PubMedAdapter()
    for url, external_id in _VALID_PUBMED:
        ref = adapter.match(url)
        assert ref is not None, url
        assert ref.site == "pubmed"
        assert ref.external_id == external_id, url


def test_pubmed_match_invalid() -> None:
    adapter = PubMedAdapter()
    for url in _INVALID_PUBMED:
        assert adapter.match(url) is None, url


def test_pmc_match_valid() -> None:
    adapter = PmcAdapter()
    for url, external_id in _VALID_PMC:
        ref = adapter.match(url)
        assert ref is not None, url
        assert ref.site == "pmc", url
        # PMCID は大文字 PMC + 数字へ正規化する。
        assert ref.external_id == external_id, url


def test_pmc_match_invalid() -> None:
    adapter = PmcAdapter()
    for url in _INVALID_PMC:
        assert adapter.match(url) is None, url


def test_pubmed_pmc_url_builders() -> None:
    pubmed = PubMedAdapter()
    pmc = PmcAdapter()
    pm_ref = SiteRef(site="pubmed", external_id="31000000")
    pmc_ref = SiteRef(site="pmc", external_id="PMC6543210")
    assert pubmed.landing_url(pm_ref) == "https://pubmed.ncbi.nlm.nih.gov/31000000/"
    assert pmc.landing_url(pmc_ref) == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6543210/"
    # PubMed は本文 PDF 直リンクを持たない(NCBI client 経由でしか本文へ到達しない)。
    assert pubmed.pdf_url(pm_ref) is None


def test_resolve_adapter_pubmed_and_pmc() -> None:
    resolved_pm = resolve_adapter("https://pubmed.ncbi.nlm.nih.gov/31000000/")
    assert resolved_pm is not None
    assert resolved_pm[0].site == "pubmed"
    assert resolved_pm[1].external_id == "31000000"

    resolved_pmc = resolve_adapter("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6543210/")
    assert resolved_pmc is not None
    assert resolved_pmc[0].site == "pmc"
    assert resolved_pmc[1].external_id == "PMC6543210"


# --------------------------------------------------------------------------- #
# NCBI E-utilities / PMC OA クライアント + Redis throttle(Task 17)
# --------------------------------------------------------------------------- #

from alinea_core.adapters.pubmed import (  # noqa: E402
    NcbiClient,
    NcbiConfig,
    ncbi_throttle,
    ncbi_throttle_interval_ms,
)


class _FakeRedis:
    """in-memory の最小 Redis(SET NX PX スピン用)。TTL は無視する。"""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.set_calls = 0

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
        self.set_calls += 1
        if nx and name in self._store:
            return None
        self._store[name] = value
        return True

    async def aclose(self) -> None:
        return None


def test_ncbi_throttle_interval_depends_on_api_key() -> None:
    # API キーなし = 3 req/s(>= 333ms 間隔)、あり = 10 req/s(>= 100ms 間隔)。
    assert ncbi_throttle_interval_ms(api_key=None) >= 333
    assert ncbi_throttle_interval_ms(api_key="secret") >= 100
    assert ncbi_throttle_interval_ms(api_key="secret") < ncbi_throttle_interval_ms(api_key=None)


async def test_ncbi_throttle_spins_until_slot_free() -> None:
    redis = _FakeRedis()
    # 最初の取得は成功、2 回目は占有中(nx 失敗)→ 解放後に取得できる。
    await ncbi_throttle(redis, interval_ms=100, sleep_ms=1)
    assert redis.set_calls == 1


async def test_ncbi_client_fetches_pmc_jats_from_configured_base() -> None:
    jats = (_FIXTURE.parent / "pmc_article.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        # PMC OA XML は設定可能な base URL 配下のみ叩く(実 NCBI へは行かない)。
        assert request.url.host == "eutils.test"
        if "efetch" in request.url.path:
            return httpx.Response(200, content=jats,
                                  headers={"content-type": "application/xml"})
        raise AssertionError(f"unexpected NCBI path: {request.url.path}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://eutils.test")
    config = NcbiConfig(eutils_base_url="http://eutils.test", api_key=None)
    ncbi = NcbiClient(config=config, client=client, redis=_FakeRedis())
    try:
        xml = await ncbi.fetch_pmc_jats("PMC6543210")
    finally:
        await client.aclose()
    assert b"A Deterministic Method for Parsing JATS" in xml


async def test_ncbi_client_maps_pmid_to_pmcid() -> None:
    idconv = b"""<?xml version="1.0"?>
    <pmcids status="ok">
      <record requested-id="31000000" pmcid="PMC6543210" pmid="31000000"/>
    </pmcids>"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "idconv.test"
        return httpx.Response(200, content=idconv, headers={"content-type": "application/xml"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://idconv.test")
    config = NcbiConfig(idconv_base_url="http://idconv.test", api_key=None)
    ncbi = NcbiClient(config=config, client=client, redis=_FakeRedis())
    try:
        pmcid = await ncbi.pmid_to_pmcid("31000000")
    finally:
        await client.aclose()
    assert pmcid == "PMC6543210"
