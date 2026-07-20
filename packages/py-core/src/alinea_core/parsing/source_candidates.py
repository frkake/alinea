"""サイト取り込みのソース候補順(Task 17。docs/02-ingest.md §8)。

各サイトアダプタが「どの本文フォーマットを、どの順で試すか」を宣言する **純粋** ヘルパ。
worker の取り込みパイプラインはこの順序に従って本文を構造化する(最初に成功した候補を採用)。

- PMC: ``jats`` → ``pdf``(OA 記事は JATS 本文=品質 A を最優先。無ければ PDF 品質 B へ縮退)。
- PubMed: ``pdf`` が取得できる場合のみ PDF。JATS 本文は基本無いため、取れなければ
  abstract metadata のみ(本文取得不可)へ縮退する。
- その他サイト(ACL Anthology 等): ``pdf`` のみ(品質 B)。

arXiv の worker 内候補ロジック(``apps/worker/.../source_candidates.py``: LaTeX→HTML→PDF)とは
別レイヤ。こちらは「site 取り込みでどの原本フォーマットを選ぶか」だけを決める軽量な順序表。
"""

from __future__ import annotations

from typing import Literal

# document_revisions.source_format の値域(0016 で 'jats' を追加)。
SiteSourceFormat = Literal["jats", "pdf"]


def site_source_candidates(site: str) -> tuple[SiteSourceFormat, ...]:
    """サイト名に対する本文フォーマットの試行順を返す。

    未知サイトは PDF(品質 B)のみを試す安全側の既定にする。
    """
    if site == "pmc":
        return ("jats", "pdf")
    if site == "pubmed":
        # PubMed 単体は JATS 本文を持たない。PDF が取得できたときだけ本文化する。
        return ("pdf",)
    return ("pdf",)


__all__ = [
    "SiteSourceFormat",
    "site_source_candidates",
]
