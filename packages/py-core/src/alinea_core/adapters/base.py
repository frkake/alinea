"""他サイトアダプタの共有型(S8 / docs/02-ingest.md §8)。

arXiv 専用の :class:`alinea_core.arxiv.ids.ArxivId` / :class:`alinea_core.arxiv.metadata.ArxivMeta`
に対応する、サイト非依存の参照・メタデータ型と :class:`SiteAdapter` プロトコルを定義する。
アダプタは純粋(ネットワーク非依存): URL 検出と、取得済み HTML から ``SiteMeta`` への写像、
および URL ビルダのみを担う。実際の HTTP 取得は上位層(adapters/fetch.py・計画)が行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from alinea_core.licenses import LicenseId


@dataclass(frozen=True)
class SiteRef:
    """正規化済みのサイト別論文参照(arXiv の ``ArxivId`` 相当)。

    ``external_id`` はサイト内で一意な識別子(例: ACL Anthology の ``2023.acl-long.123``、
    OpenReview の forum id、PubMed の PMID)。
    """

    site: str
    external_id: str
    version: str | None = None


@dataclass(frozen=True)
class SiteMeta:
    """papers 投入用に正規化したサイトメタデータ(``ArxivMeta`` の汎用版)。

    ``authors`` は ``[{"name": "First Last"}]`` 形式で ``Paper.authors`` と同型。
    ``published_on`` は ISO 日付文字列(日/月が不明なら年頭に丸める)。``pdf_url`` は本文 PDF の
    直リンク(worker の PDF 品質 B パイプラインが取得する)で、取得できないサイトでは ``None``。
    """

    site: str
    external_id: str
    title: str
    authors: list[dict[str, str]]
    abstract: str
    published_on: str | None
    venue: str | None
    doi: str | None
    pdf_url: str | None
    license: LicenseId = "unknown"
    categories: list[str] = field(default_factory=list)


@runtime_checkable
class SiteAdapter(Protocol):
    """1 サイトの検出・メタ写像・URL ビルダ(純粋)。"""

    site: str

    def match(self, url: str) -> SiteRef | None:
        """URL がこのサイトのものなら ``SiteRef`` を、そうでなければ ``None`` を返す。"""
        ...

    def parse_metadata(self, html: str, ref: SiteRef) -> SiteMeta:
        """取得済み landing HTML を ``SiteMeta`` へ写像する(ネットワーク非依存)。"""
        ...

    def landing_url(self, ref: SiteRef) -> str:
        """書誌ページ(メタデータ取得元)の URL。"""
        ...

    def pdf_url(self, ref: SiteRef) -> str | None:
        """本文 PDF の直リンク(なければ ``None``)。"""
        ...
