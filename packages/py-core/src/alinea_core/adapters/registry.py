"""サイトアダプタのレジストリ(S8)。

登録済みアダプタを順に ``match`` して最初に当たったものを返す。arXiv は既存の
:func:`alinea_core.arxiv.ids.parse_arxiv_url` が上位(API)で先に処理するため、ここには含めない
(URL 空間が重ならない)。新アダプタは ``_ADAPTERS`` に追加する。
"""

from __future__ import annotations

from alinea_core.adapters.acl_anthology import AclAnthologyAdapter
from alinea_core.adapters.base import SiteAdapter, SiteRef
from alinea_core.adapters.pubmed import PmcAdapter, PubMedAdapter

# 検出優先順(docs/02 §8): ACL Anthology → (将来)OpenReview → PubMed / PMC。
# URL 空間は重ならない(各アダプタの match は自サイトのホスト/パスにのみ当たる)。
_ADAPTERS: tuple[SiteAdapter, ...] = (
    AclAnthologyAdapter(),
    PubMedAdapter(),
    PmcAdapter(),
)


def registered_adapters() -> tuple[SiteAdapter, ...]:
    """登録済みアダプタのタプルを返す(検出順)。"""
    return _ADAPTERS


def resolve_adapter(url: str) -> tuple[SiteAdapter, SiteRef] | None:
    """URL を検出し ``(アダプタ, SiteRef)`` を返す。どのサイトでもなければ ``None``。"""
    for adapter in _ADAPTERS:
        ref = adapter.match(url)
        if ref is not None:
            return adapter, ref
    return None
