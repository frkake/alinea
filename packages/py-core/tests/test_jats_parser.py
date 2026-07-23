"""JATS (PMC Open Access) → DocumentContent 変換の単体テスト(Task 17)。

品質 A 変換・未知タグの安全縮退・XXE 硬化(DTD/外部エンティティ/スクリプト拒否)・
本文欠落時の abstract-only 縮退を fixture 駆動で検証する。外部ネットワークには一切
接続しない(パーサは純粋)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alinea_core.parsing.jats import (
    JATS_PARSER_VERSION,
    JatsParseError,
    parse_jats,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "pmc_article.xml"


def _fixture_xml() -> bytes:
    return _FIXTURE.read_bytes()


# --------------------------------------------------------------------------- #
# 品質 A 変換
# --------------------------------------------------------------------------- #


def test_parse_jats_quality_a_document() -> None:
    result = parse_jats(_fixture_xml())
    content = result.document.to_document_content()
    assert content.quality_level == "A"
    assert result.document.source_format == "jats"
    assert result.document.parser_version == JATS_PARSER_VERSION
    assert result.body_available is True

    # 見出しツリー: Introduction / Methods(+ 参考文献セクション)。
    headings = [sec.heading.title for sec in content.sections]
    assert "Introduction" in headings
    assert "Methods" in headings

    block_types = {block.type for _sec, block in content.iter_blocks()}
    assert "paragraph" in block_types
    assert "equation" in block_types
    assert "figure" in block_types
    assert "table" in block_types
    assert "reference_entry" in block_types


def test_parse_jats_metadata() -> None:
    meta = parse_jats(_fixture_xml()).meta
    assert meta.pmid == "31000000"
    assert meta.pmcid == "PMC6543210"
    assert meta.doi == "10.1234/jdt.2019.42"
    assert meta.title == "A Deterministic Method for Parsing JATS"
    assert meta.authors == [{"name": "Marie Curie"}, {"name": "Rosalind Franklin"}]
    assert meta.abstract.startswith("We present a method")
    assert meta.published_on == "2019-06-15"
    assert meta.journal == "Journal of Deterministic Testing"
    assert meta.license == "cc-by-4.0"


def test_parse_jats_equation_and_citation_inlines() -> None:
    result = parse_jats(_fixture_xml())
    blocks = [block for _sec, block in result.document.iter_blocks()]

    equations = [b for b in blocks if b.type == "equation"]
    assert equations and equations[0].latex is not None
    assert "\\mathcal{L}" in equations[0].latex

    # 引用(xref ref-type="bibr")は citation インラインへ、図参照(ref-type="fig")は ref へ。
    inline_types = {il.t for b in blocks for il in b.inlines}
    assert "citation" in inline_types
    assert "ref" in inline_types


def test_parse_jats_table_cells_preserved() -> None:
    result = parse_jats(_fixture_xml())
    tables = [b for _sec, b in result.document.iter_blocks() if b.type == "table"]
    assert tables
    table = tables[0]
    # tabular セルは cells(行 x 列)へ写像し、原文テキストが残る。
    cells = getattr(table, "cells", None)
    flat = " ".join(str(c) for row in (cells or []) for c in row)
    assert "Config" in flat and "0.91" in flat


def test_parse_jats_figure_deferred_when_asset_unfetched() -> None:
    result = parse_jats(_fixture_xml())
    figures = [b for _sec, b in result.document.iter_blocks() if b.type == "figure"]
    assert figures
    # 図アセットは境界付き取得(パーサはネットワーク非依存)なので、パース時点では
    # asset_key を持たず deferred placeholder として href を控える。
    assert figures[0].asset_key is None
    assert any(ref.get("href", "").endswith("6543210f1.jpg") for ref in result.deferred_figures)
    # 図の href は block 側にも温存され、worker が deferred placeholder 化・将来取得に使える。
    assert getattr(figures[0], "href", "").endswith("6543210f1.jpg")


# --------------------------------------------------------------------------- #
# href スキーム allow-list(stored-XSS 対策。html_parser と同一)
# --------------------------------------------------------------------------- #

_XSS_LINKS = b"""<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><sec><title>Links</title>
  <p>Safe <ext-link xlink:href="https://example.org/ok">external</ext-link> link,
  bad <ext-link xlink:href="javascript:alert(1)">script</ext-link> link,
  and <ext-link xlink:href="data:text/html;base64,PHNjcmlwdD4=">data</ext-link> link.</p>
</sec></body></article>
"""


def test_parse_jats_drops_unsafe_url_schemes() -> None:
    result = parse_jats(_XSS_LINKS)
    urls = [il for _sec, b in result.document.iter_blocks() for il in b.inlines if il.t == "url"]
    hrefs = [il.href or "" for il in urls]
    # 安全な http(s) だけが url インラインに残る。
    assert "https://example.org/ok" in hrefs
    # javascript:/data: は url にせずテキストへ縮退(a[href] に到達させない)。
    assert not any(h.lower().startswith(("javascript:", "data:")) for h in hrefs)
    all_text = " ".join(
        il.v for _sec, b in result.document.iter_blocks() for il in b.inlines if il.t == "text"
    )
    # リンクテキスト自体は失わない(縮退はプレーンテキスト化)。
    assert "script" in all_text and "data" in all_text


# --------------------------------------------------------------------------- #
# 未知タグの安全縮退
# --------------------------------------------------------------------------- #


def test_parse_jats_unknown_tag_degrades_to_child_text() -> None:
    result = parse_jats(_fixture_xml())
    all_text = " ".join(
        il.v for _sec, b in result.document.iter_blocks() for il in b.inlines if il.t == "text"
    )
    # <unknown-inline-tag> は子テキストへ縮退して失われない。
    assert "unmapped" in all_text and "inline element" in all_text


# --------------------------------------------------------------------------- #
# XXE 硬化: DTD / 外部エンティティ / スクリプトを拒否する
# --------------------------------------------------------------------------- #

_XXE_EXTERNAL_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE article [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<article><body><sec><title>t</title><p>&xxe;</p></sec></body></article>
"""

# 外部 DTD 参照のみ(内部サブセット無し)の DOCTYPE。PMC の efetch JATS が常にこの形:
#   <!DOCTYPE pmc-articleset PUBLIC "..." "https://dtd.nlm.nih.gov/.../*.dtd">
# 外部 DTD は XML_PARAM_ENTITY_PARSING_NEVER により決して取得されず、ENTITY も宣言でき
# ないため安全に許可する(取得を試みるとネットワークで固まるが、本テストが即返るのは
# 取得していない証左)。
_DOCTYPE_EXTERNAL_ONLY = b"""<?xml version="1.0"?>
<!DOCTYPE article SYSTEM "http://evil.example/jats.dtd">
<article><body><sec><title>t</title><p>hello</p></sec></body></article>
"""

# 内部サブセット([...])を持つ DOCTYPE は ENTITY 宣言の温床。外部実体も billion-laughs も
# ここに宣言されるため、内部サブセット付き DOCTYPE は一律拒否する。
_DOCTYPE_INTERNAL_SUBSET = b"""<?xml version="1.0"?>
<!DOCTYPE article [
  <!ELEMENT article ANY>
]>
<article><body><sec><title>t</title><p>hello</p></sec></body></article>
"""

_INTERNAL_ENTITY_BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE article [
  <!ENTITY a "AAAAAAAAAA">
  <!ENTITY b "&a;&a;&a;&a;&a;">
]>
<article><body><sec><title>t</title><p>&b;</p></sec></body></article>
"""


def test_parse_jats_rejects_external_entity() -> None:
    with pytest.raises(JatsParseError) as exc:
        parse_jats(_XXE_EXTERNAL_ENTITY)
    assert exc.value.kind == "parse_error"


def test_parse_jats_accepts_external_only_doctype() -> None:
    # 外部 DTD 参照のみの DOCTYPE(PMC efetch の実形式)は安全に受理される。
    result = parse_jats(_DOCTYPE_EXTERNAL_ONLY)
    assert result.body_available is True
    assert result.document.sections


def test_parse_jats_rejects_internal_subset_doctype() -> None:
    with pytest.raises(JatsParseError):
        parse_jats(_DOCTYPE_INTERNAL_SUBSET)


def test_parse_jats_rejects_entity_bomb() -> None:
    with pytest.raises(JatsParseError):
        parse_jats(_INTERNAL_ENTITY_BOMB)


def test_parse_jats_rejects_non_xml() -> None:
    with pytest.raises(JatsParseError):
        parse_jats(b"<html><body>not jats</body></html>")


# --------------------------------------------------------------------------- #
# 本文欠落: abstract metadata だけを保持し body-unavailable を明示する
# --------------------------------------------------------------------------- #

_FRONT_ONLY = b"""<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmid">42</article-id>
    <title-group><article-title>No Body Here</article-title></title-group>
    <abstract><p>Only an abstract is available for this PubMed record.</p></abstract>
  </article-meta></front>
</article>
"""


def test_parse_jats_no_body_is_abstract_only() -> None:
    result = parse_jats(_FRONT_ONLY)
    assert result.body_available is False
    assert result.meta.pmid == "42"
    assert result.meta.title == "No Body Here"
    assert result.meta.abstract.startswith("Only an abstract")
    # 本文が無いので構造化ブロックは空(abstract は Paper 側メタとして保存される)。
    assert result.document.blocks == []


# --------------------------------------------------------------------------- #
# efetch(db=pmc)の実形式: <pmc-articleset> ラッパ・<floats-group> 図・pmcid ID 種別
# --------------------------------------------------------------------------- #

# 実際の NCBI efetch は <article> を <pmc-articleset> で包み、図を <body> ではなく
# 末尾の <floats-group> に集約し、PMCID を pub-id-type="pmcid"(値は "PMC…")で持つ。
_EFETCH_ARTICLESET = b"""<?xml version="1.0"?>
<!DOCTYPE pmc-articleset PUBLIC "-//NLM//DTD ARTICLE SET 2.0//EN" \
"https://dtd.nlm.nih.gov/ncbi/pmc/articleset/nlm-articleset-2.0.dtd">
<pmc-articleset><article>
  <front><article-meta>
    <article-id pub-id-type="pmcid">PMC11638972</article-id>
    <article-id pub-id-type="pmid">38878555</article-id>
    <article-id pub-id-type="doi">10.1016/j.artmed.2024.102900</article-id>
    <title-group><article-title>Wrapped In An Articleset</article-title></title-group>
  </article-meta></front>
  <body>
    <sec><title>Intro</title>
      <p>See <xref ref-type="fig" rid="F1">Fig. 1</xref>.</p>
    </sec>
  </body>
  <floats-group>
    <fig id="F1"><label>Fig. 1.</label>
      <caption><p>A flow diagram.</p></caption>
      <graphic xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="nihms-f0001.jpg"/>
    </fig>
    <fig id="F2"><label>Fig. 2.</label>
      <caption><p>A second figure.</p></caption>
      <graphic xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="nihms-f0002.jpg"/>
    </fig>
  </floats-group>
</article></pmc-articleset>
"""


def test_parse_jats_unwraps_pmc_articleset() -> None:
    result = parse_jats(_EFETCH_ARTICLESET)
    assert result.body_available is True
    assert result.meta.title == "Wrapped In An Articleset"


def test_parse_jats_reads_pmcid_id_type() -> None:
    # efetch は pub-id-type="pmcid"(値は "PMC…")。旧経路の "pmc" とも両立する。
    meta = parse_jats(_EFETCH_ARTICLESET).meta
    assert meta.pmcid == "PMC11638972"
    assert meta.pmid == "38878555"
    assert meta.doi == "10.1016/j.artmed.2024.102900"


def test_parse_jats_extracts_floats_group_figures() -> None:
    # <floats-group> の図は本文走査では拾えないため、別途収容して図 0 枚を防ぐ。
    result = parse_jats(_EFETCH_ARTICLESET)
    figures = [b for _sec, b in result.document.iter_blocks() if b.type == "figure"]
    assert len(figures) == 2
    hrefs = {ref.get("href", "") for ref in result.deferred_figures}
    assert hrefs == {"nihms-f0001.jpg", "nihms-f0002.jpg"}


# --------------------------------------------------------------------------- #
# ネストした <sec> の section id 一意性(pipeline の重複 id 検証を通す)
# --------------------------------------------------------------------------- #

# 実測(PMC11638972)で、ネストした ``<sec>`` の連番を親ごとに 0 から振っていたため
# ``sec-s0`` が最上位イントロ節と最初の節の最初の子節で衝突し、structuring 段の
# ``_validate_unique_document_ids`` が "duplicate section id: sec-s0" で fail-closed に
# なり品質 A 取り込みが terminal 失敗していた。階層パス id で一意化する。
_NESTED_SECS = b"""<?xml version="1.0"?>
<article><body>
  <sec><title>First</title>
    <p>Parent one.</p>
    <sec><title>First-A</title><p>child a</p></sec>
    <sec><title>First-B</title><p>child b</p></sec>
  </sec>
  <sec><title>Second</title>
    <p>Parent two.</p>
    <sec><title>Second-A</title><p>child a2</p>
      <sec><title>Second-A-1</title><p>grandchild</p></sec>
    </sec>
  </sec>
</body></article>
"""


def test_parse_jats_nested_section_ids_are_unique() -> None:
    from alinea_core.translation.pipeline import _validate_unique_document_ids

    result = parse_jats(_NESTED_SECS)
    content = result.document.to_document_content()

    ids: list[str] = []

    def _walk(sec: object) -> None:
        ids.append(sec.id)  # type: ignore[attr-defined]
        for child in sec.sections:  # type: ignore[attr-defined]
            _walk(child)

    for sec in content.sections:
        _walk(sec)

    # 親ごとに 0 から振ると sec-s0 が衝突する。階層 id なら全て一意。
    assert len(ids) == len(set(ids)), f"duplicate section ids: {ids}"
    # pipeline のリビジョン全体一意性検証を通る(structuring 段が落ちない)。
    _validate_unique_document_ids(content)
