"""他サイトアダプタ 純粋コアの単体テスト(S8 フェーズ1)。

URL 検出・citation_* メタ抽出・SiteMeta 写像・registry 解決を fixture 駆動で検証する。
外部ネットワークには一切接続しない(arXiv アダプタと同方針)。
"""

from __future__ import annotations

from pathlib import Path

from alinea_core.adapters import (
    AclAnthologyAdapter,
    SiteRef,
    extract_citation_meta,
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
