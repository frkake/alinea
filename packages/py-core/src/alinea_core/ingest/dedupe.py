"""重複検知と統合(plans/05 §7・docs/02 §6)。

判定順は docs/02 §6 の逐語: ① arXiv ID(バージョン無視)→ ② DOI → ③ PDF SHA-256 →
④ タイトル正規化+第一著者姓+年のファジー一致。M0 では ①(arXiv ID)完全一致を
`detect_duplicate` で、④ ファジー一致を `find_fuzzy_duplicate` で担う。

ファジー一致はタイトル正規化 + トークンソート類似度で判定する。rapidfuzz は未導入のため
標準ライブラリ `difflib.SequenceMatcher` を用いる(carryover と同じ方針。閾値 0.92 は
`fuzz.token_sort_ratio >= 92` と等価の意図)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_core.db.models import LibraryItem, Paper

# ファジー一致のしきい値(§7.2。token_sort_ratio >= 92 → 0.92)。
FUZZY_TITLE_THRESHOLD = 0.92

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """タイトル正規化(§7.2)。NFKC → 小文字 → 英数以外を空白へ → 前後空白除去。"""
    t = unicodedata.normalize("NFKC", title).lower()
    return _NON_ALNUM.sub(" ", t).strip()


def first_author_family(authors: list[Any] | None) -> str | None:
    """第一著者の姓(名前の最終トークン)。authors 未設定なら None。"""
    if not authors:
        return None
    first = authors[0]
    name = first.get("name") if isinstance(first, dict) else str(first)
    if not name:
        return None
    parts = str(name).split()
    return parts[-1] if parts else None


def _token_sort_ratio(a: str, b: str) -> float:
    """トークンをソートして結合した文字列の類似度(0.0-1.0)。順序不変。"""
    sa = " ".join(sorted(a.split()))
    sb = " ".join(sorted(b.split()))
    return SequenceMatcher(None, sa, sb).ratio()


@dataclass(frozen=True)
class PaperBibView:
    """ファジー一致の比較に必要な最小書誌(§7.2)。"""

    title: str
    first_author_family: str | None
    year: int | None

    @classmethod
    def from_paper(cls, paper: Paper) -> PaperBibView:
        year = paper.published_on.year if paper.published_on else None
        return cls(
            title=paper.title,
            first_author_family=first_author_family(paper.authors),
            year=year,
        )


def is_fuzzy_duplicate(
    a: PaperBibView, b: PaperBibView, *, threshold: float = FUZZY_TITLE_THRESHOLD
) -> bool:
    """タイトル類似度 + 第一著者姓一致 + 年差 <= 1(§7.2)。"""
    if not a.first_author_family or not b.first_author_family:
        return False
    if a.first_author_family.lower() != b.first_author_family.lower():
        return False
    if a.year is not None and b.year is not None and abs(a.year - b.year) > 1:
        return False
    return _token_sort_ratio(normalize_title(a.title), normalize_title(b.title)) >= threshold


async def detect_duplicate(
    session: AsyncSession, arxiv_id: str, *, user_id: str | None = None
) -> LibraryItem | None:
    """arXiv ID(バージョン無視)の完全一致で既存 LibraryItem を返す(§7.1 ①)。

    同一 arXiv ID の Paper があり、かつ(``user_id`` 指定時は)そのユーザーの
    LibraryItem が既にあれば返す。無ければ None(= 新規取り込み経路)。
    """
    paper_id = (
        await session.execute(select(Paper.id).where(Paper.arxiv_id == arxiv_id))
    ).scalar_one_or_none()
    if paper_id is None:
        return None
    stmt = select(LibraryItem).where(LibraryItem.paper_id == paper_id)
    if user_id is not None:
        stmt = stmt.where(LibraryItem.user_id == user_id)
    return (await session.execute(stmt)).scalars().first()


async def find_fuzzy_duplicate(
    session: AsyncSession,
    view: PaperBibView,
    *,
    user_id: str,
    exclude_paper_id: str | None = None,
    threshold: float = FUZZY_TITLE_THRESHOLD,
) -> Paper | None:
    """ファジー一致候補(自動統合はしない。§7.2)。

    対象集合は「同一ユーザーの library_items が指す papers + public papers」。
    最初にヒットした候補を返す(呼び出し側が確認 UI へ回す)。
    """
    owned_subq = select(LibraryItem.paper_id).where(LibraryItem.user_id == user_id)
    stmt = select(Paper).where(or_(Paper.visibility == "public", Paper.id.in_(owned_subq)))
    if exclude_paper_id is not None:
        stmt = stmt.where(Paper.id != exclude_paper_id)
    for paper in (await session.execute(stmt)).scalars():
        if is_fuzzy_duplicate(view, PaperBibView.from_paper(paper), threshold=threshold):
            return paper
    return None
