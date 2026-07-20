"""arXiv ライセンスマトリクス判定(docs/09 §5.2)。

図表転載可否・クレジット付記・キャプション分離・共有ページ書誌縮退を 1 箇所で判定する。
記事モードの figure_embed(plans/07 §4.5)と共有ページ(plans/03 §14)がこの結果を使う。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# papers.license の CHECK 値域(plans/02 §4.3)
LicenseId = Literal[
    "cc-by-4.0",
    "cc-by-sa-4.0",
    "cc-by-nc-4.0",
    "cc-by-nc-sa-4.0",
    "cc-by-nd-4.0",
    "cc-by-nc-nd-4.0",
    "cc0",
    "arxiv-nonexclusive",
    "unknown",
]

# 図表転載の扱い
EmbedPolicy = Literal["allow", "caption_separate", "link_card"]


@dataclass(frozen=True)
class LicensePolicy:
    """1 ライセンスに対する権利表示ポリシー。"""

    license_id: str
    # 本人向け翻訳表示は全ライセンスで可(○)。ここは常に True。
    personal_translation: bool
    # 記事モードへの図表転載
    figure_embed: EmbedPolicy
    # クレジット自動付記が必要か
    credit_required: bool
    # SA(継承)表示が必要か
    share_alike: bool
    # 共有ページで書誌のみに縮退するか(figure 転載不可かつ権利不明)
    share_page_bibliography_only: bool


_MATRIX: dict[str, LicensePolicy] = {
    "cc-by-4.0": LicensePolicy(
        "cc-by-4.0",
        True,
        "allow",
        credit_required=True,
        share_alike=False,
        share_page_bibliography_only=False,
    ),
    "cc-by-sa-4.0": LicensePolicy(
        "cc-by-sa-4.0",
        True,
        "allow",
        credit_required=True,
        share_alike=True,
        share_page_bibliography_only=False,
    ),
    "cc-by-nc-4.0": LicensePolicy(
        "cc-by-nc-4.0",
        True,
        "allow",
        credit_required=True,
        share_alike=False,
        share_page_bibliography_only=False,
    ),
    "cc-by-nc-sa-4.0": LicensePolicy(
        "cc-by-nc-sa-4.0",
        True,
        "allow",
        credit_required=True,
        share_alike=True,
        share_page_bibliography_only=False,
    ),
    "cc-by-nd-4.0": LicensePolicy(
        # 改変不可: 図はそのまま、キャプション翻訳は図と分離して表示
        "cc-by-nd-4.0",
        True,
        "caption_separate",
        credit_required=True,
        share_alike=False,
        share_page_bibliography_only=False,
    ),
    "cc-by-nc-nd-4.0": LicensePolicy(
        "cc-by-nc-nd-4.0",
        True,
        "caption_separate",
        credit_required=True,
        share_alike=False,
        share_page_bibliography_only=False,
    ),
    "cc0": LicensePolicy(
        "cc0",
        True,
        "allow",
        credit_required=False,
        share_alike=False,
        share_page_bibliography_only=False,
    ),
    "arxiv-nonexclusive": LicensePolicy(
        # 多数派。既定。図表転載不可 → リンクカード・自作概要図で代替
        "arxiv-nonexclusive",
        True,
        "link_card",
        credit_required=True,
        share_alike=False,
        share_page_bibliography_only=False,
    ),
    "unknown": LicensePolicy(
        # 不明 / 出版社 PDF / アップロード PDF: 転載不可 + 共有ページは書誌のみ縮退
        "unknown",
        True,
        "link_card",
        credit_required=True,
        share_alike=False,
        share_page_bibliography_only=True,
    ),
}


def classify_license(license_id: str) -> LicensePolicy:
    """ライセンス ID からポリシーを返す。未知の値は 'unknown' 相当に落とす(安全側)。"""
    return _MATRIX.get(license_id, _MATRIX["unknown"])


# 本文の公開(共有)を許す「機械判定可能な互換ライセンス」の集合(docs/09 §5.2)。
# CC 系(および CC0)のみ本文公開に足る互換性がある。arxiv-nonexclusive / unknown / 出版社
# PDF は再配布権が明示されないため公開しない(private 既定・安全側)。
_SHAREABLE_LICENSES: frozenset[str] = frozenset(
    {
        "cc-by-4.0",
        "cc-by-sa-4.0",
        "cc-by-nc-4.0",
        "cc-by-nc-sa-4.0",
        "cc-by-nd-4.0",
        "cc-by-nc-nd-4.0",
        "cc0",
    }
)


def is_public_shareable_license(license_id: str | None) -> bool:
    """機械判定可能な互換ライセンスが明示された場合のみ True(=本文の公開を許可)。

    サイト取り込みの既定は private。この関数が True を返す(明示的な CC/CC0)ときだけ
    ``visibility="public"`` を許す。``None`` / ``"unknown"`` / ``"arxiv-nonexclusive"`` は False。
    """

    return bool(license_id) and license_id in _SHAREABLE_LICENSES
