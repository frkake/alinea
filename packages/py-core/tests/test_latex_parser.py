"""PY-PARSE-02: LaTeX パーサ(M2-01。plans/05 §5・§1.3・§12.3、docs/02 §3)。

arXiv e-print(tar.gz / 単一ファイル gzip)→ 品質 A 構造化。PY-PARSE-01(HTML パーサ)と同水準の
検証(11+ ブロック型 + インライン 8 種、安定 ID、セクション木、リビジョン間 carryover)に加えて、
相互参照解決(`\\ref`/`\\eqref`/`\\cite` → ref/citation インライン)を検証する。

外部ネットワーク通信は行わない(ローカルフィクスチャのみ)。フィクスチャは Rectified Flow
構造を模した縮約版を自作した(`fixtures/latex_rectified_flow_main.tex` + `_appendix.tex` を
`latex_rectified_flow.tar.gz` に同梱)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.parsing.carryover import carry_over_ids, flatten_blocks
from alinea_core.parsing.latex_parser import (
    PARSER_VERSION,
    LatexParseError,
    ParsedDocument,
    extract_latex_archive,
    parse_arxiv_latex,
    parse_latex_source,
    select_main_tex,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_TAR_GZ = _FIXTURES / "latex_rectified_flow.tar.gz"
_SINGLE_GZ = _FIXTURES / "latex_single_paper.tex.gz"
_BBL_TAR_GZ = _FIXTURES / "latex_bbl_paper.tar.gz"


def _doc() -> ParsedDocument:
    return parse_arxiv_latex(_TAR_GZ.read_bytes())


# ============================ アーカイブ展開 ============================


def test_extracts_multi_file_tar_gz() -> None:
    archive = extract_latex_archive(_TAR_GZ.read_bytes())
    assert "latex_rectified_flow_main.tex" in archive.text_files
    assert "latex_rectified_flow_appendix.tex" in archive.text_files


def test_select_main_tex_prefers_main_tex_name() -> None:
    archive = extract_latex_archive(_TAR_GZ.read_bytes())
    name, content = select_main_tex(archive.text_files)
    assert name == "latex_rectified_flow_main.tex"
    assert "\\documentclass" in content


def test_extracts_single_file_gzip_without_tar() -> None:
    archive = extract_latex_archive(_SINGLE_GZ.read_bytes())
    assert list(archive.text_files) == ["main.tex"]
    assert "\\documentclass" in archive.text_files["main.tex"]


def test_parse_arxiv_latex_handles_single_file_gzip() -> None:
    doc = parse_arxiv_latex(_SINGLE_GZ.read_bytes())
    assert doc.quality_level == "A"
    assert doc.source_format == "latex"
    kinds = [b.type for b in doc.blocks]
    assert "heading" in kinds and "paragraph" in kinds
    assert doc.sections[0].heading.title == "Solo"


def test_empty_archive_raises_latex_parse_error() -> None:
    with pytest.raises(LatexParseError) as exc:
        extract_latex_archive(b"")
    assert exc.value.kind == "empty_archive"


def test_garbage_bytes_raise_latex_parse_error() -> None:
    with pytest.raises(LatexParseError):
        parse_arxiv_latex(b"not a valid latex archive at all \x00\x01\x02")


def test_no_documentclass_raises_no_main_tex() -> None:
    with pytest.raises(LatexParseError) as exc:
        parse_arxiv_latex(b"plain text without any latex markup whatsoever")
    assert exc.value.kind == "no_main_tex"


# ============================ ブロック型・IR(PY-PARSE-01 相当) ============================


def test_parser_version_and_quality() -> None:
    doc = _doc()
    assert doc.parser_version == PARSER_VERSION == "latex-1.0.0"
    assert doc.quality_level == "A"
    assert doc.source_format == "latex"


def test_parses_all_twelve_block_types() -> None:
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


def test_all_block_ids_are_prefixed_pathsafe_and_unique() -> None:
    doc = _doc()
    ids = [b.id for b in doc.blocks]
    assert ids, "no blocks parsed"
    assert all(bid.startswith("blk-") for bid in ids)
    assert all(" " not in bid for bid in ids)
    assert len(ids) == len(set(ids))


def test_block_ids_are_deterministic() -> None:
    a = [b.id for b in _doc().blocks]
    b = [b.id for b in _doc().blocks]
    assert a == b


def test_section_tree_nesting_and_paths() -> None:
    doc = _doc()
    top_ids = [s.id for s in doc.sections]
    assert "sec-1" in top_ids
    assert "sec-2" in top_ids
    assert "sec-A" in top_ids  # 付録は番号 A に正規化(plans/05 §4.2 と同方針)
    assert "sec-refs" in top_ids  # 参考文献は独立したトップレベルセクションへ昇格
    sec1 = next(s for s in doc.sections if s.id == "sec-1")
    assert [sub.id for sub in sec1.sections] == ["sec-1-1"]
    assert sec1.heading.number == "1"
    assert sec1.heading.title == "Introduction"
    sub = sec1.sections[0]
    assert sub.heading.number == "1.1"
    assert sub.heading.title == "Reflow"


def test_appendix_number_normalized() -> None:
    doc = _doc()
    sec_a = next(s for s in doc.sections if s.id == "sec-A")
    assert sec_a.heading.number == "A"
    assert sec_a.heading.title == "Proofs"


def test_references_section_is_last_and_independent_of_appendix() -> None:
    doc = _doc()
    order = [s.id for s in doc.sections]
    assert order.index("sec-refs") > order.index("sec-A")
    refs_sec = next(s for s in doc.sections if s.id == "sec-refs")
    assert refs_sec.sections == []
    assert all(b.type in ("heading", "reference_entry") for b in refs_sec.blocks)


def test_metadata_sections_skipped() -> None:
    doc = _doc()
    paras = [b for b in doc.blocks if b.type == "paragraph"]
    joined = " ".join(block_to_plain(b) for b in paras)
    assert "We present rectified flow" not in joined  # abstract
    assert "Xingchao Liu" not in joined  # author (title/author 除去)


def test_input_command_is_expanded_into_appendix_section() -> None:
    doc = _doc()
    sec_a = next(s for s in doc.sections if s.id == "sec-A")
    joined = " ".join(block_to_plain(b) for b in sec_a.blocks)
    assert "Appendix proof text" in joined


# ============================ インライン(8 種) ============================


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


def test_code_inline_from_texttt() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}\\section{M}"
                "Run \\texttt{pip install} now.\\end{document}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    code = next(il for il in para.inlines if il.t == "code_inline")
    assert code.v == "pip install"


def test_inline_parser_preserves_display_math_and_symbol_macros() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}\\section{M}"
                "We use \\LaTeX{} notation, \\eg{} display math \\[ x^2 + y^2 \\]."
                "\\end{document}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    text = block_to_plain(para)
    assert "LaTeX" in text
    assert "e.g." in text
    math = next(il for il in para.inlines if il.t == "math_inline")
    assert math.v == "x^2 + y^2"


# ============================ 相互参照解決(PY-PARSE-02 の追加要件) ============================


def test_cite_resolves_to_citation_inline_matching_bibitem_label() -> None:
    doc = _doc()
    para = next(b for b in doc.blocks if any(il.t == "citation" for il in b.inlines))
    citation = next(il for il in para.inlines if il.t == "citation")
    assert citation.ref == "liu2022flow"
    ref_labels = {b.label for b in doc.references}
    assert citation.ref in ref_labels


def test_eqref_resolves_kind_equation_via_label_map() -> None:
    doc = _doc()
    para = next(
        b for b in doc.blocks if any(il.t == "ref" and il.ref == "eq:ode" for il in b.inlines)
    )
    ref = next(il for il in para.inlines if il.ref == "eq:ode")
    assert ref.kind == "equation"
    eq = next(b for b in doc.blocks if b.type == "equation" and b.label == "eq:ode")
    assert eq is not None


def test_ref_resolves_kind_figure_and_table_via_label_map() -> None:
    doc = _doc()
    para = next(b for b in doc.blocks if any(il.ref == "fig:overview" for il in b.inlines))
    fig_ref = next(il for il in para.inlines if il.ref == "fig:overview")
    assert fig_ref.kind == "figure"
    tbl_ref = next(il for il in para.inlines if il.ref == "tab:results")
    assert tbl_ref.kind == "table"


def test_ref_resolves_kind_section_across_files() -> None:
    """付録(\\input 展開後)からメイン文書のセクションラベルを参照解決できる。"""
    doc = _doc()
    sec_a = next(s for s in doc.sections if s.id == "sec-A")
    ref = next(il for b in sec_a.blocks for il in b.inlines if il.ref == "sec:method")
    assert ref.kind == "section"


def test_unresolved_ref_degrades_to_section_kind_with_warning() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}"
                "\\section{X}\\label{sec:x}See~\\ref{sec:unknown-target}."
                "\\end{document}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    ref = next(il for il in para.inlines if il.t == "ref")
    assert ref.kind == "section"
    assert any("sec:unknown-target" in w for w in doc.warnings)


# ============================ 数式(equation/align 分割・ソース保持) ============================


def test_equation_latex_source_preserved_verbatim() -> None:
    doc = _doc()
    eq = next(b for b in doc.blocks if b.type == "equation" and b.label == "eq:ode")
    assert eq.latex == "\\mathrm{d}Z_t = v(Z_t, t)\\,\\mathrm{d}t"


def test_align_environment_splits_into_multiple_equation_blocks() -> None:
    doc = _doc()
    sec2 = next(s for s in doc.sections if s.id == "sec-2")
    eqs = [b for b in sec2.blocks if b.type == "equation"]
    assert len(eqs) == 2
    assert eqs[0].label == "eq:group"
    assert "\\arg\\min" in (eqs[0].latex or "")
    assert "X_0" in (eqs[1].latex or "")


def test_inline_math_dollar_delimiter_preserved() -> None:
    doc = _doc()
    sec1 = next(s for s in doc.sections if s.id == "sec-1")
    sub = next(s for s in sec1.sections if s.id == "sec-1-1")
    para = next(b for b in sub.blocks if b.type == "paragraph")
    math = next(il for il in para.inlines if il.t == "math_inline")
    assert math.v == "y = f(x)"


_MATH_CORPUS: list[tuple[str, str]] = [
    ("$x+y$", "x+y"),
    ("$\\frac{1}{2}$", "\\frac{1}{2}"),
    ("$\\sum_{i=1}^{n} x_i$", "\\sum_{i=1}^{n} x_i"),
    ("$\\mathbb{E}_{x\\sim\\pi_0}[f(x)]$", "\\mathbb{E}_{x\\sim\\pi_0}[f(x)]"),
    ("\\(a^2+b^2=c^2\\)", "a^2+b^2=c^2"),
]


@pytest.mark.parametrize("math_src,expected_latex", _MATH_CORPUS)
def test_math_corpus_sources_preserved(math_src: str, expected_latex: str) -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                f"\\documentclass{{article}}\\begin{{document}}\\section{{M}}"
                f"Given {math_src} here.\\end{{document}}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    maths = [il for il in para.inlines if il.t == "math_inline"]
    assert len(maths) == 1
    assert maths[0].v == expected_latex


# ============================ 図表・参考文献 ============================


def test_figure_asset_caption_and_label() -> None:
    doc = _doc()
    fig = next(b for b in doc.blocks if b.type == "figure")
    assert fig.asset_key == "x1.png"
    assert fig.label == "fig:overview"
    cap = " ".join(il.v for il in fig.caption if il.t == "text")
    assert "Overview" in cap


def test_table_keeps_tabular_latex_source() -> None:
    doc = _doc()
    tbl = next(b for b in doc.blocks if b.type == "table")
    assert tbl.label == "tab:results"
    assert tbl.raw is not None
    assert "\\begin{tabular}" in tbl.raw
    assert "Ours & 0.99" in tbl.raw


def test_list_ordered_flag_and_items() -> None:
    doc = _doc()
    lst = next(b for b in doc.blocks if b.type == "list")
    assert lst.ordered is False  # itemize
    assert len(lst.items) == 2
    assert any(il.t == "ref" for item in lst.items for il in item)


def test_theorem_title_and_label() -> None:
    doc = _doc()
    thm = next(b for b in doc.blocks if b.type == "theorem")
    assert thm.title == "Theorem 1"
    assert thm.label == "thm:main"


def test_code_and_algorithm_content() -> None:
    doc = _doc()
    code = next(b for b in doc.blocks if b.type == "code")
    assert code.code == "pip install rectified-flow"
    alg = next(b for b in doc.blocks if b.type == "algorithm")
    assert alg.label == "alg:sampling"
    cap = " ".join(il.v for il in alg.caption)
    assert "Rectified Flow Sampling" in cap
    body = " ".join(il.v for il in alg.inlines)
    assert "range(N)" in body


def test_quote_block_present() -> None:
    doc = _doc()
    quote = next(b for b in doc.blocks if b.type == "quote")
    text = " ".join(il.v for il in quote.inlines if il.t == "text")
    assert "shortest paths" in text


def test_url_inline_keeps_href() -> None:
    doc = _doc()
    urls = [il for b in doc.blocks for il in b.inlines if il.t == "url"]
    assert any(il.href == "https://github.com/gnobitab/RectifiedFlow" for il in urls)


def test_footnote_ref_and_collected_block() -> None:
    doc = _doc()
    sec1 = next(s for s in doc.sections if s.id == "sec-1")
    para = next(b for b in sec1.blocks if b.type == "paragraph")
    fn_ref = next(il for il in para.inlines if il.t == "footnote_ref")
    assert fn_ref.ref == "footnote1"
    fn_block = next(b for b in sec1.blocks if b.type == "footnote")
    assert fn_block.label == "footnote1"
    text = " ".join(il.v for il in fn_block.inlines if il.t == "text")
    assert "causal dynamics" in text


def test_reference_structuring_from_thebibliography() -> None:
    doc = _doc()
    refs = {b.label: (b.structured or {}) for b in doc.references}
    assert refs["liu2022flow"]["arxiv_id"] == "2209.03003"
    assert refs["liu2022flow"]["year"] == "2022"
    assert "Flow Straight and Fast" in refs["liu2022flow"]["title"]
    assert refs["song2020ddpm"]["year"] == "2020"
    assert refs["song2020ddpm"]["doi"].startswith("10.48550")


def test_reference_raw_text_strips_emph_markup_for_display() -> None:
    doc = _doc()
    ref = next(b for b in doc.references if b.label == "liu2022flow")
    assert "\\emph" not in (ref.raw or "")
    assert "Flow Straight and Fast" in (ref.raw or "")


def test_bibliography_resolved_from_bbl_file() -> None:
    """`\\bibliography{}` (BibTeX 外部)→ 同梱 `.bbl` の thebibliography を採用する。"""
    doc = parse_arxiv_latex(_BBL_TAR_GZ.read_bytes())
    refs = {b.label: (b.structured or {}) for b in doc.references}
    assert "ext2021" in refs
    assert refs["ext2021"]["year"] == "2021"
    para = next(b for b in doc.blocks if b.type == "paragraph")
    citation = next(il for il in para.inlines if il.t == "citation")
    assert citation.ref == "ext2021"


def test_bibliography_resolved_from_bib_file_and_multi_optional_cite() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}\\section{M}"
                "See \\citep[see][Sec.~2]{zhao2026towards} for details."
                "\\bibliography{refs}\\end{document}"
            ),
            "refs.bib": """
                @inproceedings{zhao2026towards,
                  author = {Zhao, Alice and Smith, Bob},
                  title = {Towards Better Reference Extraction},
                  booktitle = {Proceedings of Tests},
                  year = {2026},
                  eprint = {2601.01234},
                  archivePrefix = {arXiv}
                }
            """,
        },
    )
    refs = {b.label: b for b in doc.references}
    assert "zhao2026towards" in refs
    assert "Towards Better Reference Extraction" in (refs["zhao2026towards"].raw or "")
    para = next(b for b in doc.blocks if b.type == "paragraph")
    citation = next(il for il in para.inlines if il.t == "citation")
    assert citation.ref == "zhao2026towards"


def test_inline_parser_treats_latex_linebreak_and_control_space_as_space() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}\\section{Prompt}"
                r"Please describe this video in detail. Include: \ 1. The main subject \\ 2. The environment."
                "\\end{document}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    text = block_to_plain(para)
    assert "\\" not in text
    assert "Include: 1. The main subject 2. The environment." in text


# ============================ carryover(既存基盤の再利用確認) ============================


def test_carryover_identical_document_keeps_all_ids() -> None:
    v1 = _doc()
    old = flatten_blocks(v1.sections)
    v2 = _doc()
    stats = carry_over_ids(old, v2.sections)
    assert stats.total == stats.carried
    assert stats.carried_ratio == 1.0
    assert [b.id for b in flatten_blocks(v2.sections)] == [b.id for b in old]


def test_carryover_edit_same_count_by_order() -> None:
    base = {
        "main.tex": (
            "\\documentclass{article}\\begin{document}\\section{Intro}\n\n"
            "First paragraph about rectified flow methods.\n\n"
            "Second paragraph describing the ODE dynamics carefully.\n\n"
            "Third paragraph with experimental results here.\n\n"
            "\\end{document}"
        )
    }
    v1 = parse_latex_source("main.tex", base)
    old = flatten_blocks(v1.sections)
    edited = dict(base)
    edited["main.tex"] = base["main.tex"].replace(
        "Second paragraph describing the ODE dynamics carefully.",
        "Second paragraph describing the ODE dynamics very carefully.",
    )
    v2 = parse_latex_source("main.tex", edited)
    stats = carry_over_ids(old, v2.sections)
    new = flatten_blocks(v2.sections)
    assert stats.by_order >= 1
    assert new[1].id == old[1].id


# ============================ document IR 再利用 ============================


def test_to_document_content_roundtrip() -> None:
    doc = _doc()
    content = doc.to_document_content()
    assert isinstance(content, DocumentContent)
    assert content.quality_level == "A"
    assert len(content.iter_blocks()) == len(doc.blocks)
