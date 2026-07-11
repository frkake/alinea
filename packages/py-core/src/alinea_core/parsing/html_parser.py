"""arXiv HTML(LaTeXML 出力)パーサ(plans/05 §4・docs/01 §4・docs/02 §3)。

arXiv 公式 HTML と ar5iv はどちらも LaTeXML 生成で `ltx_*` クラス体系が共通のため、
単一パーサで両対応する。DOM を走査し docs/01 §4 の構造化ドキュメント中間表現
(11+ ブロック型 + インライン 8 種)へ変換する。ブロック/インラインの Pydantic モデルと
安定 ID 生成は既存の `alinea_core.document` を再利用する(重複定義しない)。

出力の `ParsedDocument` は plans/02 §3.2 の DocumentContentJson(quality_level="A",
source_format="arxiv_html")と同型に写像できる(`to_document_content`)。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.parsing.block_ids import assign_block_ids

PARSER_VERSION = "html-1.3.0"

_WS = re.compile(r"\s+")

# 走査対象外(メタデータ・装飾)。plans/05 §4.2。
_SKIP_CLASSES = frozenset(
    {
        "ltx_authors",
        "ltx_dates",
        "ltx_keywords",
        "ltx_pagination",
        "ltx_page_footer",
        "ltx_role_acknowledgement",
        "ltx_creator",
        "ltx_personname",
        "ltx_page_navbar",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# 見出しタグ番号から除く前置ラベル語(付録は番号 "A" に正規化。plans/05 §4.2)。
_LABEL_WORD = re.compile(r"^(?:appendix|appendices|section|chapter|part)\s+", re.IGNORECASE)
_PATH_UNSAFE = re.compile(r"[^0-9A-Za-z-]")
_ACTIVE_INLINE_TAGS = frozenset({"embed", "foreignobject", "iframe", "object", "script"})

# reference_entry 構造化(plans/05 §4.2.1)。
_ARXIV_RE = re.compile(
    r"(?:arXiv:|arxiv\.org/abs/)\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.IGNORECASE
)
_YEAR_PAREN_RE = re.compile(r"\((19|20)\d{2}\)")
_YEAR_RE = re.compile(r"(19|20)\d{2}")
# reference_entry のタイトル抽出用: typographic/ASCII の引用符。unicode エスケープで曖昧文字回避。
_TITLE_QUOTE_RE = re.compile('[\u201c"\u2018]([^\u201d"\u2019]+)[\u201d"\u2019]')
_DOI_RE = re.compile(r"doi\.org/(\S+)", re.IGNORECASE)

# 相互参照 id パターン → ref.kind(plans/05 §4.3.1)。
_RE_EQ = re.compile(r"^[SA]\d+\.E\d+$")
_RE_FIG = re.compile(r"^[SA]\d+\.F\d+$")
_RE_TBL = re.compile(r"^[SA]\d+\.T\d+$")
_RE_THM = re.compile(r"^Thm[a-z]+\d+$")
_RE_ALG = re.compile(r"^(?:alg|algorithm)\d+$")
_RE_FN = re.compile(r"^footnote\d+$")


class ParsedDocument(BaseModel):
    """パース結果。sections をツリーの正とし、blocks/figures 等は導出ビュー。"""

    quality_level: Literal["A", "B"] = "A"
    source_format: str = "arxiv_html"
    parser_version: str = PARSER_VERSION
    sections: list[Section] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def _all_blocks(self) -> list[Block]:
        out: list[Block] = []

        def walk(sec: Section) -> None:
            out.extend(sec.blocks)
            for sub in sec.sections:
                walk(sub)

        for sec in self.sections:
            walk(sec)
        return out

    @property
    def blocks(self) -> list[Block]:
        """文書順の全ブロック(平坦)。"""
        return self._all_blocks()

    @property
    def figures(self) -> list[Block]:
        return [b for b in self._all_blocks() if b.type == "figure"]

    @property
    def tables(self) -> list[Block]:
        return [b for b in self._all_blocks() if b.type == "table"]

    @property
    def references(self) -> list[Block]:
        return [b for b in self._all_blocks() if b.type == "reference_entry"]

    def to_document_content(self) -> DocumentContent:
        """document_revisions.content(plans/02 §3.2)と同型へ写像する。"""
        return DocumentContent(quality_level=self.quality_level, sections=self.sections)


# ---- モジュール関数(DOM ヘルパ) ----
def _classes(node: LexborNode) -> frozenset[str]:
    cls = node.attributes.get("class")
    if not cls:
        return frozenset()
    return frozenset(str(cls).split())


def _safe_inline_figure_html(html: str | None) -> str | None:
    """Return one structurally inert SVG; reject composite/active author HTML."""
    if not html:
        return None
    fragment = LexborHTMLParser(html)
    svgs = fragment.css("svg")
    if len(svgs) != 1 or fragment.css_first("img") is not None:
        return None
    root = fragment.root
    if root is None:
        return None
    for element in root.traverse(include_text=False):
        tag = str(element.tag or "").casefold()
        if tag in _ACTIVE_INLINE_TAGS:
            return None
        for raw_name, raw_value in element.attributes.items():
            name = str(raw_name).rsplit(":", 1)[-1].casefold()
            value = str(raw_value or "").strip()
            if name.startswith("on") or name == "srcdoc":
                return None
            if name in {"href", "src"} and not re.fullmatch(r"#[A-Za-z0-9_.:-]+", value):
                return None
    return svgs[0].html


def _is_safe_fallback_image(image: LexborNode, figure: LexborNode) -> bool:
    """Accept a raster visual only outside captions and active/SVG content."""

    ancestor = image.parent
    while ancestor is not None:
        if ancestor == figure:
            return True
        tag = str(ancestor.tag or "").casefold()
        if tag in {"figcaption", "svg"} or tag in _ACTIVE_INLINE_TAGS:
            return False
        if "ltx_caption" in _classes(ancestor):
            return False
        ancestor = ancestor.parent
    return False


def _is_visual_inside_caption(visual: LexborNode, figure: LexborNode) -> bool:
    ancestor = visual.parent
    while ancestor is not None and ancestor != figure:
        if str(ancestor.tag or "").casefold() == "figcaption":
            return True
        if "ltx_caption" in _classes(ancestor):
            return True
        ancestor = ancestor.parent
    return False


def _has_svg_ancestor(visual: LexborNode, figure: LexborNode) -> bool:
    ancestor = visual.parent
    while ancestor is not None and ancestor != figure:
        if str(ancestor.tag or "").casefold() == "svg":
            return True
        ancestor = ancestor.parent
    return False


def _fallback_wrapper(visual: LexborNode, figure: LexborNode) -> LexborNode | None:
    """Return an explicitly visual/fallback wrapper, never the figure root.

    A generic layout container (for example ``ltx_flex_figure``) may contain
    multiple independent panels, so mere common ancestry is not enough to
    treat an SVG and raster as alternative encodings of one panel.
    """

    ancestor = visual.parent
    while ancestor is not None and ancestor != figure:
        tag = str(ancestor.tag or "").casefold()
        classes = {value.casefold() for value in _classes(ancestor)}
        if tag == "picture" or any(
            marker in value
            for value in classes
            for marker in ("fallback", "alternate", "alternative", "picture", "visual", "graphics")
        ):
            return ancestor
        ancestor = ancestor.parent
    return None


def _is_explicit_raster_fallback(image: LexborNode) -> bool:
    attributes = {
        str(key).casefold(): str(value or "").casefold()
        for key, value in image.attributes.items()
    }
    classes = {value.casefold() for value in _classes(image)}
    return (
        "hidden" in attributes
        or "inert" in attributes
        or attributes.get("aria-hidden") == "true"
        or attributes.get("role") in {"none", "presentation"}
        or "data-fallback" in attributes
        or "data-alternate" in attributes
        or any("fallback" in value or "alternate" in value for value in classes)
    )


def _element_children(node: LexborNode) -> list[LexborNode]:
    return list(node.iter(include_text=False))


def _collapse(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def _compact_citation_text(text: str | None) -> str:
    """本文中引用の表示値から、LaTeXML が混ぜる書誌展開を落として短いラベルにする。"""
    value = _collapse(text)
    if not value:
        return ""
    value = re.sub(r"\s+([,.;:)])", r"\1", value)
    value = re.sub(r"([([])\s+", r"\1", value)

    author_year = re.match(
        r"^([A-Z][A-Za-z'\u2019.-]+(?:\s+et\s+al\.?)?)\s*[, ]*\(?"
        r"((?:19|20)\d{2})(?:\s*(?:\{[^})]{1,8}\}|[a-z]))?\)?",
        value,
    )
    if author_year:
        author = re.sub(r"\bet\s+al\.?$", "et al.", author_year.group(1), flags=re.IGNORECASE)
        return f"{author} ({author_year.group(2)})"

    looks_expanded = len(value) > 72 or value.count(",") >= 2
    if looks_expanded:
        raw_reference = re.match(r"^([A-Z][A-Za-z'\u2019.-]+)\b.*?\b((?:19|20)\d{2})\b", value)
        if raw_reference:
            return f"{raw_reference.group(1)} et al. ({raw_reference.group(2)})"
    return value


def _math_latex(node: LexborNode | None) -> str:
    """`<math>` から LaTeX を取り出す。annotation(application/x-tex)優先→alttext→本文。"""
    if node is None:
        return ""
    ann = node.css_first('annotation[encoding="application/x-tex"]')
    if ann is not None:
        text = ann.text(deep=True)
        if text and text.strip():
            return text.strip()
    alt = node.attributes.get("alttext")
    if alt:
        return str(alt).strip()
    return _collapse(node.text(deep=True))


def _ref_kind(target: str) -> str:
    """相互参照 id → kind(plans/05 §4.3.1)。未知は section へ縮退。"""
    if _RE_EQ.match(target):
        return "equation"
    if _RE_FIG.match(target):
        return "figure"
    if _RE_TBL.match(target):
        return "table"
    if _RE_THM.match(target):
        return "theorem"
    if _RE_ALG.match(target):
        return "algorithm"
    if _RE_FN.match(target):
        return "footnote"
    return "section"


def _merge_text(inlines: list[Inline]) -> list[Inline]:
    """隣接 text を連結し前後の空白専用 text を除く。"""
    out: list[Inline] = []
    for il in inlines:
        if il.t == "text" and out and out[-1].t == "text":
            out[-1] = Inline(t="text", v=_WS.sub(" ", out[-1].v + il.v))
        else:
            out.append(il)
    while out and out[0].t == "text" and out[0].v == " ":
        out.pop(0)
    while out and out[-1].t == "text" and out[-1].v == " ":
        out.pop()
    return out


class _ArxivHtmlParser:
    """1 回のパースの状態(脚注カウンタ・警告)を保持する。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self._fn_counter = 0
        self._fn_stack: list[list[Block]] = []

    # ---- 判定 ----
    def _is_heading_el(self, node: LexborNode) -> bool:
        if node.tag in _HEADING_TAGS:
            return True
        return any(c.startswith("ltx_title") for c in _classes(node))

    def _is_section_container(self, node: LexborNode) -> bool:
        cls = _classes(node)
        if cls & {
            "ltx_section",
            "ltx_subsection",
            "ltx_subsubsection",
            "ltx_appendix",
            "ltx_bibliography",
        }:
            return True
        return node.tag == "section"

    def _is_skip(self, node: LexborNode) -> bool:
        cls = _classes(node)
        if "ltx_ERROR" in cls:
            self.warnings.append(f"LaTeXML 変換エラー要素をスキップ: {_collapse(node.text())[:80]}")
            return True
        if cls & _SKIP_CLASSES:
            return True
        if "ltx_abstract" in cls:
            return True
        return node.tag in ("script", "style", "nav", "header", "footer")

    def _find_heading(self, node: LexborNode) -> LexborNode | None:
        for ch in _element_children(node):
            if self._is_heading_el(ch):
                return ch
        return None

    def _heading_number_title(self, heading: LexborNode | None) -> tuple[str, str]:
        if heading is None:
            return "", ""
        number = ""
        parts: list[str] = []
        for ch in heading.iter(include_text=True):
            if ch.tag == "-text":
                parts.append(ch.text(deep=False) or "")
                continue
            cls = _classes(ch)
            if any(c.startswith("ltx_tag") for c in cls):
                number = _collapse(ch.text(deep=True))
                continue
            parts.append(ch.text(deep=True) or "")
        clean = _LABEL_WORD.sub("", _collapse(number)).strip().rstrip(".")
        return clean, _collapse("".join(parts))

    def _level_of(self, node: LexborNode, default: int) -> int:
        cls = _classes(node)
        if "ltx_subsubsection" in cls:
            return 3
        if "ltx_subsection" in cls:
            return 2
        if "ltx_section" in cls or "ltx_appendix" in cls:
            return 1
        return min(default, 4)

    def _child_path(self, parent_path: str, node: LexborNode, number: str, index: int) -> str:
        if "ltx_bibliography" in _classes(node):
            return "refs"
        if number:
            return _PATH_UNSAFE.sub("", number.replace(".", "-"))
        base = f"s{index}"
        return f"{parent_path}-{base}" if parent_path else base

    # ---- インライン ----
    def _inlines(self, node: LexborNode, skip: tuple[str, ...] = ()) -> list[Inline]:
        out: list[Inline] = []
        for ch in node.iter(include_text=True):
            if ch.tag == "-text":
                raw = ch.text(deep=False) or ""
                if raw.strip():
                    out.append(Inline(t="text", v=_WS.sub(" ", raw)))
                elif raw:
                    out.append(Inline(t="text", v=" "))
                continue
            cls = _classes(ch)
            if skip and (cls & set(skip)):
                continue
            if any(c.startswith("ltx_tag") for c in cls):
                continue
            out.extend(self._inline_element(ch))
        return _merge_text(out)

    def _inline_element(self, node: LexborNode) -> list[Inline]:
        cls = _classes(node)
        tag = node.tag
        if tag == "math":
            return [Inline(t="math_inline", v=_math_latex(node))]
        if tag == "cite" or "ltx_cite" in cls:
            visible = _compact_citation_text(node.text())
            anchors = [a for a in node.css("a") if (a.attributes.get("href") or "").startswith("#")]

            def cite_value(anchor: LexborNode) -> str:
                anchor_text = _compact_citation_text(anchor.text())
                if len(anchors) > 1:
                    return anchor_text or visible
                return visible or anchor_text

            cites = [
                Inline(
                    t="citation",
                    ref=(a.attributes.get("href") or "")[1:],
                    v=cite_value(a),
                )
                for a in anchors
            ]
            if cites:
                return cites
            txt = visible
            return [Inline(t="text", v=txt)] if txt else []
        if tag == "a":
            return self._anchor(node, cls)
        if "ltx_role_footnote" in cls:
            return [self._make_footnote(node)]
        if (
            tag in ("em", "i", "strong", "b")
            or "ltx_emph" in cls
            or "ltx_font_italic" in cls
            or "ltx_font_bold" in cls
        ):
            txt = _collapse(node.text())
            return [Inline(t="emphasis", v=txt)] if txt else []
        if tag in ("code", "tt", "kbd", "samp") or "ltx_font_typewriter" in cls:
            txt = _collapse(node.text())
            return [Inline(t="code_inline", v=txt)] if txt else []
        # 透過(span.ltx_text 等)→ 子を再帰
        return self._inlines(node)

    def _anchor(self, node: LexborNode, cls: frozenset[str]) -> list[Inline]:
        href = node.attributes.get("href") or ""
        txt = _collapse(node.text())
        if href.startswith("#bib"):
            return [Inline(t="citation", ref=href[1:], v=_compact_citation_text(txt))]
        if href.startswith("#"):
            target = href[1:]
            return [Inline(t="ref", kind=_ref_kind(target), ref=target, v=txt)]
        if "ltx_url" in cls or href.startswith(("http://", "https://", "mailto:", "ftp://")):
            return [Inline(t="url", v=txt or href, href=href)]
        return [Inline(t="text", v=txt)] if txt else []

    def _make_footnote(self, node: LexborNode) -> Inline:
        self._fn_counter += 1
        n = self._fn_counter
        content = node.css_first(".ltx_note_content") or node
        inlines = self._inlines(content, skip=("ltx_note_mark",))
        block = Block(id="", type="footnote", label=f"footnote{n}", inlines=inlines)
        if self._fn_stack:
            self._fn_stack[-1].append(block)
        return Inline(t="footnote_ref", ref=f"footnote{n}")

    def _flatten_inlines(self, node: LexborNode) -> list[Inline]:
        paras = node.css("p.ltx_p") or node.css("p")
        if paras:
            inlines: list[Inline] = []
            for p in paras:
                inlines.extend(self._inlines(p))
            return _merge_text(inlines)
        return self._inlines(node)

    # ---- ブロック ----
    def _eq_number(self, node: LexborNode) -> str | None:
        tag = node.css_first(".ltx_tag_equation") or node.css_first(".ltx_tag")
        if tag is None:
            return None
        m = re.search(r"\d+", tag.text() or "")
        return m.group() if m else None

    def _equation(self, node: LexborNode) -> Block:
        math = node if node.tag == "math" else node.css_first("math")
        latex = _math_latex(math) if math is not None else _collapse(node.text())
        return Block(
            id="",
            type="equation",
            latex=latex,
            number=self._eq_number(node),
            label=node.attributes.get("id") or None,
        )

    def _equationgroup(self, node: LexborNode) -> list[Block]:
        blocks: list[Block] = []
        for tr in node.css("tr"):
            math = tr.css_first("math")
            if math is None:
                continue
            blocks.append(
                Block(
                    id="",
                    type="equation",
                    latex=_math_latex(math),
                    number=self._eq_number(tr) or self._eq_number(node),
                    label=tr.attributes.get("id") or node.attributes.get("id") or None,
                )
            )
        return blocks or [self._equation(node)]

    def _caption(self, node: LexborNode) -> tuple[list[Inline], str | None]:
        cap = node.css_first("figcaption") or node.css_first(".ltx_caption")
        if cap is None:
            return [], None
        number: str | None = None
        inlines: list[Inline] = []
        for ch in cap.iter(include_text=True):
            if ch.tag == "-text":
                raw = ch.text(deep=False) or ""
                if raw.strip():
                    inlines.append(Inline(t="text", v=_WS.sub(" ", raw)))
                elif raw:
                    inlines.append(Inline(t="text", v=" "))
                continue
            cls = _classes(ch)
            if any(c.startswith("ltx_tag") for c in cls):
                m = re.search(r"\d+", ch.text() or "")
                if m:
                    number = m.group()
                continue
            inlines.extend(self._inline_element(ch))
        return _merge_text(inlines), number

    def _figure(self, node: LexborNode) -> list[Block]:
        caption, number = self._caption(node)
        label = node.attributes.get("id") or None
        visuals: list[tuple[str, LexborNode]] = []
        for element in node.traverse(include_text=False):
            tag = str(element.tag or "").casefold()
            if tag == "svg":
                if not _is_visual_inside_caption(element, node) and not _has_svg_ancestor(
                    element, node
                ):
                    visuals.append(("svg", element))
            elif tag == "img" and _is_safe_fallback_image(element, node):
                visuals.append(("img", element))

        wrapper_members: dict[int, list[tuple[str, LexborNode]]] = {}
        for kind, visual in visuals:
            wrapper = _fallback_wrapper(visual, node)
            if wrapper is not None:
                wrapper_members.setdefault(wrapper.mem_id, []).append((kind, visual))

        slots: list[tuple[LexborNode | None, LexborNode | None]] = []
        for kind, visual in visuals:
            if kind == "svg":
                slots.append((visual, None))
                continue

            paired = False
            wrapper = _fallback_wrapper(visual, node)
            if wrapper is not None:
                members = wrapper_members.get(wrapper.mem_id, [])
                structurally_paired = (
                    len(members) == 2
                    and {member_kind for member_kind, _member in members} == {"svg", "img"}
                )
            else:
                structurally_paired = False
            if slots and slots[-1][0] is not None and slots[-1][1] is None:
                svg = slots[-1][0]
                svg_wrapper = _fallback_wrapper(svg, node)
                same_wrapper = (
                    structurally_paired
                    and svg_wrapper is not None
                    and wrapper is not None
                    and svg_wrapper.mem_id == wrapper.mem_id
                )
                if same_wrapper or _is_explicit_raster_fallback(visual):
                    slots[-1] = (svg, visual)
                    paired = True
            if not paired:
                slots.append((None, visual))

        has_active_content = any(
            str(element.tag or "").casefold() in _ACTIVE_INLINE_TAGS
            for element in node.traverse(include_text=False)
        )
        if not slots:
            slots.append((None, None))
        blocks: list[Block] = []
        for index, (slot_svg, slot_image) in enumerate(slots):
            raw = _safe_inline_figure_html(
                slot_svg.html if slot_svg is not None else None
            )
            if has_active_content:
                raw = None
            blocks.append(
                Block(
                    id="",
                    type="figure",
                    asset_key=(slot_image.attributes.get("src") or None)
                    if slot_image is not None
                    else None,
                    raw=raw,
                    caption=caption if index == 0 else [],
                    number=number if index == 0 else None,
                    label=label if index == 0 else None,
                )
            )
        return blocks

    def _table(self, node: LexborNode) -> Block:
        tabular = node.css_first("table.ltx_tabular") or node.css_first("table")
        content_html = tabular.html if tabular is not None else None
        caption, number = self._caption(node)
        # セル構造 HTML は raw に保持(Block モデルを再利用。plans/05 §4.2 content_html)。
        return Block(
            id="",
            type="table",
            raw=content_html,
            caption=caption,
            number=number,
            label=node.attributes.get("id") or None,
        )

    def _algorithm(self, node: LexborNode) -> Block:
        caption, number = self._caption(node)
        body = node.css_first(".ltx_listing") or node.css_first("pre")
        text = _collapse((body or node).text(deep=True))
        return Block(
            id="",
            type="algorithm",
            inlines=[Inline(t="text", v=text)] if text else [],
            caption=caption,
            number=number,
            label=node.attributes.get("id") or None,
        )

    def _code(self, node: LexborNode) -> Block:
        text = (node.text(deep=True) or "").strip("\n")
        return Block(id="", type="code", code=text, language=None)

    def _list(self, node: LexborNode) -> Block:
        ordered = node.tag == "ol" or "ltx_enumerate" in _classes(node)
        items: list[list[Inline]] = []
        for li in _element_children(node):
            if li.tag != "li" and "ltx_item" not in _classes(li):
                continue
            inl = self._inlines(li)
            if inl:
                items.append(inl)
        return Block(id="", type="list", ordered=ordered, items=items)

    def _quote(self, node: LexborNode) -> Block:
        return Block(id="", type="quote", inlines=self._flatten_inlines(node))

    def _theorem(self, node: LexborNode) -> Block:
        heading = self._find_heading(node)
        # 種別名+番号(例「Theorem 1」)を丸ごと保持する(docs/01 §4.1・plans/05 §4.2)。
        title = _collapse(heading.text()).rstrip(" .") if heading is not None else ""
        inlines: list[Inline] = []
        for ch in _element_children(node):
            if ch is heading or self._is_heading_el(ch):
                continue
            inlines.extend(self._flatten_inlines(ch))
        return Block(
            id="",
            type="theorem",
            title=title or None,
            label=node.attributes.get("id") or None,
            inlines=_merge_text(inlines),
        )

    def _blocks_from_element(self, node: LexborNode) -> list[Block]:
        cls = _classes(node)
        tag = node.tag
        if self._is_skip(node):
            return []
        if "ltx_para" in cls:
            return self._blocks_from_children(node)
        if "ltx_equationgroup" in cls:
            return self._equationgroup(node)
        if "ltx_equation" in cls or tag == "math":
            return [self._equation(node)]
        if tag == "figure" or cls & {"ltx_figure", "ltx_table", "ltx_float"}:
            if "ltx_table" in cls:
                return [self._table(node)]
            if "ltx_float_algorithm" in cls or "ltx_algorithm" in cls:
                return [self._algorithm(node)]
            return self._figure(node)
        if "ltx_algorithm" in cls:
            return [self._algorithm(node)]
        if "ltx_listing" in cls or "ltx_verbatim" in cls or tag == "pre":
            return [self._code(node)]
        if tag in ("ul", "ol") or cls & {"ltx_itemize", "ltx_enumerate"}:
            return [self._list(node)]
        if tag == "blockquote" or "ltx_quote" in cls:
            return [self._quote(node)]
        if "ltx_theorem" in cls:
            return [self._theorem(node)]
        if self._is_heading_el(node):
            return []  # セクション見出しは _process_section で生成済み
        if tag == "p" or "ltx_p" in cls:
            inl = self._inlines(node)
            return [Block(id="", type="paragraph", inlines=inl)] if inl else []
        # 未知のコンテナ: 要素の子があれば再帰、なければ本文があれば段落。
        if _element_children(node):
            return self._blocks_from_children(node)
        inl = self._inlines(node)
        return [Block(id="", type="paragraph", inlines=inl)] if inl else []

    def _blocks_from_children(self, node: LexborNode) -> list[Block]:
        out: list[Block] = []
        for ch in _element_children(node):
            if self._is_heading_el(ch):
                continue
            out.extend(self._blocks_from_element(ch))
        return out

    # ---- セクション ----
    def _structure_reference(self, raw: str) -> dict[str, str] | None:
        out: dict[str, str] = {}
        am = _ARXIV_RE.search(raw)
        if am:
            out["arxiv_id"] = am.group(1)
        ym = _YEAR_PAREN_RE.search(raw)
        if ym:
            out["year"] = ym.group()[1:-1]
        else:
            ym2 = _YEAR_RE.search(raw)
            if ym2:
                out["year"] = ym2.group()
        tm = _TITLE_QUOTE_RE.search(raw)
        if tm:
            out["title"] = tm.group(1).strip()
        else:
            parts = re.split(r"\.\s+", raw)
            if len(parts) >= 2:
                out["title"] = parts[1].strip()
        dm = _DOI_RE.search(raw)
        if dm:
            out["doi"] = dm.group(1).rstrip(".")
        return out or None

    def _bibliography(self, node: LexborNode) -> Section:
        _, title = self._heading_number_title(self._find_heading(node))
        title = title or "References"
        sec = Section(id="sec-refs", heading=SectionHeading(number="", title=title))
        sec.blocks.append(Block(id="", type="heading", level=1, title=title))
        items = node.css("li.ltx_bibitem") or node.css("li")
        for li in items:
            raw = _collapse(li.text(deep=True))
            if not raw:
                continue
            sec.blocks.append(
                Block(
                    id="",
                    type="reference_entry",
                    raw=raw,
                    label=li.attributes.get("id") or None,
                    structured=self._structure_reference(raw),
                )
            )
        return sec

    def _process_section(self, node: LexborNode, level: int, path: str) -> Section:
        if "ltx_bibliography" in _classes(node):
            return self._bibliography(node)
        heading = self._find_heading(node)
        number, title = self._heading_number_title(heading)
        sec = Section(id=f"sec-{path}", heading=SectionHeading(number=number, title=title))
        if title or number:
            sec.blocks.append(
                Block(
                    id="",
                    type="heading",
                    level=level,
                    number=number or None,
                    title=title or None,
                )
            )
        self._fn_stack.append([])
        child_index = 0
        for ch in _element_children(node):
            if ch is heading or self._is_heading_el(ch):
                continue
            if self._is_skip(ch):
                continue
            if self._is_section_container(ch):
                sub_number, _ = self._heading_number_title(self._find_heading(ch))
                sub_path = self._child_path(path, ch, sub_number, child_index)
                child_index += 1
                sub_level = self._level_of(ch, level + 1)
                sec.sections.append(self._process_section(ch, sub_level, sub_path))
            else:
                sec.blocks.extend(self._blocks_from_element(ch))
        sec.blocks.extend(self._fn_stack.pop())
        return sec

    def parse(self, html: str) -> ParsedDocument:
        tree = LexborHTMLParser(html)
        container = tree.css_first("article.ltx_document") or tree.body or tree.root
        if container is None:
            return ParsedDocument(sections=[], warnings=self.warnings)
        sections: list[Section] = []
        pending: list[Block] = []
        order = 0
        self._fn_stack.append([])
        for ch in _element_children(container):
            if ch.tag == "h1":
                continue
            if self._is_skip(ch):
                continue
            if self._is_section_container(ch):
                if pending:
                    fns = self._fn_stack.pop()
                    intro = Section(id=f"sec-s{order}", heading=SectionHeading())
                    intro.blocks.extend(pending)
                    intro.blocks.extend(fns)
                    sections.append(intro)
                    order += 1
                    pending = []
                    self._fn_stack.append([])
                number, _ = self._heading_number_title(self._find_heading(ch))
                path = self._child_path("", ch, number, order)
                sections.append(self._process_section(ch, self._level_of(ch, 1), path))
                order += 1
            else:
                pending.extend(self._blocks_from_element(ch))
        fns = self._fn_stack.pop()
        if pending or fns:
            intro = Section(id=f"sec-s{order}", heading=SectionHeading())
            intro.blocks.extend(pending)
            intro.blocks.extend(fns)
            sections.append(intro)
        assign_block_ids(sections)
        return ParsedDocument(sections=sections, warnings=self.warnings)


def parse_arxiv_html(html: str) -> ParsedDocument:
    """arXiv/ar5iv の LaTeXML HTML を構造化ドキュメントへパースする(plans/05 §4)。"""
    return _ArxivHtmlParser().parse(html)
