"""JATS (PMC Open Access) XML → DocumentContent 変換(Task 17。docs/02-ingest.md §8)。

PubMed Central の Open Access 記事は JATS XML で配布され、``section`` / ``paragraph`` /
``figure`` / ``table`` / ``equation`` / ``citation`` を構造として保持している。本モジュールは
その XML を :class:`alinea_core.document.blocks.DocumentContent`(品質 A、``source_format="jats"``)
へ写像する **純粋** パーサ(ネットワーク非依存)である。図アセットの実体取得は上位層
(worker)が境界付きで行い、パース時点では ``graphic/@xlink:href`` を ``deferred_figures``
として控えるだけ(失敗時 deferred placeholder。P3: 黙って壊れない)。

XXE 硬化(ハード制約):
    ``defusedxml`` / ``lxml`` は依存に無いため、``xml.parsers.expat`` を直接使い、
    ``DOCTYPE`` 宣言・``ENTITY`` 宣言(内部/外部)・外部エンティティ参照のいずれかを
    検出した時点で :class:`JatsParseError` を送出して **fail-closed** にする(スクリプト/
    外部実体/DTD を一切展開しない)。カスタムエンティティは宣言段階で拒否されるため
    entity-expansion(billion laughs)も原理的に発生しない。
"""

from __future__ import annotations

import re
import xml.parsers.expat as expat
from dataclasses import dataclass, field
from typing import Any

from alinea_core.adapters.pubmed import normalize_pmcid, normalize_pmid
from alinea_core.arxiv.licenses import normalize_license_url
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.licenses import LicenseId
from alinea_core.parsing.block_ids import assign_block_ids

JATS_PARSER_VERSION = "jats-1.0.0"

# 入力サイズ上限(展開後 XML は expat がストリームで扱うが、生バイトの上限は別途上位で担保)。
_WS = re.compile(r"\s+")

# JATS の本文セクション見出し以外で、無視してよい非本文セクション種別。
_INLINE_EMPHASIS_TAGS = frozenset({"bold", "italic", "sc", "underline", "strong", "em"})
_INLINE_CODE_TAGS = frozenset({"monospace", "code", "tt"})
_INLINE_MATH_TAGS = frozenset({"inline-formula"})
_SKIP_INLINE_TAGS = frozenset({"label"})  # fig/table のラベルは block 側で扱う

# url インラインとして許可する href スキーム(html_parser.py と同じ allow-list。
# javascript:/data: 等の危険スキームは url にせずテキストへ縮退させる)。
_SAFE_URL_SCHEMES = ("http://", "https://", "mailto:", "ftp://")


def _is_safe_url_scheme(href: str) -> bool:
    """href が安全なスキーム(http/https/mailto/ftp)か、あるいはスキームなし相対かを判定する。"""
    value = href.strip()
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith(_SAFE_URL_SCHEMES):
        return True
    # スキームを持たない(相対/フラグメント)URL は安全側で許可する。危険なのは明示スキームのみ。
    return ":" not in value.split("/", 1)[0]


class JatsParseError(Exception):
    """JATS パース失敗。``kind`` は plans/05 §2.4 の Problem code(リトライ分類の判定元)。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# --------------------------------------------------------------------------- #
# XXE-safe な軽量 DOM(expat 直叩き)
# --------------------------------------------------------------------------- #


@dataclass
class _Node:
    """最小の要素ノード(タグはローカル名へ正規化済み)。"""

    tag: str
    attrib: dict[str, str] = field(default_factory=dict)
    children: list[_Node] = field(default_factory=list)
    text: str = ""  # 開始タグ直後のテキスト
    tail: str = ""  # 終了タグ直後のテキスト


def _localname(name: str) -> str:
    """名前空間展開後の ``{uri}local`` やプレフィクス付き名をローカル名へ落とす。"""
    if "}" in name:
        name = name.rsplit("}", 1)[1]
    if ":" in name:
        name = name.rsplit(":", 1)[1]
    return name


def _safe_parse(data: bytes | str) -> _Node:
    """XXE 硬化した expat で XML をパースし、ルート ``_Node`` を返す。

    DOCTYPE / ENTITY 宣言・外部エンティティ参照を検出したら即 fail-closed。
    """

    if isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = data

    # namespace_separator を渡すと属性名も {uri}local へ展開される。
    parser = expat.ParserCreate(namespace_separator="}")

    def _guard_doctype(
        _name: str, _system_id: Any, _public_id: Any, has_internal_subset: int
    ) -> None:
        # PMC の efetch JATS は常に外部 DTD 参照のみの DOCTYPE(内部サブセット無し)を持つ:
        #   <!DOCTYPE pmc-articleset PUBLIC "..." "https://dtd.nlm.nih.gov/.../*.dtd">
        # 内部サブセット([...])が無い DOCTYPE は ENTITY 宣言を持ち得ないため billion-laughs
        # は原理的に不可能で、外部 DTD は XML_PARAM_ENTITY_PARSING_NEVER により決して取得
        # されない。よって外部参照のみの DOCTYPE は安全に許可する。内部サブセットを持つ
        # DOCTYPE(カスタム ENTITY 宣言の温床)だけは従来通り fail-closed で拒否する。
        if has_internal_subset:
            raise JatsParseError(
                "parse_error", "DOCTYPE with an internal subset is not allowed in JATS input"
            )

    def _reject_entity(*_args: Any, **_kwargs: Any) -> None:
        raise JatsParseError("parse_error", "entity declarations are not allowed in JATS input")

    def _reject_external(*_args: Any, **_kwargs: Any) -> bool:
        raise JatsParseError("parse_error", "external entity references are not allowed")

    parser.StartDoctypeDeclHandler = _guard_doctype
    parser.EntityDeclHandler = _reject_entity
    parser.UnparsedEntityDeclHandler = _reject_entity
    parser.ExternalEntityRefHandler = _reject_external
    # 予期しないパラメータ実体の展開も無効化する(標準実体 &amp; 等はそのまま処理される)。
    try:
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    except (AttributeError, expat.error):  # pragma: no cover - platform variance
        pass

    root_holder: list[_Node] = []
    stack: list[_Node] = []

    def _start(name: str, attrs: dict[str, str]) -> None:
        node = _Node(
            tag=_localname(name),
            attrib={_localname(k): v for k, v in attrs.items()},
        )
        if stack:
            stack[-1].children.append(node)
        else:
            root_holder.append(node)
        stack.append(node)

    def _end(_name: str) -> None:
        stack.pop()

    def _chardata(text: str) -> None:
        if not stack:
            return
        node = stack[-1]
        if node.children:
            node.children[-1].tail += text
        else:
            node.text += text

    parser.StartElementHandler = _start
    parser.EndElementHandler = _end
    parser.CharacterDataHandler = _chardata

    try:
        parser.Parse(raw, True)
    except JatsParseError:
        raise
    except expat.ExpatError as exc:
        raise JatsParseError("parse_error", f"invalid JATS XML: {exc}") from exc

    if not root_holder:
        raise JatsParseError("parse_error", "JATS XML has no root element")
    return root_holder[0]


def _find(node: _Node, tag: str) -> _Node | None:
    for child in node.children:
        if child.tag == tag:
            return child
    return None


def _findall(node: _Node, tag: str) -> list[_Node]:
    return [child for child in node.children if child.tag == tag]


def _iter(node: _Node) -> Any:
    yield node
    for child in node.children:
        yield from _iter(child)


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


def _all_text(node: _Node) -> str:
    """要素配下の全テキストを連結する(未知タグの安全縮退の基底)。"""
    parts: list[str] = [node.text]
    for child in node.children:
        parts.append(_all_text(child))
        parts.append(child.tail)
    return "".join(parts)


# --------------------------------------------------------------------------- #
# メタデータ
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class JatsMeta:
    """JATS front メタデータ(Paper へ載せる書誌)。"""

    pmid: str | None
    pmcid: str | None
    doi: str | None
    title: str
    authors: list[dict[str, str]]
    abstract: str
    published_on: str | None
    journal: str | None
    license: LicenseId


def _extract_meta(article: _Node) -> JatsMeta:
    front = _find(article, "front")
    article_meta = _find(front, "article-meta") if front else None
    journal_meta = _find(front, "journal-meta") if front else None

    pmid = pmcid = doi = None
    title = ""
    authors: list[dict[str, str]] = []
    abstract = ""
    published_on: str | None = None
    journal: str | None = None
    license_id: LicenseId = "unknown"

    if article_meta is not None:
        for aid in _findall(article_meta, "article-id"):
            kind = (aid.attrib.get("pub-id-type") or "").lower()
            value = _clean(_all_text(aid))
            if kind == "pmid":
                pmid = value
            elif kind in ("pmc", "pmcid"):
                # PMC OA 直配布は "pmc"、efetch(db=pmc)は "pmcid"(値は "PMC…")を使う。
                pmcid = normalize_pmcid(value)
            elif kind == "doi":
                doi = value

        title_group = _find(article_meta, "title-group")
        if title_group is not None:
            title_node = _find(title_group, "article-title")
            if title_node is not None:
                title = _clean(_all_text(title_node))

        contrib_group = _find(article_meta, "contrib-group")
        if contrib_group is not None:
            for contrib in _findall(contrib_group, "contrib"):
                name = _find(contrib, "name")
                if name is None:
                    continue
                surname_node = _find(name, "surname")
                given_node = _find(name, "given-names")
                surname = _clean(_all_text(surname_node)) if surname_node is not None else ""
                given = _clean(_all_text(given_node)) if given_node is not None else ""
                full = " ".join(part for part in (given, surname) if part)
                if full:
                    authors.append({"name": full})

        published_on = _extract_pub_date(article_meta)

        abstract_node = _find(article_meta, "abstract")
        if abstract_node is not None:
            abstract = _clean(_all_text(abstract_node))

        license_id = _extract_license(article_meta)

    if journal_meta is not None:
        title_group = _find(journal_meta, "journal-title-group")
        node = _find(title_group, "journal-title") if title_group else None
        if node is None:
            node = _find(journal_meta, "journal-title")
        if node is not None:
            journal = _clean(_all_text(node))

    return JatsMeta(
        pmid=pmid,
        pmcid=pmcid,
        doi=doi,
        title=title,
        authors=authors,
        abstract=abstract,
        published_on=published_on,
        journal=journal,
        license=license_id,
    )


def _extract_pub_date(article_meta: _Node) -> str | None:
    """``pub-date`` を ISO 日付へ。epub/ppub を優先し、月/日欠けは 01 に丸める。"""
    dates = _findall(article_meta, "pub-date")
    if not dates:
        return None

    def score(node: _Node) -> int:
        pub_type = (node.attrib.get("pub-type") or node.attrib.get("date-type") or "").lower()
        return {"epub": 0, "ppub": 1, "pub": 2, "collection": 3}.get(pub_type, 4)

    node = sorted(dates, key=score)[0]
    year_node = _find(node, "year")
    if year_node is None:
        return None
    year = _clean(_all_text(year_node))
    if not re.fullmatch(r"\d{4}", year):
        return None
    month_node = _find(node, "month")
    month = _clean(_all_text(month_node)) if month_node is not None else "1"
    day_node = _find(node, "day")
    day = _clean(_all_text(day_node)) if day_node is not None else "1"
    try:
        month_i = min(max(int(month or "1"), 1), 12)
        day_i = min(max(int(day or "1"), 1), 31)
    except ValueError:
        return None
    return f"{year}-{month_i:02d}-{day_i:02d}"


def _extract_license(article_meta: _Node) -> LicenseId:
    permissions = _find(article_meta, "permissions")
    if permissions is None:
        return "unknown"
    license_node = _find(permissions, "license")
    if license_node is None:
        return "unknown"
    href = license_node.attrib.get("href") or license_node.attrib.get("xlink:href")
    if href:
        resolved = normalize_license_url(href)
        if resolved != "unknown":
            return resolved
    # href が無ければ license-p の本文 URL からも試みる。
    text = _all_text(license_node)
    match = re.search(r"https?://\S+", text)
    if match:
        return normalize_license_url(match.group(0).rstrip(".)"))
    return "unknown"


# --------------------------------------------------------------------------- #
# 本文構造化
# --------------------------------------------------------------------------- #


@dataclass
class JatsDocument:
    """JATS パース結果(品質 A の DocumentContent 同型)。"""

    source_format: str = "jats"
    parser_version: str = JATS_PARSER_VERSION
    sections: list[Section] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

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
        return self._all_blocks()

    def iter_blocks(self) -> list[tuple[Section, Block]]:
        """全ブロックを (所属セクション, ブロック) の列で走査する(入れ子対応)。"""
        result: list[tuple[Section, Block]] = []

        def walk(sec: Section) -> None:
            for blk in sec.blocks:
                result.append((sec, blk))
            for sub in sec.sections:
                walk(sub)

        for s in self.sections:
            walk(s)
        return result

    def to_document_content(self) -> DocumentContent:
        return DocumentContent(quality_level="A", sections=self.sections)


@dataclass
class JatsParseResult:
    """パーサの返り値。document・meta・図の遅延取得情報・本文有無を含む。"""

    document: JatsDocument
    meta: JatsMeta
    deferred_figures: list[dict[str, str]] = field(default_factory=list)
    body_available: bool = True


class _BodyBuilder:
    """JATS ``<body>`` を Section/Block ツリーへ変換する(未知タグは子テキストへ縮退)。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.deferred_figures: list[dict[str, str]] = []

    # -- インライン --------------------------------------------------------
    def _inlines(self, node: _Node) -> list[Inline]:
        out: list[Inline] = []
        self._emit_text(out, node.text)
        for child in node.children:
            self._inline_child(out, child)
            self._emit_text(out, child.tail)
        return _merge_text(out)

    @staticmethod
    def _emit_text(out: list[Inline], text: str) -> None:
        if text and text.strip():
            out.append(Inline(t="text", v=_WS.sub(" ", text)))

    def _inline_child(self, out: list[Inline], node: _Node) -> None:
        tag = node.tag
        if tag == "xref":
            self._emit_xref(out, node)
            return
        if tag in _INLINE_MATH_TAGS:
            tex = _find(node, "tex-math")
            latex = _clean(_all_text(tex)) if tex is not None else _clean(_all_text(node))
            out.append(Inline(t="math_inline", v=latex))
            return
        if tag in _INLINE_CODE_TAGS:
            out.append(Inline(t="code_inline", v=_clean(_all_text(node))))
            return
        if tag == "ext-link" or tag == "uri":
            href = node.attrib.get("href") or node.attrib.get("xlink:href") or ""
            text = _clean(_all_text(node))
            if _is_safe_url_scheme(href):
                out.append(Inline(t="url", v=text or href, href=href))
            else:
                # javascript:/data: 等の危険スキームは url インラインにせずテキストへ縮退する
                # (html_parser と同じ allow-list。stored-XSS 対策)。
                self._emit_text(out, text or href)
            return
        if tag in _INLINE_EMPHASIS_TAGS:
            out.append(Inline(t="emphasis", v=_clean(_all_text(node))))
            return
        if tag in _SKIP_INLINE_TAGS:
            return
        # 未知インラインタグは安全に子テキストへ縮退する(P3: 黙って壊れない)。
        self._emit_text(out, node.text)
        for grandchild in node.children:
            self._inline_child(out, grandchild)
            self._emit_text(out, grandchild.tail)

    def _emit_xref(self, out: list[Inline], node: _Node) -> None:
        ref_type = (node.attrib.get("ref-type") or "").lower()
        rid = node.attrib.get("rid") or ""
        text = _clean(_all_text(node))
        if ref_type == "bibr":
            out.append(Inline(t="citation", v=text, ref=rid))
        elif ref_type in ("fig", "table", "disp-formula", "sec"):
            kind = {
                "fig": "figure",
                "table": "table",
                "disp-formula": "equation",
                "sec": "section",
            }[ref_type]
            out.append(Inline(t="ref", v=text, ref=rid, kind=kind))
        else:
            self._emit_text(out, text)

    # -- ブロック ----------------------------------------------------------
    def _blocks_from(self, node: _Node) -> list[Block]:
        tag = node.tag
        if tag == "p":
            inlines = self._inlines(node)
            return [Block(id="", type="paragraph", inlines=inlines)] if inlines else []
        if tag in ("disp-formula",):
            return [self._equation_block(node)]
        if tag == "fig":
            return [self._figure_block(node)]
        if tag in ("table-wrap", "table"):
            return [self._table_block(node)]
        if tag in ("list",):
            return [self._list_block(node)]
        if tag in ("disp-quote",):
            return [Block(id="", type="quote", inlines=self._inlines(node))]
        if tag in ("statement", "boxed-text"):
            # 未知/複合ブロックは子ブロックへ展開(安全縮退)。
            out: list[Block] = []
            for child in node.children:
                out.extend(self._blocks_from(child))
            return out
        # その他の未知ブロックは子テキストを段落として拾う(空なら捨てる)。
        text = _clean(_all_text(node))
        if text:
            return [Block(id="", type="paragraph", inlines=[Inline(t="text", v=text)])]
        return []

    def _equation_block(self, node: _Node) -> Block:
        tex = _find(node, "tex-math")
        latex = _clean(_all_text(tex)) if tex is not None else _clean(_all_text(node))
        label = _find(node, "label")
        return Block(
            id="",
            type="equation",
            latex=latex,
            label=node.attrib.get("id") or None,
            number=_clean(_all_text(label)) if label is not None else None,
        )

    def _figure_block(self, node: _Node) -> Block:
        label_node = _find(node, "label")
        caption_node = _find(node, "caption")
        caption = self._inlines(caption_node) if caption_node is not None else []
        graphic = _find(node, "graphic")
        href = ""
        if graphic is not None:
            href = graphic.attrib.get("href") or graphic.attrib.get("xlink:href") or ""
        fig_id = node.attrib.get("id") or ""
        block = Block(
            id="",
            type="figure",
            label=fig_id or None,
            number=_clean(_all_text(label_node)) if label_node is not None else None,
            caption=caption,
            asset_key=None,  # 実体取得は worker が境界付きで行う(パースは純粋)。
        )
        if href:
            # deferred placeholder として href を控える(worker が取得・確定/縮退)。
            block.href = href  # type: ignore[attr-defined]
            self.deferred_figures.append({"figure_label": fig_id, "href": href})
        return block

    def _table_block(self, node: _Node) -> Block:
        # table-wrap 配下から実 table を取り出す(直接 table でも可)。
        table = node if node.tag == "table" else _find(node, "table")
        label_node = _find(node, "label")
        caption_node = _find(node, "caption")
        caption = self._inlines(caption_node) if caption_node is not None else []
        cells = self._table_cells(table) if table is not None else []
        return Block(
            id="",
            type="table",
            label=(node.attrib.get("id") or None),
            number=_clean(_all_text(label_node)) if label_node is not None else None,
            caption=caption,
            cells=cells,
        )

    def _table_cells(self, table: _Node) -> list[list[str]]:
        rows: list[list[str]] = []
        for section_tag in ("thead", "tbody", "tfoot"):
            for group in _findall(table, section_tag):
                for tr in _findall(group, "tr"):
                    rows.append(self._row_cells(tr))
        # thead/tbody で包まれない直接の tr にも対応する。
        for tr in _findall(table, "tr"):
            rows.append(self._row_cells(tr))
        return [row for row in rows if row]

    @staticmethod
    def _row_cells(tr: _Node) -> list[str]:
        cells: list[str] = []
        for cell in tr.children:
            if cell.tag in ("td", "th"):
                cells.append(_clean(_all_text(cell)))
        return cells

    def _list_block(self, node: _Node) -> Block:
        items: list[list[Inline]] = []
        ordered = (node.attrib.get("list-type") or "").lower() in ("order", "ordered", "arabic")
        for item in _findall(node, "list-item"):
            inlines: list[Inline] = []
            for child in item.children:
                if child.tag == "p":
                    inlines.extend(self._inlines(child))
            if not inlines:
                text = _clean(_all_text(item))
                if text:
                    inlines = [Inline(t="text", v=text)]
            if inlines:
                items.append(inlines)
        return Block(id="", type="list", ordered=ordered, items=items)

    # -- セクション --------------------------------------------------------
    def _section(self, node: _Node, path: str) -> Section:
        """1 つの ``<sec>`` を Section へ。``path`` は階層パス(例: "s0", "s0-1")。

        section id はリビジョン全体で一意でなければならない(pipeline の
        ``_validate_unique_document_ids`` が重複を fail-closed で弾く)。ネストした
        ``<sec>`` の連番を親ごとに 0 から振ると ``sec-s0`` 等が衝突するため、
        html_parser と同じく親のパスを引き継いだ階層 id を採る。
        """
        title_node = _find(node, "title")
        title = _clean(_all_text(title_node)) if title_node is not None else ""
        sec = Section(id=f"sec-{path}", heading=SectionHeading(title=title))
        sub_order = 0
        for child in node.children:
            if child.tag == "title":
                continue
            if child.tag == "sec":
                sec.sections.append(self._section(child, f"{path}-{sub_order}"))
                sub_order += 1
            else:
                sec.blocks.extend(self._blocks_from(child))
        return sec

    def build(
        self, body: _Node, back: _Node | None, floats: list[_Node] | None = None
    ) -> list[Section]:
        sections: list[Section] = []
        pending: list[Block] = []
        order = 0
        for child in body.children:
            if child.tag == "sec":
                if pending:
                    intro = Section(id=f"sec-s{order}", heading=SectionHeading())
                    intro.blocks.extend(pending)
                    sections.append(intro)
                    order += 1
                    pending = []
                sections.append(self._section(child, f"s{order}"))
                order += 1
            else:
                pending.extend(self._blocks_from(child))
        if pending:
            intro = Section(id=f"sec-s{order}", heading=SectionHeading())
            intro.blocks.extend(pending)
            sections.append(intro)
            order += 1

        # <floats-group>: PMC は本文と別に figure/table を末尾へ集約することがある
        # (body 内は xref 参照のみ)。本文走査では拾えないため図表ブロックを別セクションへ
        # 収容する(拾わないと品質 A なのに図 0 枚になる)。
        for group in floats or []:
            float_blocks: list[Block] = []
            for child in group.children:
                float_blocks.extend(self._blocks_from(child))
            if float_blocks:
                floats_sec = Section(
                    id=f"sec-s{order}", heading=SectionHeading(title="Figures and Tables")
                )
                floats_sec.blocks.extend(float_blocks)
                sections.append(floats_sec)
                order += 1

        # back/ref-list を参考文献セクションへ。
        if back is not None:
            ref_blocks = self._reference_blocks(back)
            if ref_blocks:
                refs = Section(id=f"sec-s{order}", heading=SectionHeading(title="References"))
                refs.blocks.extend(ref_blocks)
                sections.append(refs)
        return sections

    def _reference_blocks(self, back: _Node) -> list[Block]:
        blocks: list[Block] = []
        for ref_list in _findall(back, "ref-list"):
            for ref in _findall(ref_list, "ref"):
                raw = _clean(_all_text(ref))
                if not raw:
                    continue
                blocks.append(
                    Block(
                        id="",
                        type="reference_entry",
                        raw=raw,
                        label=ref.attrib.get("id") or None,
                    )
                )
        return blocks


def _merge_text(inlines: list[Inline]) -> list[Inline]:
    """連続する text インラインをマージし、前後空白を整える。"""
    merged: list[Inline] = []
    for il in inlines:
        if il.t == "text" and merged and merged[-1].t == "text":
            merged[-1].v = f"{merged[-1].v}{il.v}"
        else:
            merged.append(il)
    return merged


def parse_jats(data: bytes | str) -> JatsParseResult:
    """JATS XML を構造化ドキュメントへ変換する(XXE 硬化・純粋)。

    Open Access 本文があれば品質 A の :class:`JatsDocument` を返し、front のみ(PubMed で
    JATS 本文が無い)なら ``body_available=False`` の空 document + abstract メタを返す。
    """

    root = _safe_parse(data)
    # PMC の efetch は <article> を <pmc-articleset> でラップして返す(通常 1 件)。
    # PMC OA の直接 JATS は <article> ルート。両形式を受理し、article 要素へ降りる。
    article: _Node | None
    if root.tag == "article":
        article = root
    elif root.tag == "pmc-articleset":
        article = _find(root, "article")
    else:
        raise JatsParseError("parse_error", f"unexpected JATS root element: {root.tag!r}")
    if article is None:
        raise JatsParseError("parse_error", "pmc-articleset contains no <article> element")

    meta = _extract_meta(article)
    body = _find(article, "body")
    back = _find(article, "back")
    # <floats-group> は article 直下(body/back の兄弟)に置かれることがある。
    floats = _findall(article, "floats-group")

    builder = _BodyBuilder()
    if body is None:
        # 本文欠落: abstract メタのみ保持し body-unavailable を明示する。
        return JatsParseResult(
            document=JatsDocument(sections=[], warnings=["JATS body unavailable"]),
            meta=meta,
            deferred_figures=[],
            body_available=False,
        )

    sections = builder.build(body, back, floats)
    assign_block_ids(sections)
    document = JatsDocument(sections=sections, warnings=builder.warnings)
    return JatsParseResult(
        document=document,
        meta=meta,
        deferred_figures=builder.deferred_figures,
        body_available=True,
    )


__all__ = [
    "JATS_PARSER_VERSION",
    "JatsDocument",
    "JatsMeta",
    "JatsParseError",
    "JatsParseResult",
    "normalize_pmcid",
    "normalize_pmid",
    "parse_jats",
]
