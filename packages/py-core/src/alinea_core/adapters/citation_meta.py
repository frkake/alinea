"""Highwire/Google Scholar ``<meta name="citation_*">`` の汎用抽出(S8)。

``citation_*`` メタタグは ACL Anthology・OpenReview・PubMed/PMC・主要出版社が横断的に出す
事実上の標準の書誌埋め込み。ここで 1 度実装しておけば、各サイトアダプタは共通の抽出結果を
``SiteMeta`` に写すだけで済む(後続アダプタの限界コストを下げる最大の再利用資産)。

DOM は準信頼として ``selectolax`` の属性値のみを読む(スクリプト実行なし)。抽出値そのものの
無害化(``sanitize_untrusted_text``)は Paper へ載せる上位層の責務とする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser

_WS = re.compile(r"\s+")


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = _WS.sub(" ", text).strip()
    return cleaned or None


@dataclass(frozen=True)
class CitationMeta:
    """``citation_*`` メタタグの生の集約(サイト非依存)。"""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    publication_date: str | None = None
    journal_title: str | None = None
    conference_title: str | None = None
    doi: str | None = None
    pdf_url: str | None = None
    language: str | None = None


def extract_citation_meta(html: str) -> CitationMeta:
    """HTML の ``<meta name="citation_*">`` 群を ``CitationMeta`` に集約する。

    同名タグが複数ある場合(``citation_author``)は出現順に集める。``content`` の空白は
    正規化し、値が空のタグは無視する。
    """

    tree = LexborHTMLParser(html)
    authors: list[str] = []
    singles: dict[str, str] = {}
    for node in tree.css("meta"):
        name = (node.attributes.get("name") or "").strip().lower()
        if not name.startswith("citation_"):
            continue
        content = _clean(node.attributes.get("content"))
        if content is None:
            continue
        if name == "citation_author":
            authors.append(content)
        elif name not in singles:
            # 同名の単数タグは最初の値を採る(重複時の決定性)。
            singles[name] = content

    return CitationMeta(
        title=singles.get("citation_title"),
        authors=authors,
        abstract=singles.get("citation_abstract"),
        publication_date=singles.get("citation_publication_date")
        or singles.get("citation_date"),
        journal_title=singles.get("citation_journal_title"),
        conference_title=singles.get("citation_conference_title"),
        doi=singles.get("citation_doi"),
        pdf_url=singles.get("citation_pdf_url"),
        language=singles.get("citation_language"),
    )


def normalize_scholar_author(raw: str) -> str:
    """Scholar 形式の著者名 ``"Last, First"`` を ``"First Last"`` へ正規化する。

    カンマが無い(既に ``"First Last"`` 形式、または姓のみ)場合はそのまま返す。カンマが複数
    ある異常入力は分割せず素のまま返す(安全側)。
    """

    parts = [part.strip() for part in raw.split(",")]
    if len(parts) == 2 and all(parts):
        family, given = parts
        return f"{given} {family}"
    return _WS.sub(" ", raw).strip()


def citation_date_to_iso(raw: str | None) -> str | None:
    """``citation_publication_date``(``YYYY`` / ``YYYY/MM`` / ``YYYY/MM/DD``)を ISO 日付へ。

    月・日が欠けていれば ``01`` に丸める(``published_on`` は Paper の日付列に落ちる)。
    区切りは ``/`` と ``-`` の両方を許容する。解釈できなければ ``None``。
    """

    if not raw:
        return None
    tokens = re.split(r"[/-]", raw.strip())
    if not tokens or not re.fullmatch(r"\d{4}", tokens[0]):
        return None
    year = tokens[0]
    month = tokens[1] if len(tokens) > 1 and tokens[1].isdigit() else "1"
    day = tokens[2] if len(tokens) > 2 and tokens[2].isdigit() else "1"
    try:
        month_i = min(max(int(month), 1), 12)
        day_i = min(max(int(day), 1), 31)
    except ValueError:
        return None
    return f"{year}-{month_i:02d}-{day_i:02d}"
