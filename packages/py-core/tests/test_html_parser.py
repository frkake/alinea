"""PY-PARSE-01 / PY-PARSE-04: arXiv HTML パーサ(M0-15。plans/05 §4・docs/01 §4)。

- PY-PARSE-01: LaTeXML DOM → 11+ ブロック型 + インライン 8 種の分解、安定 ID、
  セクションツリー、図表・参考文献、リビジョン間 carryover。
- PY-PARSE-04: KaTeX 数式コーパス — 代表的な LaTeXML 数式 HTML が math_inline /
  equation として抽出され、KaTeX 描画可能な LaTeX ソース
  (annotation encoding="application/x-tex")が保持されること。

外部ネットワーク通信は行わない(ローカル文字列のみ)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.parsing.carryover import carry_over_ids, flatten_blocks
from alinea_core.parsing.html_parser import (
    PARSER_VERSION,
    ParsedDocument,
    _ref_kind,
    parse_arxiv_html,
)
from alinea_core.parsing.pdf_sync import BlockPosition, PdfWord, sync_block_positions

_FIXTURE = Path(__file__).parent / "fixtures" / "rectified_flow_arxiv.html"


def _doc() -> ParsedDocument:
    return parse_arxiv_html(_FIXTURE.read_text(encoding="utf-8"))


# ============================ PY-PARSE-01 ============================
def test_step1_parses_heading_paragraph_math() -> None:
    """plan Step 1 のシード(実モデルに合わせ type / equation を検証)。"""
    doc = parse_arxiv_html(
        "<section><h2>Introduction</h2><p>Rectified flow.</p><math>x</math></section>"
    )
    kinds = [b.type for b in doc.blocks]
    assert "heading" in kinds
    assert "paragraph" in kinds
    assert "equation" in kinds  # docs/01 §4.1 の数式ブロック型は `equation`
    assert all(b.id.startswith("blk-") for b in doc.blocks)


def test_parses_all_eleven_plus_block_types() -> None:
    doc = _doc()
    kinds = {b.type for b in doc.blocks}
    expected = {
        "heading",
        "paragraph",
        "figure",
        "table",
        "equation",
        "code",
        "list",
        "quote",
        "theorem",
        "algorithm",
        "footnote",
        "reference_entry",
    }
    assert expected <= kinds, f"missing block types: {expected - kinds}"


def test_all_block_ids_are_prefixed_and_pathsafe() -> None:
    doc = _doc()
    ids = [b.id for b in doc.blocks]
    assert ids, "no blocks parsed"
    assert all(bid.startswith("blk-") for bid in ids)
    assert all(" " not in bid for bid in ids)  # パスに空白が漏れない(付録番号など)
    assert len(ids) == len(set(ids)), "block ids must be unique within a revision"


def test_block_ids_are_deterministic() -> None:
    a = [b.id for b in _doc().blocks]
    b = [b.id for b in _doc().blocks]
    assert a == b


def test_block_id_uses_equation_number() -> None:
    doc = _doc()
    eqs = [b for b in doc.blocks if b.type == "equation"]
    # docs/01 §4.4 例 blk-3-eq5-… の形(セクションパス + eq + 式番号)。
    assert any(b.id.startswith("blk-1-eq1-") for b in eqs)
    assert any(b.id.startswith("blk-2-eq2-") for b in eqs)


def test_section_tree_nesting_and_paths() -> None:
    doc = _doc()
    top_ids = [s.id for s in doc.sections]
    assert "sec-1" in top_ids
    assert "sec-2" in top_ids
    assert "sec-A" in top_ids  # 付録は番号 A に正規化(plans/05 §4.2)
    assert "sec-refs" in top_ids  # 参考文献は固定 path
    sec1 = next(s for s in doc.sections if s.id == "sec-1")
    assert [sub.id for sub in sec1.sections] == ["sec-1-1"]
    assert sec1.heading.number == "1"
    assert sec1.heading.title == "Introduction"


def test_appendix_number_normalized() -> None:
    doc = _doc()
    sec_a = next(s for s in doc.sections if s.id == "sec-A")
    assert sec_a.heading.number == "A"
    assert sec_a.heading.title == "Proofs"


def test_all_eight_inline_types_extracted() -> None:
    doc = _doc()
    seen: set[str] = set()
    for b in doc.blocks:
        for il in b.inlines + b.caption:
            seen.add(il.t)
        for item in b.items:
            for il in item:
                seen.add(il.t)
    expected = {
        "text",
        "math_inline",
        "citation",
        "ref",
        "footnote_ref",
        "url",
        "emphasis",
        "code_inline",
    }
    assert expected <= seen, f"missing inline types: {expected - seen}"


def test_citation_and_ref_targets() -> None:
    doc = _doc()
    para = next(b for b in doc.blocks if b.type == "paragraph")
    citation = next(il for il in para.inlines if il.t == "citation")
    assert citation.ref == "bib.bib1"  # reference_entry の label と一致
    kinds = {il.ref: il.kind for il in para.inlines if il.t == "ref"}
    assert kinds["S1.E1"] == "equation"
    assert kinds["S1.F1"] == "figure"


def test_author_year_citation_strips_latexml_bibliography_expansion() -> None:
    doc = parse_arxiv_html(
        """
        <article class="ltx_document">
          <section class="ltx_section" id="S1">
            <h2 class="ltx_title">Intro</h2>
            <div class="ltx_para"><p class="ltx_p">
              Recent models
              <cite class="ltx_cite ltx_citemacro_citet">
                Achiam et al.(<a href="#bib.bib1">2023</a>)
                <span class="ltx_bibblock">
                  Achiam, Adler, Agarwal, Ahmad, Akkaya, Aleman, Almeida, Altenschmidt,
                  Altman, Anadkat, et al.
                </span>
              </cite>
              improved performance.
            </p></div>
          </section>
          <section class="ltx_bibliography" id="bib">
            <h2 class="ltx_title">References</h2>
            <ul><li class="ltx_bibitem" id="bib.bib1">Achiam et al. 2023.</li></ul>
          </section>
        </article>
        """
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    citation = next(il for il in para.inlines if il.t == "citation")
    assert citation.ref == "bib.bib1"
    assert citation.v == "Achiam et al. (2023)"
    assert "Adler" not in citation.v


def test_url_inline_keeps_href() -> None:
    doc = _doc()
    urls = [il for b in doc.blocks for il in b.inlines if il.t == "url"]
    assert any(il.href == "https://github.com/gnobitab/RectifiedFlow" for il in urls)


def test_footnote_ref_and_collected_block() -> None:
    doc = _doc()
    para = next(b for b in doc.blocks if b.type == "paragraph")
    fn_ref = next(il for il in para.inlines if il.t == "footnote_ref")
    assert fn_ref.ref == "footnote1"
    fn_block = next(b for b in doc.blocks if b.type == "footnote")
    assert fn_block.label == "footnote1"
    # 番号マーカー(ltx_note_mark)は本文に混ざらない
    text = " ".join(il.v for il in fn_block.inlines if il.t == "text")
    assert "causal" in text
    assert not text.strip().startswith("1")


def test_figure_asset_caption_and_number() -> None:
    doc = _doc()
    fig = next(b for b in doc.blocks if b.type == "figure")
    assert fig.asset_key == "x1.png"
    assert fig.number == "1"
    assert fig.label == "S1.F1"
    cap = " ".join(il.v for il in fig.caption if il.t == "text")
    assert "Overview" in cap  # 「Figure 1:」タグは除去済み
    assert "Figure 1" not in cap


def test_table_keeps_cell_html() -> None:
    doc = _doc()
    tbl = next(b for b in doc.blocks if b.type == "table")
    assert tbl.label == "S2.T1"
    assert tbl.number == "1"
    assert tbl.raw is not None and "ltx_tabular" in tbl.raw


def test_list_ordered_flag_and_items() -> None:
    doc = _doc()
    lst = next(b for b in doc.blocks if b.type == "list")
    assert lst.ordered is False  # ltx_itemize
    assert len(lst.items) == 2
    assert any(il.t == "ref" for item in lst.items for il in item)


def test_theorem_title_and_label() -> None:
    doc = _doc()
    thm = next(b for b in doc.blocks if b.type == "theorem")
    assert thm.title == "Theorem 1"  # 種別名+番号を保持
    assert thm.label == "Thmtheorem1"


def test_code_and_algorithm_content() -> None:
    doc = _doc()
    code = next(b for b in doc.blocks if b.type == "code")
    assert code.code == "pip install rectified-flow"
    alg = next(b for b in doc.blocks if b.type == "algorithm")
    assert alg.number == "1"
    body = " ".join(il.v for il in alg.inlines)
    assert "range(N)" in body


def test_reference_structuring() -> None:
    doc = _doc()
    refs = {b.label: (b.structured or {}) for b in doc.references}
    assert refs["bib.bib1"]["arxiv_id"] == "2209.03003"
    assert refs["bib.bib1"]["year"] == "2022"
    assert "Rectified Flow" in refs["bib.bib1"]["title"]
    assert refs["bib.bib2"]["doi"].startswith("10.48550")
    assert refs["bib.bib2"]["year"] == "2020"


def test_metadata_sections_skipped() -> None:
    doc = _doc()
    # abstract / authors / dates 由来のブロックは本文に混ざらない
    paras = [b for b in doc.blocks if b.type == "paragraph"]
    joined = " ".join(il.v for b in paras for il in b.inlines if il.t == "text")
    assert "We present rectified flow" not in joined  # abstract
    assert "Xingchao Liu" not in joined  # authors


def test_parser_version_and_quality() -> None:
    doc = _doc()
    assert doc.parser_version == PARSER_VERSION == "html-1.2.0"
    assert doc.quality_level == "A"
    assert doc.source_format == "arxiv_html"


def test_inline_svg_figure_raw_is_preserved() -> None:
    doc = parse_arxiv_html(
        """
        <article class="ltx_document">
          <section class="ltx_section">
            <h2 class="ltx_title">Results</h2>
            <figure id="S1.F1" class="ltx_figure">
              <div class="ltx_flex_figure">
                <svg id="chart" width="120" height="60"><path d="M0 50 L100 10"></path></svg>
              </div>
              <figcaption class="ltx_caption">
                <span class="ltx_tag ltx_tag_figure">Figure 1: </span>Inline SVG chart.
              </figcaption>
            </figure>
          </section>
        </article>
        """
    )
    fig = next(b for b in doc.blocks if b.type == "figure")

    assert fig.asset_key is None
    assert fig.raw is not None
    assert "<svg" in fig.raw
    assert "chart" in fig.raw


@pytest.mark.parametrize(
    "svg_body",
    [
        '<rect width="10" height="10" style="fill:red"></rect>',
        '<style>rect{fill:red}</style><rect width="10" height="10"></rect>',
    ],
    ids=["style-attribute", "style-element"],
)
def test_inline_svg_safe_css_is_preserved_for_worker_validation(svg_body: str) -> None:
    doc = parse_arxiv_html(
        f"""<article class="ltx_document"><section class="ltx_section">
<h2 class="ltx_title">Results</h2><figure class="ltx_figure">
<svg width="10" height="10">{svg_body}</svg></figure></section></article>"""
    )
    fig = doc.figures[0]

    assert fig.asset_key is None
    assert fig.raw is not None
    assert "fill:red" in fig.raw


@pytest.mark.parametrize(
    "visual",
    [
        '<iframe srcdoc="&lt;script&gt;document.body.dataset.pwned=1&lt;/script&gt;"></iframe>',
        '<svg><foreignObject><img src="relative.png"></foreignObject></svg>',
        '<svg onload="document.body.dataset.pwned=1"><path d="M0 0"></path></svg>',
        '<object data="relative.svg"></object><svg width="10" height="10"></svg>',
    ],
)
def test_inline_figure_raw_rejects_active_or_composite_html(visual: str) -> None:
    doc = parse_arxiv_html(
        f"""<article class="ltx_document"><section class="ltx_section">
<h2 class="ltx_title">Results</h2><figure class="ltx_figure">
<div class="ltx_flex_figure">{visual}</div></figure></section></article>"""
    )
    fig = doc.figures[0]

    assert fig.raw is None
    assert fig.asset_key is None


def test_composite_svg_figure_is_rejected_instead_of_preserved_as_raw_html() -> None:
    doc = parse_arxiv_html(
        """
        <article class="ltx_document">
          <section class="ltx_section">
            <h2 class="ltx_title">Results</h2>
            <figure id="S1.F1" class="ltx_figure">
              <svg id="panel" width="120" height="60">
                <foreignObject><img class="ltx_graphics" src="2607.00001v1/a.png"></foreignObject>
                <foreignObject><img class="ltx_graphics" src="2607.00001v1/b.png"></foreignObject>
              </svg>
              <figcaption class="ltx_caption">
                <span class="ltx_tag ltx_tag_figure">Figure 1: </span>Composite panel.
              </figcaption>
            </figure>
          </section>
        </article>
        """
    )
    fig = next(b for b in doc.blocks if b.type == "figure")

    assert fig.asset_key is None
    assert fig.raw is None


def test_to_document_content_roundtrip() -> None:
    doc = _doc()
    content = doc.to_document_content()
    assert isinstance(content, DocumentContent)
    assert content.quality_level == "A"
    assert len(content.iter_blocks()) == len(doc.blocks)


# ============================ PY-PARSE-04 ============================
# 代表的な LaTeXML 数式 HTML パターン(inline)。annotation の x-tex が正。
_MATH_CORPUS: list[tuple[str, str]] = [
    (
        '<math display="inline"><semantics><mrow><mi>x</mi><mo>+</mo><mi>y</mi></mrow>'
        '<annotation encoding="application/x-tex">x+y</annotation></semantics></math>',
        "x+y",
    ),
    (
        # mathml のテキストは "12" だが annotation は分数ソース。annotation が採られること。
        '<math display="inline"><semantics><mfrac><mn>1</mn><mn>2</mn></mfrac>'
        '<annotation encoding="application/x-tex">\\frac{1}{2}</annotation></semantics></math>',
        "\\frac{1}{2}",
    ),
    (
        '<math display="inline"><semantics>'
        '<annotation encoding="application/x-tex">\\sum_{i=1}^{n} x_i</annotation></semantics></math>',
        "\\sum_{i=1}^{n} x_i",
    ),
    (
        '<math display="inline"><semantics>'
        '<annotation encoding="application/x-tex">\\mathbb{E}_{x\\sim\\pi_0}[f(x)]</annotation>'
        "</semantics></math>",
        "\\mathbb{E}_{x\\sim\\pi_0}[f(x)]",
    ),
]


@pytest.mark.parametrize("math_html,expected_latex", _MATH_CORPUS)
def test_inline_math_annotation_is_preserved(math_html: str, expected_latex: str) -> None:
    html = '<article class="ltx_document"><section class="ltx_section"><h2 class="ltx_title">M</h2>'
    html += f'<div class="ltx_para"><p class="ltx_p">Given {math_html} here.</p></div></section></article>'
    doc = parse_arxiv_html(html)
    para = next(b for b in doc.blocks if b.type == "paragraph")
    maths = [il for il in para.inlines if il.t == "math_inline"]
    assert len(maths) == 1
    # KaTeX がレンダリングする x-tex ソースがそのまま保持される。
    assert maths[0].v == expected_latex


def test_inline_math_alttext_fallback() -> None:
    """annotation が無ければ @alttext を LaTeX ソースに使う(KaTeX 描画可能)。"""
    html = (
        '<article class="ltx_document"><section class="ltx_section"><h2 class="ltx_title">M</h2>'
        '<div class="ltx_para"><p class="ltx_p">See '
        '<math display="inline" alttext="a^2+b^2=c^2"></math> now.</p></div></section></article>'
    )
    doc = parse_arxiv_html(html)
    para = next(b for b in doc.blocks if b.type == "paragraph")
    math = next(il for il in para.inlines if il.t == "math_inline")
    assert math.v == "a^2+b^2=c^2"


def test_block_equation_latex_number_and_label() -> None:
    doc = _doc()
    eqs = {b.number: b for b in doc.blocks if b.type == "equation"}
    assert set(eqs) >= {"1", "2", "3"}
    assert eqs["1"].label == "S1.E1"
    assert eqs["1"].latex == "\\mathrm{d}Z_{t}=v(Z_{t},t)\\,\\mathrm{d}t"
    latex2 = eqs["2"].latex
    assert latex2 is not None and "\\arg\\min" in latex2  # equationgroup 行分割


def test_equationgroup_splits_into_rows() -> None:
    doc = _doc()
    sec2 = next(s for s in doc.sections if s.id == "sec-2")
    eq_numbers = [b.number for b in sec2.blocks if b.type == "equation"]
    assert eq_numbers == ["2", "3"]  # 2 行 → 2 ブロック


def test_math_corpus_all_katex_sources_nonempty() -> None:
    """コーパス全体で LaTeX ソースが失われないこと。"""
    for math_html, expected in _MATH_CORPUS:
        html = (
            '<article class="ltx_document"><section class="ltx_section"><h2 class="ltx_title">M</h2>'
            f'<div class="ltx_para"><p class="ltx_p">{math_html}</p></div></section></article>'
        )
        doc = parse_arxiv_html(html)
        maths = [il for b in doc.blocks for il in b.inlines if il.t == "math_inline"]
        assert maths and maths[0].v == expected


# ============================ ref.kind パターン(plans/05 §4.3.1) ============================
@pytest.mark.parametrize(
    "target,kind",
    [
        ("S2.SS1", "section"),
        ("S12", "section"),
        ("A1", "section"),
        ("S2.E7", "equation"),
        ("A1.E2", "equation"),
        ("S2.F2", "figure"),
        ("S4.T1", "table"),
        ("Thmtheorem1", "theorem"),
        ("alg3", "algorithm"),
        ("algorithm2", "algorithm"),
        ("footnote4", "footnote"),
        ("weird-id", "section"),  # 未知は section へ縮退
    ],
)
def test_ref_kind_patterns(target: str, kind: str) -> None:
    assert _ref_kind(target) == kind


# ============================ carryover(plans/05 §4.5) ============================
_CARRY_BASE = (
    '<article class="ltx_document"><section class="ltx_section" id="S1">'
    '<h2 class="ltx_title"><span class="ltx_tag ltx_tag_section">1 </span>Intro</h2>'
    '<div class="ltx_para"><p class="ltx_p">First paragraph about rectified flow methods.</p></div>'
    '<div class="ltx_para"><p class="ltx_p">Second paragraph describing the ODE dynamics carefully.</p></div>'
    '<div class="ltx_para"><p class="ltx_p">Third paragraph with experimental results here.</p></div>'
    "</section></article>"
)


def test_carryover_identical_keeps_all_ids() -> None:
    v1 = parse_arxiv_html(_CARRY_BASE)
    old = flatten_blocks(v1.sections)
    v2 = parse_arxiv_html(_CARRY_BASE)
    stats = carry_over_ids(old, v2.sections)
    assert stats.total == stats.carried
    assert stats.carried_ratio == 1.0
    assert [b.id for b in flatten_blocks(v2.sections)] == [b.id for b in old]


def test_carryover_edit_same_count_by_order() -> None:
    v1 = parse_arxiv_html(_CARRY_BASE)
    old = flatten_blocks(v1.sections)
    edited = _CARRY_BASE.replace(
        "Second paragraph describing the ODE dynamics carefully.",
        "Second paragraph describing the ODE dynamics very carefully.",
    )
    v2 = parse_arxiv_html(edited)
    stats = carry_over_ids(old, v2.sections)
    new = flatten_blocks(v2.sections)
    # 前後がハッシュ一致のアンカーとなり、間の同種同数ブロックが出現順で引き継がれる。
    assert stats.by_order >= 1
    assert new[2].id == old[2].id


def test_carryover_insertion_uses_fuzzy_and_new_id() -> None:
    v1 = parse_arxiv_html(_CARRY_BASE)
    old = flatten_blocks(v1.sections)
    old_ids = {b.id for b in old}
    edited = _CARRY_BASE.replace(
        "Second paragraph describing the ODE dynamics carefully.",
        "Second paragraph describing the ODE dynamics carefuly.",  # typo → 高類似
    ).replace(
        '<div class="ltx_para"><p class="ltx_p">Third',
        '<div class="ltx_para"><p class="ltx_p">Brand new inserted sentence.</p></div>'
        '<div class="ltx_para"><p class="ltx_p">Third',
    )
    v2 = parse_arxiv_html(edited)
    stats = carry_over_ids(old, v2.sections)
    new = flatten_blocks(v2.sections)
    assert stats.by_fuzzy >= 1  # typo ブロックは編集距離で引き継ぎ
    typo = next(b for b in new if any("carefuly" in il.v for il in b.inlines))
    assert typo.id in old_ids
    inserted = next(b for b in new if any("Brand new" in il.v for il in b.inlines))
    assert inserted.id not in old_ids  # 挿入は新規 ID


def test_carryover_positional_match_when_structure_stable() -> None:
    """パス②(前後関係): 前後がアンカーで同種同数なら、内容が変わっても位置で引き継ぐ。

    ID は不透明識別子で「§1 の 2 番目の段落」に付いた注釈は位置に追従する(docs/01 §4.3)。
    """
    v1 = parse_arxiv_html(_CARRY_BASE)
    old = flatten_blocks(v1.sections)
    edited = _CARRY_BASE.replace(
        "Second paragraph describing the ODE dynamics carefully.",
        "A completely different sentence about diffusion and score matching entirely.",
    )
    v2 = parse_arxiv_html(edited)
    stats = carry_over_ids(old, v2.sections)
    new = flatten_blocks(v2.sections)
    assert stats.by_order >= 1
    assert new[2].id == old[2].id


def test_carryover_dissimilar_block_gets_new_id_when_count_changes() -> None:
    """パス③: 位置整合が崩れ(挿入で個数不一致)、類似も無ければ新規 ID(黙って移さない。P3)。"""
    v1 = parse_arxiv_html(_CARRY_BASE)
    old = flatten_blocks(v1.sections)
    old_ids = {b.id for b in old}
    edited = _CARRY_BASE.replace(
        "Second paragraph describing the ODE dynamics carefully.",
        "A completely different sentence about diffusion and score matching entirely.",
    ).replace(
        '<div class="ltx_para"><p class="ltx_p">Third',
        '<div class="ltx_para"><p class="ltx_p">Yet another unrelated inserted line.</p></div>'
        '<div class="ltx_para"><p class="ltx_p">Third',
    )
    v2 = parse_arxiv_html(edited)
    carry_over_ids(old, v2.sections)
    new = flatten_blocks(v2.sections)
    rewritten = next(b for b in new if any("diffusion" in il.v for il in b.inlines))
    assert rewritten.id not in old_ids


# ============================ pdf_sync(plans/05 §4.6, 最小実装) ============================
def test_pdf_sync_without_pdf_returns_null_positions() -> None:
    doc = _doc()
    result = sync_block_positions(doc.sections, None)
    assert result.sync_rate == 0.0
    assert result.positions
    assert all(p.page is None and p.bbox is None for p in result.positions)
    assert all(isinstance(p, BlockPosition) for p in result.positions)


def test_pdf_sync_interface_matches_words() -> None:
    # 素通しインターフェース検証: 単語列を与えると page/bbox を導出する。
    section = Section(id="sec-1", heading=SectionHeading(number="1", title="X"))
    section.blocks = [
        Block(
            id="blk-1-p1-aaaa",
            type="paragraph",
            inlines=[Inline(t="text", v="rectified flow transports data")],
        )
    ]
    words = [
        PdfWord(page=1, text="rectified", x0=10, y0=20, x1=60, y1=32),
        PdfWord(page=1, text="flow", x0=62, y0=20, x1=90, y1=32),
        PdfWord(page=1, text="transports", x0=92, y0=20, x1=150, y1=32),
        PdfWord(page=1, text="data", x0=152, y0=20, x1=180, y1=32),
    ]
    result = sync_block_positions([section], [words])
    assert result.target == 1
    pos = result.positions[0]
    assert pos.block_id == "blk-1-p1-aaaa"
    assert pos.page == 1
    assert pos.bbox == [10.0, 20.0, 180.0, 32.0]
    assert result.sync_rate == 1.0
