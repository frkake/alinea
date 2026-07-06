"""PY-LIC-01: ライセンスマトリクス判定(docs/09 §5.2)。全 8 行を検証。"""

from __future__ import annotations

from yakudoku_core.licenses import classify_license


def test_cc_by_allows_embed_with_credit() -> None:
    p = classify_license("cc-by-4.0")
    assert p.figure_embed == "allow"
    assert p.credit_required is True
    assert p.share_alike is False


def test_cc_by_sa_requires_share_alike() -> None:
    p = classify_license("cc-by-sa-4.0")
    assert p.figure_embed == "allow"
    assert p.share_alike is True


def test_cc_by_nc_allows_embed() -> None:
    assert classify_license("cc-by-nc-4.0").figure_embed == "allow"
    assert classify_license("cc-by-nc-sa-4.0").figure_embed == "allow"


def test_cc_by_nd_separates_caption() -> None:
    p = classify_license("cc-by-nd-4.0")
    assert p.figure_embed == "caption_separate"


def test_cc0_allows_embed_no_credit() -> None:
    p = classify_license("cc0")
    assert p.figure_embed == "allow"
    assert p.credit_required is False


def test_arxiv_nonexclusive_link_card_default() -> None:
    p = classify_license("arxiv-nonexclusive")
    assert p.figure_embed == "link_card"
    assert p.share_page_bibliography_only is False


def test_unknown_link_card_and_bibliography_only() -> None:
    p = classify_license("unknown")
    assert p.figure_embed == "link_card"
    assert p.share_page_bibliography_only is True


def test_unrecognized_falls_back_to_unknown() -> None:
    p = classify_license("some-future-license")
    assert p.license_id == "unknown"
    assert p.share_page_bibliography_only is True


def test_all_licenses_allow_personal_translation() -> None:
    for lic in (
        "cc-by-4.0",
        "cc-by-sa-4.0",
        "cc-by-nc-4.0",
        "cc-by-nc-sa-4.0",
        "cc-by-nd-4.0",
        "cc-by-nc-nd-4.0",
        "cc0",
        "arxiv-nonexclusive",
        "unknown",
    ):
        assert classify_license(lic).personal_translation is True
