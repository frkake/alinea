"""PubMed / PMC サイトアダプタ + NCBI クライアント(Task 17。docs/02-ingest.md §8)。

- :class:`PubMedAdapter` — ``pubmed.ncbi.nlm.nih.gov/<PMID>`` を検出し PMID へ正規化する。
  PubMed 単体は本文 PDF 直リンクを持たない(本文は NCBI 経由でしか到達しない)。
- :class:`PmcAdapter` — ``ncbi.nlm.nih.gov/pmc/articles/PMC<n>`` を検出し PMCID へ正規化する。
  PMC OA 記事は JATS 本文(品質 A)を持つ。

一つの論文が PMID と PMCID の両方を持つことがある(ID 変換は :class:`NcbiClient` が担う)。
両 ID は Task 15 の ``PaperExternalId``(1 論文に複数 ID)へ保存する。

:class:`NcbiClient` は E-utilities(efetch)・PMC OA・ID Converter を **設定可能な base URL**
で叩く副作用層で、テストでは MockTransport + 別 base URL を注入して実 NCBI に一切触れない。
レート制限は Redis の ``SET NX PX`` スピン(:func:`ncbi_throttle`)で全ワーカー横断に効かせ、
API キーなし=3req/s、あり=10req/s とする(NCBI の公開レート方針)。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from alinea_core.adapters.base import SiteMeta, SiteRef
from alinea_core.adapters.fetch import SiteFetchError
from alinea_core.arxiv.fetch import RedisLike

if TYPE_CHECKING:
    from alinea_core.settings import CoreSettings

# --------------------------------------------------------------------------- #
# URL 検出 / ID 正規化
# --------------------------------------------------------------------------- #

_PUBMED_SITE = "pubmed"
_PMC_SITE = "pmc"

_SCHEME = r"(?:https?://)?"
_TAIL = r"(?:[?#].*)?"

# PubMed: pubmed.ncbi.nlm.nih.gov/<digits> または旧 www.ncbi.nlm.nih.gov/pubmed/<digits>
_PUBMED_HOST = r"(?:www\.)?pubmed\.ncbi\.nlm\.nih\.gov"
_PUBMED_LEGACY_HOST = r"(?:www\.)?ncbi\.nlm\.nih\.gov/pubmed"
_PUBMED_PATTERN = re.compile(
    rf"^{_SCHEME}(?:{_PUBMED_HOST}|{_PUBMED_LEGACY_HOST})/(?P<id>\d+)/?{_TAIL}$"
)

# PMC: (www|pmc).ncbi.nlm.nih.gov/pmc/articles/PMC<digits> と pmc.ncbi.nlm.nih.gov/articles/PMC<n>
_PMC_PATTERN = re.compile(
    rf"^{_SCHEME}(?:www\.)?(?:pmc\.)?ncbi\.nlm\.nih\.gov/(?:pmc/)?articles/(?P<id>PMC\d+)/?{_TAIL}$",
    re.IGNORECASE,
)

_PMID_RE = re.compile(r"^\d+$")
_PMCID_RE = re.compile(r"^PMC\d+$")


def normalize_pmid(raw: str) -> str | None:
    """PubMed の PMID を正規化する(数字列のみ)。"""
    value = raw.strip()
    return value if _PMID_RE.match(value) else None


def normalize_pmcid(raw: str) -> str | None:
    """PMCID を ``PMC<digits>`` へ正規化する(大文字化・接頭辞補完)。"""
    value = raw.strip().upper()
    if not value:
        return None
    if value.isdigit():
        value = f"PMC{value}"
    return value if _PMCID_RE.match(value) else None


class PubMedAdapter:
    """PubMed の検出・URL ビルダ(:class:`SiteAdapter` 実装。純粋)。

    本文 PDF 直リンクは無い(``pdf_url`` は ``None``)。書誌・本文は :class:`NcbiClient` 経由。
    """

    site = _PUBMED_SITE

    def match(self, url: str) -> SiteRef | None:
        raw = url.strip()
        if not raw:
            return None
        hit = _PUBMED_PATTERN.match(raw)
        if hit is None:
            return None
        pmid = normalize_pmid(hit.group("id"))
        if pmid is None:
            return None
        return SiteRef(site=_PUBMED_SITE, external_id=pmid)

    def landing_url(self, ref: SiteRef) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{ref.external_id}/"

    def pdf_url(self, ref: SiteRef) -> str | None:
        return None

    def parse_metadata(self, html: str, ref: SiteRef) -> SiteMeta:
        from alinea_core.adapters.citation_meta import (
            citation_date_to_iso,
            extract_citation_meta,
            normalize_scholar_author,
        )

        cite = extract_citation_meta(html)
        return SiteMeta(
            site=_PUBMED_SITE,
            external_id=ref.external_id,
            title=cite.title or "",
            authors=[{"name": normalize_scholar_author(name)} for name in cite.authors],
            abstract=cite.abstract or "",
            published_on=citation_date_to_iso(cite.publication_date),
            venue=cite.journal_title,
            doi=cite.doi,
            pdf_url=None,
            license="unknown",
            categories=[],
        )


class PmcAdapter:
    """PMC の検出・URL ビルダ(:class:`SiteAdapter` 実装。純粋)。

    PMC OA 記事は JATS 本文(品質 A)を持つ。本文取得は :class:`NcbiClient` の PMC OA 経由。
    """

    site = _PMC_SITE

    def match(self, url: str) -> SiteRef | None:
        raw = url.strip()
        if not raw:
            return None
        hit = _PMC_PATTERN.match(raw)
        if hit is None:
            return None
        pmcid = normalize_pmcid(hit.group("id"))
        if pmcid is None:
            return None
        return SiteRef(site=_PMC_SITE, external_id=pmcid)

    def landing_url(self, ref: SiteRef) -> str:
        # 正規ホストは pmc.ncbi.nlm.nih.gov。旧 www.ncbi.nlm.nih.gov/pmc/ は 301 で
        # ここへ転送されるが、リダイレクト先ホストが allow-list 外になり fetch が
        # source_not_found で落ちるため、最初から正規ホストを返す。
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{ref.external_id}/"

    def pdf_url(self, ref: SiteRef) -> str | None:
        # PMC の本文 PDF 直リンクは記事ごとに異なり landing HTML からしか判らないため、
        # 予測 URL は組み立てない(本文は JATS 優先で NcbiClient が取得する)。
        return None

    def parse_metadata(self, html: str, ref: SiteRef) -> SiteMeta:
        from alinea_core.adapters.citation_meta import (
            citation_date_to_iso,
            extract_citation_meta,
            normalize_scholar_author,
        )

        cite = extract_citation_meta(html)
        return SiteMeta(
            site=_PMC_SITE,
            external_id=ref.external_id,
            title=cite.title or "",
            authors=[{"name": normalize_scholar_author(name)} for name in cite.authors],
            abstract=cite.abstract or "",
            published_on=citation_date_to_iso(cite.publication_date),
            venue=cite.journal_title,
            doi=cite.doi,
            pdf_url=cite.pdf_url,
            license="unknown",
            categories=[],
        )


# --------------------------------------------------------------------------- #
# NCBI クライアント(E-utilities / PMC OA / ID Converter)+ Redis throttle
# --------------------------------------------------------------------------- #

_THROTTLE_KEY = "ncbi:throttle"
_NCBI_HOST = "eutils.ncbi.nlm.nih.gov"
_IDCONV_HOST = "www.ncbi.nlm.nih.gov"

# NCBI 公開レート: API キーなし=3req/s、あり=10req/s。若干の余裕を持たせる。
_INTERVAL_NO_KEY_MS = 340
_INTERVAL_WITH_KEY_MS = 105

MAX_JATS_BYTES = 24 * 1024 * 1024


def ncbi_throttle_interval_ms(*, api_key: str | None) -> int:
    """API キーの有無に応じたリクエスト最小間隔(ms)。無=3req/s、有=10req/s。"""
    return _INTERVAL_WITH_KEY_MS if api_key else _INTERVAL_NO_KEY_MS


async def ncbi_throttle(
    redis: RedisLike, *, interval_ms: int = _INTERVAL_NO_KEY_MS, sleep_ms: int = 20
) -> None:
    """NCBI へのアクセス間隔を全ワーカー横断で制限する(``SET NX PX`` スピン)。

    arXiv の :func:`alinea_core.arxiv.fetch.arxiv_throttle` と同方式。取得できるまで
    ``sleep_ms`` スリープして再試行する(打ち切りはジョブタイムアウトに委ねる)。
    """
    while True:
        acquired = await redis.set(_THROTTLE_KEY, b"1", nx=True, px=interval_ms)
        if acquired:
            return
        await asyncio.sleep(sleep_ms / 1000.0)


@dataclass
class NcbiConfig:
    """NCBI エンドポイントの設定(テストで base URL を差し替える)。"""

    eutils_base_url: str = f"https://{_NCBI_HOST}"
    idconv_base_url: str = f"https://{_IDCONV_HOST}"
    api_key: str | None = None
    tool: str = "alinea"
    email: str = "admin@alinea.app"

    @classmethod
    def from_settings(cls, settings: CoreSettings) -> NcbiConfig:
        eutils = getattr(settings, "alinea_ncbi_eutils_base_url", "") or f"https://{_NCBI_HOST}"
        idconv = getattr(settings, "alinea_ncbi_idconv_base_url", "") or f"https://{_IDCONV_HOST}"
        api_key = getattr(settings, "ncbi_api_key", "") or None
        return cls(eutils_base_url=eutils, idconv_base_url=idconv, api_key=api_key)


@dataclass
class NcbiClient:
    """E-utilities / PMC OA / ID Converter の副作用ラッパ(設定可能 base URL・throttle 付き)。"""

    config: NcbiConfig = field(default_factory=NcbiConfig)
    client: httpx.AsyncClient | None = None
    redis: RedisLike | None = None

    def _params(self, extra: dict[str, str]) -> dict[str, str]:
        params = {"tool": self.config.tool, "email": self.config.email, **extra}
        if self.config.api_key:
            params["api_key"] = self.config.api_key
        return params

    async def _throttle(self) -> None:
        if self.redis is not None:
            await ncbi_throttle(
                self.redis,
                interval_ms=ncbi_throttle_interval_ms(api_key=self.config.api_key),
            )

    async def _get(
        self, base_url: str, path: str, params: dict[str, str], *, max_bytes: int
    ) -> bytes:
        owns = self.client is None
        http = self.client or httpx.AsyncClient()
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            await self._throttle()
            try:
                resp = await http.get(url, params=params, timeout=httpx.Timeout(30.0, connect=5.0))
            except httpx.HTTPError as exc:
                raise SiteFetchError("network_error", f"NCBI request failed: {exc}") from exc
            if resp.status_code == 429:
                raise SiteFetchError("rate_limited", "NCBI returned 429")
            if resp.status_code == 404:
                raise SiteFetchError("source_not_found", "NCBI returned 404")
            if resp.status_code >= 500:
                raise SiteFetchError("upstream_5xx", f"NCBI returned {resp.status_code}")
            if resp.status_code != 200:
                raise SiteFetchError("source_not_found", f"NCBI returned {resp.status_code}")
            data = resp.content
            if len(data) > max_bytes:
                raise SiteFetchError("source_too_large", "NCBI response exceeds size limit")
            return data
        finally:
            if owns:
                await http.aclose()

    async def fetch_pmc_jats(self, pmcid: str) -> bytes:
        """PMC OA 記事の JATS XML を取得する(efetch, db=pmc, rettype=xml)。"""
        normalized = normalize_pmcid(pmcid)
        if normalized is None:
            raise SiteFetchError("source_not_found", f"invalid PMCID: {pmcid!r}")
        numeric = normalized.removeprefix("PMC")
        params = self._params({"db": "pmc", "id": numeric, "rettype": "xml", "retmode": "xml"})
        return await self._get(
            self.config.eutils_base_url, "entrez/eutils/efetch.fcgi", params,
            max_bytes=MAX_JATS_BYTES,
        )

    async def fetch_pubmed_abstract_xml(self, pmid: str) -> bytes:
        """PubMed 記事(JATS 本文が無いもの)の abstract を含む XML を取得する。"""
        normalized = normalize_pmid(pmid)
        if normalized is None:
            raise SiteFetchError("source_not_found", f"invalid PMID: {pmid!r}")
        params = self._params({"db": "pubmed", "id": normalized, "retmode": "xml"})
        return await self._get(
            self.config.eutils_base_url, "entrez/eutils/efetch.fcgi", params,
            max_bytes=MAX_JATS_BYTES,
        )

    async def pmid_to_pmcid(self, pmid: str) -> str | None:
        """ID Converter で PMID → PMCID を引く(無ければ ``None``)。"""
        normalized = normalize_pmid(pmid)
        if normalized is None:
            return None
        params = self._params({"ids": normalized, "format": "xml", "versions": "no"})
        data = await self._get(
            self.config.idconv_base_url, "pmc/utils/idconv/v1.0/", params,
            max_bytes=1 * 1024 * 1024,
        )
        match = re.search(rb'pmcid="(PMC\d+)"', data)
        if match is None:
            return None
        return normalize_pmcid(match.group(1).decode("ascii"))


__all__ = [
    "MAX_JATS_BYTES",
    "NcbiClient",
    "NcbiConfig",
    "PmcAdapter",
    "PubMedAdapter",
    "ncbi_throttle",
    "ncbi_throttle_interval_ms",
    "normalize_pmcid",
    "normalize_pmid",
]
