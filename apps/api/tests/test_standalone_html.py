"""スタンドアロン HTML 純レンダラのテスト(Feature S3・DB 非依存)。

``schemas/standalone_html.py`` は「DB から解決済みの値を受け取り HTML 文字列を返す純関数」
(``schemas/export.py`` と同方針)。全ブロック種・全インライン種・HTML エスケープ・図 data URI・
数式フォールバックマークアップ・訳優先フォールバック・対訳 2 カラム・記事 Markdown サブセットを
DB なしで検証する。

Task 10 拡張: KaTeX 埋め込み・LaTeX表示クリーニングの golden テスト。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

from alinea_api.schemas.latex_display import clean_latex_display_text
from alinea_api.schemas.standalone_html import (
    ArticleBlockView,
    StandaloneMeta,
    TranslationView,
    _KATEX_DIR,
    build_katex_runtime,
    escape_html,
    render_article_html,
    render_block,
    render_document_html,
    render_inline,
)
from alinea_core.document.blocks import Block, DocumentContent

# Canonical shared fixture — also imported by the TS parity test
_SHARED_FIXTURE = (
    pathlib.Path(__file__).parent.parent.parent.parent  # repo root (apps/api -> apps -> repo)
    / "apps/web/src/components/viewer/latex-display-clean.fixtures.json"
)

META = StandaloneMeta(
    title="Flow <Straight> & Fast",
    authors=["Xingchang Liu", "Qiang Liu"],
    arxiv_id="2209.03003",
    generated_at="2026-07-16T00:00:00+00:00",
    mode_label="原文",
    quality_level="A",
)


# ---------------------------------------------------------------------------
# escape_html
# ---------------------------------------------------------------------------
def test_escape_html_escapes_markup_chars() -> None:
    assert escape_html('a < b & c > d "e"') == "a &lt; b &amp; c &gt; d &quot;e&quot;"


# ---------------------------------------------------------------------------
# render_inline(8 種)
# ---------------------------------------------------------------------------
def test_render_inline_text_is_escaped() -> None:
    assert render_inline({"t": "text", "v": "x < y & z"}) == "x &lt; y &amp; z"


def test_render_inline_emphasis_wraps_em() -> None:
    out = render_inline({"t": "emphasis", "v": "strong"})
    assert out == "<em>strong</em>"


def test_render_inline_emphasis_recurses_children() -> None:
    out = render_inline(
        {"t": "emphasis", "children": [{"t": "text", "v": "a"}, {"t": "code_inline", "v": "b"}]}
    )
    assert out == "<em>a<code>b</code></em>"


def test_render_inline_code_inline() -> None:
    assert render_inline({"t": "code_inline", "v": "f(x)"}) == "<code>f(x)</code>"


def test_render_inline_math_inline_uses_alinea_math_span() -> None:
    out = render_inline({"t": "math_inline", "v": r"\alpha"})
    assert 'class="alinea-math"' in out
    assert 'data-display="false"' in out
    assert r"\alpha" in out


def test_render_inline_citation() -> None:
    out = render_inline({"t": "citation", "ref": "ref-12"})
    assert "ref-12" in out


def test_render_inline_ref_labels_by_kind() -> None:
    assert "Fig. 1" in render_inline({"t": "ref", "kind": "figure", "ref": "fig:1", "v": "1"})
    assert "Eq" in render_inline({"t": "ref", "kind": "equation", "ref": "eq:5", "v": "5"})


def test_render_inline_url_is_anchor() -> None:
    out = render_inline({"t": "url", "v": "site", "href": "https://x.test"})
    assert '<a href="https://x.test"' in out
    assert ">site</a>" in out


def test_render_inline_footnote_ref_is_sup() -> None:
    out = render_inline({"t": "footnote_ref", "v": "1", "ref": "fn-1"})
    assert out.startswith("<sup")
    assert "1" in out


# ---------------------------------------------------------------------------
# render_block(代表 12 種)
# ---------------------------------------------------------------------------
def test_render_block_paragraph() -> None:
    block = Block(id="b1", type="paragraph", inlines=[{"t": "text", "v": "Hello world"}])
    out = render_block(block, tv=None, image_data_uris={})
    assert "Hello world" in out
    assert out.strip().startswith("<p")


def test_render_block_heading_shows_number_and_title() -> None:
    block = Block(id="h1", type="heading", level=1, number="3", title="Method")
    out = render_block(block, tv=None, image_data_uris={})
    assert "3" in out
    assert "Method" in out


def test_render_block_equation_uses_math_markup() -> None:
    block = Block(id="e1", type="equation", number="1", latex=r"\frac{a}{b}")
    out = render_block(block, tv=None, image_data_uris={})
    assert "alinea-math" in out
    assert 'data-display="true"' in out
    assert r"\frac{a}{b}" in out


def test_render_block_equation_falls_back_to_image_when_no_latex() -> None:
    block = Block(id="e2", type="equation", asset_key="eq.png")
    out = render_block(block, tv=None, image_data_uris={"eq.png": "data:image/png;base64,AAA"})
    assert "data:image/png;base64,AAA" in out


def test_render_block_figure_with_data_uri() -> None:
    block = Block(
        id="f1", type="figure", asset_key="fig-1.png", caption=[{"t": "text", "v": "A figure"}]
    )
    out = render_block(
        block, tv=None, image_data_uris={"fig-1.png": "data:image/png;base64,ZZZ"}
    )
    assert "data:image/png;base64,ZZZ" in out
    assert "A figure" in out


def test_render_block_figure_missing_asset_placeholder() -> None:
    block = Block(id="f2", type="figure", asset_key="missing.png")
    out = render_block(block, tv=None, image_data_uris={})
    assert "画像" in out  # 「画像を表示できません」等のプレースホルダ


def test_render_block_code() -> None:
    block = Block(id="c1", type="code", language="python", code="print('hi')")
    out = render_block(block, tv=None, image_data_uris={})
    assert "<pre" in out
    assert "print(&#x27;hi&#x27;)" in out or "print('hi')" in out


def test_render_block_list_ordered_and_unordered() -> None:
    ol = Block(id="l1", type="list", ordered=True, items=[[{"t": "text", "v": "one"}]])
    ul = Block(id="l2", type="list", ordered=False, items=[[{"t": "text", "v": "two"}]])
    assert "<ol" in render_block(ol, tv=None, image_data_uris={})
    assert "<ul" in render_block(ul, tv=None, image_data_uris={})


def test_render_block_quote() -> None:
    block = Block(id="q1", type="quote", inlines=[{"t": "text", "v": "quoted"}])
    out = render_block(block, tv=None, image_data_uris={})
    assert "<blockquote" in out
    assert "quoted" in out


def test_render_block_reference_entry() -> None:
    block = Block(id="r1", type="reference_entry", raw="Liu et al. 2022. Rectified Flow.")
    out = render_block(block, tv=None, image_data_uris={})
    assert "Rectified Flow" in out


# ---------------------------------------------------------------------------
# render_document_html — modes
# ---------------------------------------------------------------------------
def _content() -> DocumentContent:
    return DocumentContent.model_validate(
        {
            "quality_level": "A",
            "sections": [
                {
                    "id": "sec-1",
                    "heading": {"number": "1", "title": "Introduction"},
                    "blocks": [
                        {"id": "blk-p1", "type": "paragraph",
                         "inlines": [{"t": "text", "v": "Rectified flow is straight."}]},
                        {"id": "blk-p2", "type": "paragraph",
                         "inlines": [{"t": "text", "v": "Second paragraph."}]},
                    ],
                }
            ],
        }
    )


def test_render_document_source_is_self_contained() -> None:
    out = render_document_html(
        _content(), mode="source", units={}, image_data_uris={}, meta=META
    )
    assert out.lstrip().lower().startswith("<!doctype html>")
    assert "<style" in out  # inline CSS
    assert "Rectified flow is straight." in out
    # title はエスケープされて head に出る
    assert "Flow &lt;Straight&gt; &amp; Fast" in out


def test_render_document_translation_prefers_translation_then_falls_back() -> None:
    units = {
        "blk-p1": TranslationView(
            content_ja=[{"t": "text", "v": "整流フローは直線的。"}],
            text_ja="整流フローは直線的。",
            displayable=True,
        ),
        # blk-p2 は未訳 → 原文フォールバック
    }
    out = render_document_html(
        _content(), mode="translation", units=units, image_data_uris={}, meta=META
    )
    assert "整流フローは直線的。" in out
    assert "Second paragraph." in out  # フォールバック


def test_render_document_translation_skips_non_displayable() -> None:
    units = {
        "blk-p1": TranslationView(
            content_ja=[{"t": "text", "v": "無効訳"}], text_ja="無効訳", displayable=False
        ),
    }
    out = render_document_html(
        _content(), mode="translation", units=units, image_data_uris={}, meta=META
    )
    assert "無効訳" not in out
    assert "Rectified flow is straight." in out  # 原文フォールバック


def test_render_document_bilingual_two_columns() -> None:
    units = {
        "blk-p1": TranslationView(
            content_ja=[{"t": "text", "v": "整流フローは直線的。"}],
            text_ja="整流フローは直線的。",
            displayable=True,
        ),
    }
    out = render_document_html(
        _content(), mode="bilingual", units=units, image_data_uris={}, meta=META
    )
    assert "Rectified flow is straight." in out
    assert "整流フローは直線的。" in out


def test_render_document_math_source_fallback_has_no_script() -> None:
    content = DocumentContent.model_validate(
        {
            "quality_level": "A",
            "sections": [
                {
                    "id": "s",
                    "heading": {"number": "1", "title": "T"},
                    "blocks": [{"id": "e", "type": "equation", "latex": "a=b"}],
                }
            ],
        }
    )
    out = render_document_html(content, mode="source", units={}, image_data_uris={}, meta=META)
    assert "<script" not in out  # math_runtime 未指定はフォールバック(JS 無し)


# ---------------------------------------------------------------------------
# render_article_html
# ---------------------------------------------------------------------------
def test_render_article_html_blocks_and_markdown_subset() -> None:
    blocks = [
        ArticleBlockView(type="heading", content={"text": "はじめに", "level": 2}),
        ArticleBlockView(
            type="paragraph",
            content={"markdown": "これは **太字** と *斜体* と `コード` と [リンク](https://x.test)。"},
        ),
        ArticleBlockView(type="quote_source", content={"text_en": "This is the source."}),
        ArticleBlockView(
            type="discussion",
            content={"items": [{"text": "論点1"}, {"text": "論点2"}]},
        ),
        ArticleBlockView(type="attribution", content={"text": "元の論文とは別物です。"}),
    ]
    meta = StandaloneMeta(
        title="やさしい解説",
        authors=["Liu"],
        arxiv_id="2209.03003",
        generated_at="2026-07-16T00:00:00+00:00",
        mode_label="記事",
        quality_level="A",
    )
    out = render_article_html(blocks, image_data_uris={}, meta=meta)
    assert out.lstrip().lower().startswith("<!doctype html>")
    assert "はじめに" in out
    assert "<b>太字</b>" in out
    assert "<i>斜体</i>" in out
    assert "<code>コード</code>" in out
    assert '<a href="https://x.test"' in out
    assert "This is the source." in out
    assert "論点1" in out
    assert "元の論文とは別物です。" in out


def test_render_article_html_figure_embed_data_uri() -> None:
    blocks = [
        ArticleBlockView(
            type="figure_embed",
            content={"asset_key": "fig-1.png", "caption_ja": "図の説明"},
        ),
    ]
    meta = StandaloneMeta(
        title="A", authors=[], arxiv_id=None, generated_at="t", mode_label="記事",
        quality_level="A",
    )
    out = render_article_html(
        blocks, image_data_uris={"fig-1.png": "data:image/png;base64,QQQ"}, meta=meta
    )
    assert "data:image/png;base64,QQQ" in out
    assert "図の説明" in out


# ---------------------------------------------------------------------------
# Task 10: KaTeX 埋め込み golden テスト
# ---------------------------------------------------------------------------

def test_katex_runtime_contains_no_external_urls() -> None:
    """build_katex_runtime() の出力に外部リソース参照 (CDN/外部ホスト) が存在しないこと。

    KaTeX JS 内の W3C 名前空間 URI (http://www.w3.org/...) は JS 文字列リテラルであり
    ネットワーク参照ではないため除外する。src/href 属性や @import による外部ロードを検査する。
    """
    runtime = build_katex_runtime()
    # HTML 属性による外部リソース参照がないか検査 (src=/href= with http/https)
    external_src_href = re.findall(r'(?:src|href)=["\']https?://', runtime)
    assert external_src_href == [], f"External src/href found: {external_src_href}"
    assert "@import url(http" not in runtime, "External @import found"
    # CDN ドメインが含まれていないか
    for cdn in ("cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"):
        assert cdn not in runtime, f"CDN reference to {cdn} found"


def test_katex_runtime_contains_katex_js() -> None:
    """build_katex_runtime() に KaTeX の JS コードが inline で含まれること。"""
    runtime = build_katex_runtime()
    # KaTeX minified JS の特徴的な文字列
    assert "<script>" in runtime or "<script " in runtime
    assert "katex" in runtime.lower()
    # renderMathInElement or katex.render must be present
    assert "renderMathInElement" in runtime or "katex.render" in runtime


def test_katex_runtime_contains_css_inline() -> None:
    """build_katex_runtime() に KaTeX CSS が inline <style> で含まれること。"""
    runtime = build_katex_runtime()
    assert "<style>" in runtime or '<style ' in runtime
    # KaTeX CSS の特徴的なクラス名
    assert ".katex" in runtime


def test_katex_runtime_css_has_inline_fonts() -> None:
    """KaTeX CSS 中のフォント参照が data: URI (base64) になっていること。"""
    runtime = build_katex_runtime()
    # WOFF2 font references should be data URIs
    assert "data:font/woff2;base64," in runtime, (
        "KaTeX fonts are not inlined as data URIs — external font references remain"
    )
    # No relative font paths like fonts/KaTeX_*.woff2
    assert "fonts/KaTeX_" not in runtime, "Relative font path found — fonts not inlined"


def test_katex_manifest_integrity() -> None:
    """manifest.json の SHA-256 ハッシュがオンディスクファイルと一致すること。"""
    manifest_path = _KATEX_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    # Version key present
    assert "katex_version" in manifest, "manifest.json missing 'katex_version' key"
    assert manifest["katex_version"] == "0.16.22"
    # Hash integrity for every non-metadata entry
    failures = []
    for name, expected_sha in manifest.items():
        if name == "katex_version":
            continue
        file_path = _KATEX_DIR / name
        if not file_path.exists():
            failures.append(f"{name}: file not found")
            continue
        actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            failures.append(f"{name}: expected {expected_sha[:16]}… got {actual_sha[:16]}…")
    assert not failures, "manifest.json hash mismatches:\n" + "\n".join(failures)


def test_document_with_katex_runtime_has_no_network_refs() -> None:
    """KaTeX runtime を注入したスタンドアロン HTML に外部 URL 参照がないこと。"""
    content = DocumentContent.model_validate({
        "quality_level": "A",
        "sections": [{
            "id": "s1",
            "heading": {"number": "1", "title": "Math"},
            "blocks": [{"id": "e1", "type": "equation", "latex": r"\frac{a}{b}"}],
        }],
    })
    runtime = build_katex_runtime()
    out = render_document_html(
        content, mode="source", units={}, image_data_uris={}, meta=META,
        math_runtime=runtime,
    )
    # Check for any src= or href= pointing to external URL
    external_refs = re.findall(r'(?:src|href)=["\']https?://', out)
    assert external_refs == [], f"External network references found: {external_refs}"
    # Also check @import in style and CDN refs
    assert "@import url(http" not in out
    for cdn in ("cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"):
        assert cdn not in out, f"CDN reference to {cdn} found"


def test_document_with_katex_runtime_renders_math_markup() -> None:
    """KaTeX runtime 注入後も alinea-math スパンが残ること(JS が削除しない)。"""
    content = DocumentContent.model_validate({
        "quality_level": "A",
        "sections": [{
            "id": "s1",
            "heading": {"number": "1", "title": "Math"},
            "blocks": [{"id": "e1", "type": "equation", "latex": r"E=mc^2"}],
        }],
    })
    runtime = build_katex_runtime()
    out = render_document_html(
        content, mode="source", units={}, image_data_uris={}, meta=META,
        math_runtime=runtime,
    )
    assert 'class="alinea-math"' in out
    assert "E=mc^2" in out


# ---------------------------------------------------------------------------
# Task 10: LaTeX 表示クリーニング golden テスト (shared JSON fixtures)
# ---------------------------------------------------------------------------

def _load_latex_cases() -> list[dict]:
    return json.loads(_SHARED_FIXTURE.read_text())


def test_latex_clean_parity_with_shared_fixtures() -> None:
    """shared JSON fixtures が Python clean_latex_display_text() で通ること。

    同一フィクスチャは apps/web/src/components/viewer/latex-display-clean.test.ts でも
    TypeScript 側の cleanLatexDisplayText() に対して検査する (parity 保証)。
    """
    cases = _load_latex_cases()
    failures = []
    for case in cases:
        inp = case["input"]
        expected = case["output"]
        got = clean_latex_display_text(inp)
        if got != expected:
            failures.append(
                f"\n  [{case['description']}]\n  input:    {inp!r}\n  expected: {expected!r}\n  got:      {got!r}"
            )
    assert not failures, "LaTeX clean parity failures:" + "".join(failures)


def test_latex_clean_plain_text_passthrough() -> None:
    """LaTeX マーカーがない平文はそのまま返すこと。"""
    text = "This is plain text with no LaTeX."
    assert clean_latex_display_text(text) == text


def test_latex_clean_textbf_unwrapped() -> None:
    assert clean_latex_display_text(r"\textbf{bold}") == "bold"


def test_latex_clean_label_removed() -> None:
    result = clean_latex_display_text(r"x = 1 \label{eq:main}")
    assert r"\label" not in result


def test_latex_clean_notag_removed() -> None:
    result = clean_latex_display_text(r"x = 1 \notag")
    assert r"\notag" not in result
