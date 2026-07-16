"""論文単位スタンドアロンエクスポートの HTML 純レンダラ(Feature S3)。

``schemas/export.py`` と同じく「DB から解決済みの値を受け取り HTML 文字列を返す純関数」を
主体とする。DB アクセスは ``routers/export.py`` の責務で、本モジュールは pytest から直接呼べる
純関数として単体テスト可能にする。

- 出力は**サーバ非依存で開ける単一 HTML**(inline CSS・図は data URI・数式は意味づけマークアップ)。
- ブロック/インラインの分岐はビューアの ``SourcePane.tsx`` / ``InlineRenderer.tsx`` を写す。
- 数式は常に ``<span class="alinea-math" data-display=...>…LaTeX…</span>`` で出力する。実際の
  描画方式(KaTeX ランタイム注入)は ``math_runtime`` 引数で ``<head>`` に差し込む。未指定時は
  inline CSS が等幅ボックスの LaTeX ソース表示にフォールバックする(欠損ではなく読める劣化)。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from alinea_core.document.blocks import Block, DocumentContent

Mode = Literal["source", "translation", "bilingual"]


# ============================================================================
# レンダリング入力(DB から解決済みの値のみを持つ、DB 非依存の値オブジェクト)
# ============================================================================
@dataclass(frozen=True)
class TranslationView:
    """1 ブロックの訳(translation_units 由来)。``displayable`` は呼び出し側で計算する。"""

    content_ja: list[dict[str, Any]] | dict[str, Any] | None
    text_ja: str
    displayable: bool


@dataclass(frozen=True)
class ArticleBlockView:
    """記事 1 ブロックの wire 相当(``article_blocks`` の type + content)。"""

    type: str
    content: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StandaloneMeta:
    title: str
    authors: list[str]
    arxiv_id: str | None
    generated_at: str  # ISO8601
    mode_label: str  # 「原文」「訳文」「対訳」「記事」
    quality_level: str  # "A" | "B"


# ============================================================================
# エスケープ
# ============================================================================
def escape_html(value: str) -> str:
    """HTML テキストノード用エスケープ(``&``/``<``/``>``/``"`` を実体参照へ)。"""
    return html.escape(str(value), quote=True)


def _escape_attr(value: str) -> str:
    return html.escape(str(value), quote=True)


# ============================================================================
# 数式マークアップ(方式非依存。math_runtime が実描画を担う)
# ============================================================================
def _math_span(latex: str, *, display: bool) -> str:
    flag = "true" if display else "false"
    return (
        f'<span class="alinea-math" data-display="{flag}">'
        f"{escape_html(latex)}</span>"
    )


# ============================================================================
# インライン(docs/01 §4.2 の 8 種。InlineRenderer.tsx を写す)
# ============================================================================
_REF_LABELS = {
    "figure": "Fig.",
    "table": "Table",
    "equation": "Eq.",
    "section": "Sec.",
    "algorithm": "Algorithm",
    "theorem": "Theorem",
}


def render_inline(inline: dict[str, Any]) -> str:
    t = inline.get("t")
    if t == "text":
        return escape_html(str(inline.get("v") or ""))
    if t == "emphasis":
        children = inline.get("children")
        if isinstance(children, list):
            inner = "".join(
                render_inline(child) for child in children if isinstance(child, dict)
            )
        else:
            inner = escape_html(str(inline.get("v") or ""))
        return f"<em>{inner}</em>"
    if t == "code_inline":
        return f"<code>{escape_html(str(inline.get('v') or ''))}</code>"
    if t == "math_inline":
        return _math_span(str(inline.get("v") or ""), display=False)
    if t == "citation":
        ref = str(inline.get("ref") or inline.get("v") or "").strip()
        return f'<span class="alinea-cite">[{escape_html(ref)}]</span>'
    if t == "ref":
        prefix = _REF_LABELS.get(str(inline.get("kind") or ""), "Ref.")
        number = str(inline.get("v") or inline.get("ref") or "").strip()
        label = f"{prefix} {number}".strip()
        return f'<span class="alinea-ref">{escape_html(label)}</span>'
    if t == "url":
        href = str(inline.get("href") or inline.get("v") or "").strip()
        label = str(inline.get("v") or href)
        if not href:
            return escape_html(label)
        return (
            f'<a href="{_escape_attr(href)}" rel="noopener noreferrer" '
            f'target="_blank">{escape_html(label)}</a>'
        )
    if t == "footnote_ref":
        number = str(inline.get("v") or inline.get("ref") or "").strip()
        return f'<sup class="alinea-fnref">{escape_html(number)}</sup>'
    return escape_html(str(inline.get("v") or ""))


def _render_inlines(inlines: list[Any] | None) -> str:
    if not inlines:
        return ""
    return "".join(render_inline(il) for il in inlines if isinstance(il, dict))


def _inline_dicts(inlines: list[Any] | None) -> list[dict[str, Any]]:
    """``Block`` の pydantic Inline / dict の混在を dict 列へ正規化する。"""
    out: list[dict[str, Any]] = []
    for il in inlines or []:
        if isinstance(il, dict):
            out.append(il)
        else:
            dump = getattr(il, "model_dump", None)
            if callable(dump):
                out.append(dump(mode="json", exclude_none=True))
    return out


# ============================================================================
# 図(data URI 化済みの辞書から解決。欠損は読める劣化)
# ============================================================================
_IMAGE_MISSING = '<div class="alinea-missing">画像を表示できません</div>'


def _figure_image(asset_key: str | None, image_data_uris: dict[str, str]) -> str:
    if asset_key and asset_key in image_data_uris:
        return f'<img src="{_escape_attr(image_data_uris[asset_key])}" alt="" loading="lazy">'
    return _IMAGE_MISSING


# ============================================================================
# ブロック(docs/01 §4.1 の 12 種。SourcePane.SourceBlock を写す)
# ============================================================================
def _translated_inlines(tv: TranslationView | None) -> str | None:
    """訳の表示用 HTML(displayable でない/訳なしは None)。"""
    if tv is None or not tv.displayable:
        return None
    content = tv.content_ja
    if isinstance(content, list) and content:
        return _render_inlines(_inline_dicts(content))
    if tv.text_ja:
        return escape_html(tv.text_ja)
    return None


def render_block(
    block: Block,
    *,
    tv: TranslationView | None,
    image_data_uris: dict[str, str],
) -> str:
    """1 ブロックを HTML 化する。``tv`` があれば段落系は訳優先(未訳は原文フォールバック)。"""
    btype = block.type
    if btype == "heading":
        number = escape_html(block.number or "")
        title = escape_html(block.title or "")
        level = min(max(block.level or 2, 1), 6) + 1
        head = f"{number} {title}".strip()
        return f'<h{level} class="alinea-heading">{head}</h{level}>'
    if btype == "equation":
        number = ""
        if block.number:
            number = f' <span class="alinea-eqno">({escape_html(block.number)})</span>'
        inner = (
            _math_span(block.latex, display=True)
            if block.latex
            else _figure_image(block.asset_key, image_data_uris)
        )
        return f'<div class="alinea-equation">{inner}{number}</div>'
    if btype == "code":
        return f'<pre class="alinea-code"><code>{escape_html(block.code or "")}</code></pre>'
    if btype in ("figure", "table"):
        return _render_figure_table(block, image_data_uris)
    if btype == "list":
        tag = "ol" if block.ordered else "ul"
        items = "".join(
            f"<li>{_render_inlines(_inline_dicts(item))}</li>" for item in block.items
        )
        return f'<{tag} class="alinea-list">{items}</{tag}>'
    if btype == "quote":
        body = _translated_inlines(tv) or _render_inlines(_inline_dicts(block.inlines))
        return f'<blockquote class="alinea-quote">{body}</blockquote>'
    if btype in ("theorem", "algorithm"):
        title = ""
        if block.title:
            title = f'<span class="alinea-thm-title">{escape_html(block.title)}</span>'
        caption = _render_inlines(_inline_dicts(block.caption)) if block.caption else ""
        body = _translated_inlines(tv) or _render_inlines(_inline_dicts(block.inlines))
        return f'<div class="alinea-theorem">{title}{caption}{body}</div>'
    if btype == "footnote":
        marker = f'<sup>{escape_html(block.label or "")}</sup>' if block.label else ""
        body = _render_inlines(_inline_dicts(block.inlines))
        return f'<p class="alinea-footnote">{marker}{body}</p>'
    if btype == "reference_entry":
        return f'<p class="alinea-reference">{escape_html(block.raw or "")}</p>'
    # paragraph(既定)
    body = _translated_inlines(tv) or _render_inlines(_inline_dicts(block.inlines))
    return f'<p class="alinea-paragraph">{body}</p>'


def _render_figure_table(block: Block, image_data_uris: dict[str, str]) -> str:
    label_kind = "図" if block.type == "figure" else "表"
    label = f"{label_kind} {block.label}" if block.label else ""
    caption_inlines = _render_inlines(_inline_dicts(block.caption))
    caption = ""
    if label or caption_inlines:
        caption = (
            f'<figcaption class="alinea-caption">'
            f'<span class="alinea-medialabel">{escape_html(label)}</span> {caption_inlines}'
            f"</figcaption>"
        )
    image = _figure_image(block.asset_key, image_data_uris)
    return f'<figure class="alinea-figure">{image}{caption}</figure>'


# ============================================================================
# HTML ドキュメント外殻
# ============================================================================
_BASE_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; background: #ffffff; color: #1a1a1a; line-height: 1.8;
  font-family: -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", sans-serif;
}
.alinea-wrap { max-width: 820px; margin: 0 auto; padding: 40px 24px 96px; }
.alinea-doc-header {
  border-bottom: 1px solid #e2e2e2; margin-bottom: 32px; padding-bottom: 16px;
}
.alinea-doc-title { font-size: 22px; font-weight: 700; margin: 0 0 8px; }
.alinea-doc-meta { font-size: 12.5px; color: #666; }
.alinea-mode-badge {
  display: inline-block; padding: 1px 8px; border-radius: 4px; margin-right: 8px;
  background: #eef2ff; color: #3538cd; font-size: 11px; font-weight: 600;
}
.alinea-section { margin: 28px 0; }
.alinea-heading { font-weight: 700; line-height: 1.4; margin: 28px 0 12px; }
.alinea-paragraph { margin: 0 0 14px; }
.alinea-quote {
  border-left: 3px solid #d0d0d0; margin: 14px 0; padding: 2px 0 2px 16px; color: #444;
}
.alinea-theorem {
  border: 1px solid #e2e2e2; border-radius: 6px; padding: 12px 16px; margin: 14px 0;
}
.alinea-thm-title { font-weight: 700; margin-right: 8px; }
.alinea-code {
  background: #f5f5f5; border-radius: 6px; padding: 12px 14px; overflow-x: auto;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; line-height: 1.5;
}
.alinea-list { margin: 0 0 14px; padding-left: 26px; }
.alinea-figure { margin: 20px 0; text-align: center; }
.alinea-figure img { max-width: 100%; height: auto; }
.alinea-caption { font-size: 12.5px; color: #555; margin-top: 8px; }
.alinea-medialabel { font-weight: 700; }
.alinea-equation { text-align: center; margin: 16px 0; position: relative; }
.alinea-eqno { color: #888; font-size: 13px; margin-left: 12px; }
.alinea-cite, .alinea-ref { color: #3538cd; font-weight: 600; }
.alinea-reference { font-size: 13px; color: #444; margin: 4px 0; }
.alinea-footnote { font-size: 13px; color: #444; }
.alinea-missing {
  display: inline-block; padding: 12px 16px; background: #f5f5f5; color: #888;
  border-radius: 6px; font-size: 13px;
}
.alinea-math {
  font-family: ui-monospace, "SF Mono", Menlo, monospace; background: #f5f5f7;
  padding: 0 3px; border-radius: 3px; font-size: 0.95em;
}
.alinea-math[data-display="true"] { display: inline-block; padding: 6px 10px; }
.alinea-bi-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; align-items: start;
}
.alinea-bi-src, .alinea-bi-tr { min-width: 0; }
.alinea-bi-full { grid-column: 1 / -1; }
"""


def _document_header(meta: StandaloneMeta) -> str:
    authors = escape_html(", ".join(meta.authors)) if meta.authors else "—"
    ids: list[str] = []
    if meta.arxiv_id:
        ids.append(f"arXiv:{escape_html(meta.arxiv_id)}")
    ids.append(f"品質 {escape_html(meta.quality_level)}")
    meta_line = " · ".join([authors, *ids])
    badge = f'<span class="alinea-mode-badge">{escape_html(meta.mode_label)}</span>'
    return (
        '<header class="alinea-doc-header">'
        f'<h1 class="alinea-doc-title">{escape_html(meta.title)}</h1>'
        f'<div class="alinea-doc-meta">{badge}{meta_line}</div>'
        "</header>"
    )


def _html_shell(*, meta: StandaloneMeta, body: str, math_runtime: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="ja">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape_html(meta.title)}</title>\n"
        f"<style>{_BASE_CSS}</style>\n"
        f"{math_runtime}"
        "</head>\n<body>\n"
        '<div class="alinea-wrap">\n'
        f"{_document_header(meta)}\n"
        f"{body}\n"
        "</div>\n</body>\n</html>\n"
    )


# ============================================================================
# ドキュメント HTML(原文/訳文/対訳)
# ============================================================================
def render_document_html(
    content: DocumentContent,
    *,
    mode: Mode,
    units: dict[str, TranslationView],
    image_data_uris: dict[str, str],
    meta: StandaloneMeta,
    math_runtime: str = "",
) -> str:
    """原文/訳文/対訳の単一 HTML を返す。"""
    parts: list[str] = []
    current_section_id: str | None = None
    bilingual_open = False

    def close_bilingual() -> None:
        nonlocal bilingual_open
        if bilingual_open:
            parts.append("</div>")
            bilingual_open = False

    for section, block in content.iter_blocks():
        if section.id != current_section_id:
            close_bilingual()
            current_section_id = section.id
            heading = section.heading
            if heading and (heading.number or heading.title):
                head = f"{escape_html(heading.number)} {escape_html(heading.title)}".strip()
                parts.append(f'<h2 class="alinea-heading">{head}</h2>')

        tv = units.get(block.id)
        if mode == "bilingual":
            parts.append(_render_bilingual_block(block, tv, image_data_uris))
        elif mode == "translation":
            parts.append(render_block(block, tv=tv, image_data_uris=image_data_uris))
        else:  # source
            parts.append(render_block(block, tv=None, image_data_uris=image_data_uris))

    close_bilingual()
    body = "\n".join(parts)
    return _html_shell(meta=meta, body=body, math_runtime=math_runtime)


_BILINGUAL_PARALLEL_TYPES = frozenset({"paragraph", "list", "quote", "theorem", "footnote"})


def _render_bilingual_block(
    block: Block, tv: TranslationView | None, image_data_uris: dict[str, str]
) -> str:
    """対訳: 段落系は 2 カラム(原文 | 訳)、それ以外は全幅。"""
    if block.type in _BILINGUAL_PARALLEL_TYPES:
        source_html = render_block(block, tv=None, image_data_uris=image_data_uris)
        translated = _translated_inlines(tv)
        tr_html = (
            translated
            if translated is not None
            else '<span class="alinea-missing">翻訳なし</span>'
        )
        return (
            '<div class="alinea-bi-grid">'
            f'<div class="alinea-bi-src">{source_html}</div>'
            f'<div class="alinea-bi-tr">{tr_html}</div>'
            "</div>"
        )
    full = render_block(block, tv=None, image_data_uris=image_data_uris)
    return f'<div class="alinea-bi-full">{full}</div>'


# ============================================================================
# 記事 HTML(article_blocks の wire 相当)
# ============================================================================
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")


def _render_article_markdown(md: str) -> str:
    """記事段落の最小 Markdown サブセット(markdown.tsx を写す)。

    先にエスケープし、その上でトークンを HTML に置換する(生 HTML は許可しない)。
    """
    escaped = escape_html(md)
    escaped = _MD_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = _MD_BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", escaped)
    escaped = _MD_ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", escaped)

    def link(m: re.Match[str]) -> str:
        label, href = m.group(1), m.group(2)
        return f'<a href="{href}" rel="noopener noreferrer" target="_blank">{label}</a>'

    escaped = _MD_LINK_RE.sub(link, escaped)
    return escaped


def _render_article_block(block: ArticleBlockView, image_data_uris: dict[str, str]) -> str:
    c = block.content
    if block.type == "heading":
        level = min(max(int(c.get("level") or 2), 2), 4)
        text = escape_html(str(c.get("text") or ""))
        return f'<h{level} class="alinea-heading">{text}</h{level}>'
    if block.type == "paragraph":
        md = str(c.get("markdown") or c.get("md") or "")
        return f'<div class="alinea-paragraph">{_render_article_markdown(md)}</div>'
    if block.type == "quote_source":
        text_en = escape_html(str(c.get("text_en") or ""))
        return f'<blockquote class="alinea-quote">{text_en}</blockquote>'
    if block.type in ("figure_embed", "explainer_figure"):
        image = _figure_image(str(c.get("asset_key") or "") or None, image_data_uris)
        caption = escape_html(str(c.get("caption_ja") or c.get("caption") or ""))
        credit = escape_html(str(c.get("credit") or ""))
        cap_html = f'<figcaption class="alinea-caption">{caption}'
        if credit:
            cap_html += f' <span class="alinea-medialabel">{credit}</span>'
        cap_html += "</figcaption>"
        return f'<figure class="alinea-figure">{image}{cap_html}</figure>'
    if block.type == "discussion":
        items = c.get("items") or []
        lis = "".join(
            f"<li>{_render_article_markdown(str(i.get('text') or i.get('md') or ''))}</li>"
            for i in items
            if isinstance(i, dict)
        )
        return (
            '<div class="alinea-discussion">'
            '<h3 class="alinea-heading">議論したい点</h3>'
            f'<ol class="alinea-list">{lis}</ol></div>'
        )
    if block.type == "attribution":
        text = escape_html(str(c.get("text") or ""))
        return f'<footer class="alinea-attribution">{text}</footer>'
    return ""


def render_article_html(
    blocks: list[ArticleBlockView],
    *,
    image_data_uris: dict[str, str],
    meta: StandaloneMeta,
    math_runtime: str = "",
) -> str:
    body = "\n".join(_render_article_block(b, image_data_uris) for b in blocks)
    return _html_shell(meta=meta, body=body, math_runtime=math_runtime)
