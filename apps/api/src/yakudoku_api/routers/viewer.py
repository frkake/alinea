"""viewer — リビジョン・ドキュメント・進捗(plans/03 §6)。

ビューア初期化の複合エンドポイント(§6.1)と、構造化ドキュメント(§6.3)・単一ブロック
(§6.4)・図表(§6.5)・参考文献(§6.6)の取得。認証はすべて `session`。
リビジョン系は「public 論文 / 所有者 / 自ライブラリに存在」のいずれかで閲覧可、
それ以外は 404 `not_found`(存在を秘匿)。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import (
    Annotation,
    DocumentRevision,
    Job,
    LibraryItem,
    Note,
    Paper,
    ReadingSession,
    ResourceLink,
    TranslationSet,
    User,
)
from yakudoku_core.document.blocks import Block, DocumentContent, Section
from yakudoku_core.document.plaintext import inline_to_plain
from yakudoku_core.ingest import joblog
from yakudoku_core.translation.pipeline import (
    BLOCKING_FLAGS,
    compute_progress,
    compute_translation_scope,
    resolve_display_units,
)

from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.schemas.common import LastPosition, LibraryItemSummary, PaperBib
from yakudoku_api.schemas.viewer import (
    BlockDetail,
    BlockTranslation,
    FigureItem,
    FigurePosition,
    FiguresResponse,
    ReferenceInLibrary,
    ReferenceItem,
    ReferencesResponse,
    RevisionInfo,
    RevisionListItem,
    RevisionListResponse,
    TimelineEntry,
    TocNode,
    ViewerCounts,
    ViewerInit,
    ViewerTranslation,
    asset_url,
    author_names,
    authors_short,
    build_license_card,
)

router = APIRouter(tags=["viewer"])


# --- 認可・解決ヘルパ(translations ルータからも import する) -----------------------


async def resolve_owned_library_item(db: AsyncSession, item_id: str, user: User) -> LibraryItem:
    """自分の LibraryItem を返す。無ければ 404。"""
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user.id):
        raise ProblemException("not_found")
    return item


async def _paper_accessible(db: AsyncSession, paper: Paper, user: User) -> bool:
    if paper.visibility == "public":
        return True
    if paper.owner_user_id is not None and str(paper.owner_user_id) == str(user.id):
        return True
    owned = await db.scalar(
        select(LibraryItem.id).where(
            LibraryItem.user_id == user.id, LibraryItem.paper_id == paper.id
        )
    )
    return owned is not None


async def resolve_accessible_revision(
    db: AsyncSession, revision_id: str, user: User
) -> tuple[DocumentRevision, Paper]:
    """閲覧可能なリビジョンとその論文を返す。閲覧不可・不存在は 404。"""
    revision = await db.get(DocumentRevision, revision_id)
    if revision is None:
        raise ProblemException("not_found")
    paper = await db.get(Paper, revision.paper_id)
    if paper is None or not await _paper_accessible(db, paper, user):
        raise ProblemException("not_found")
    return revision, paper


def _as_content(revision: DocumentRevision) -> DocumentContent:
    return DocumentContent.model_validate(revision.content)


def _find_section(content: DocumentContent, section_id: str) -> Section | None:
    def walk(sec: Section) -> Section | None:
        if sec.id == section_id:
            return sec
        for sub in sec.sections:
            found = walk(sub)
            if found is not None:
                return found
        return None

    for top in content.sections:
        found = walk(top)
        if found is not None:
            return found
    return None


def _block_section_map(content: DocumentContent) -> dict[str, Section]:
    return {blk.id: sec for sec, blk in content.iter_blocks()}


def _section_display(section: Section) -> str:
    """ "§2.2 Reflow" 形式の短縮表記。number が無ければ title のみ。"""
    number = (section.heading.number or "").strip()
    title = (section.heading.title or "").strip()
    if number:
        head = f"§{number}"
        return f"{head} {title}".strip() if title else head
    return title


_REFERENCE_HEADING_RE = re.compile(
    r"\b(references|bibliography|works cited|literature cited)\b", re.IGNORECASE
)
_REF_MARKER_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)])\s*")
_REF_SPLIT_RE = re.compile(r"(?m)(?=^\s*(?:\[\d+\]|\d+[.)])\s+)")
_INLINE_REF_SPLIT_RE = re.compile(r"\s+(?=(?:\[\d+\]|\d+[.)])\s+[A-Z])")
_ARXIV_RE = re.compile(
    r"(?:arXiv:|arxiv\.org/abs/)\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.IGNORECASE
)
_YEAR_PAREN_RE = re.compile(r"\((19|20)\d{2}\)")
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_TITLE_QUOTE_RE = re.compile('[\u201c"\u2018]([^\u201d"\u2019]+)[\u201d"\u2019]')
_DOI_RE = re.compile(r"(?:doi\.org/|doi:\s*)(\S+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_LATEX_CMD_WITH_ARG_RE = re.compile(
    r"\\(?:emph|textit|textbf|textsc|texttt|mathrm|mathbf|mathit)\{([^{}]*)\}"
)
_LATEX_BARE_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?")


def _strip_light_markup(text: str) -> str:
    out = text
    prev = None
    while prev != out:
        prev = out
        out = _LATEX_CMD_WITH_ARG_RE.sub(r"\1", out)
    out = out.replace("~", " ")
    out = _LATEX_BARE_CMD_RE.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip()


def _plain_block_text(block: Block) -> str:
    if block.raw:
        return _strip_light_markup(block.raw)
    if block.inlines:
        return inline_to_plain(block.inlines)
    if block.caption:
        return inline_to_plain(block.caption)
    if block.items:
        return " ".join(inline_to_plain(item) for item in block.items)
    if block.title:
        return block.title
    if block.code:
        return block.code
    if block.latex:
        return block.latex
    return ""


def _is_reference_section(section: Section) -> bool:
    title = (section.heading.title or "").strip()
    if section.id == "sec-refs" or _REFERENCE_HEADING_RE.search(title):
        return True
    blocks = [b for b in section.blocks if b.type != "heading"]
    return bool(blocks) and all(b.type == "reference_entry" for b in blocks)


def _strip_reference_marker(raw: str) -> str:
    return _REF_MARKER_RE.sub("", raw).strip()


def _split_reference_text(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    chunks = [c.strip() for c in _REF_SPLIT_RE.split(text) if c.strip()]
    if len(chunks) <= 1:
        chunks = [c.strip() for c in _INLINE_REF_SPLIT_RE.split(text) if c.strip()]
    return [_strip_reference_marker(c) for c in chunks if _strip_reference_marker(c)]


def _infer_reference_authors(raw: str) -> list[str] | None:
    head = re.split(r"\.\s+", _strip_reference_marker(raw), maxsplit=1)[0].strip()
    if not head or len(head) > 180:
        return None
    if not re.search(r"[A-Za-z]", head):
        return None
    lower = head.lower()
    if lower.startswith(("in ", "proceedings ", "journal ")):
        return None
    return [head]


def _structure_reference_text(raw: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    am = _ARXIV_RE.search(raw)
    if am:
        out["arxiv_id"] = am.group(1)
    ym = _YEAR_PAREN_RE.search(raw)
    if ym:
        out["year"] = ym.group()[1:-1]
    else:
        matches = list(_YEAR_RE.finditer(raw))
        if matches:
            out["year"] = matches[-1].group()
    tm = _TITLE_QUOTE_RE.search(raw)
    if tm:
        out["title"] = tm.group(1).strip()
    else:
        parts = re.split(r"\.\s+", raw)
        if len(parts) >= 2:
            out["title"] = parts[1].strip()
    dm = _DOI_RE.search(raw)
    if dm:
        out["doi"] = dm.group(1).rstrip(".,;}])\"'")
    urlm = _URL_RE.search(raw)
    if urlm:
        out["url"] = urlm.group().rstrip(".,;}])\"'")
    authors = _infer_reference_authors(raw)
    if authors:
        out["authors"] = authors
    return out


def _reference_records(content: DocumentContent) -> list[dict[str, Any]]:
    explicit = [b for _s, b in content.iter_blocks() if b.type == "reference_entry"]
    if explicit:
        records: list[dict[str, Any]] = []
        for blk in explicit:
            raw = _plain_block_text(blk)
            structured = dict(blk.structured or {}) if isinstance(blk.structured, dict) else {}
            if raw:
                structured = {**_structure_reference_text(raw), **structured}
            records.append(
                {
                    "block_id": blk.id,
                    "label": blk.label,
                    "structured": structured,
                    "raw": raw,
                }
            )
        return records

    records = []
    for section in content.sections:
        stack = [section]
        while stack:
            sec = stack.pop(0)
            stack[0:0] = list(sec.sections)
            if not _is_reference_section(sec):
                continue
            for blk in sec.blocks:
                if blk.type == "heading":
                    continue
                for raw in _split_reference_text(_plain_block_text(blk)):
                    records.append(
                        {
                            "block_id": blk.id,
                            "label": blk.label,
                            "structured": _structure_reference_text(raw),
                            "raw": raw,
                        }
                    )
    return records


def _surname(author: str) -> str:
    first = re.split(r"\s+and\s+|;", author, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    if "," in first:
        candidate = first.split(",", 1)[0].strip()
    else:
        names = re.findall(r"[A-Za-z][A-Za-z'\-]*", first)
        candidate = names[-1] if names else ""
    return candidate[:1].upper() + candidate[1:] if candidate else ""


def _has_multiple_authors(structured: dict[str, Any], raw: str) -> bool:
    authors = structured.get("authors")
    if isinstance(authors, list) and len(authors) > 1:
        return True
    head = re.split(r"\.\s+", _strip_reference_marker(raw), maxsplit=1)[0]
    return bool(re.search(r"\band\b", head, re.IGNORECASE) or head.count(",") >= 2)


def _reference_citation_display(record: dict[str, Any], idx: int) -> str:
    structured = record.get("structured") if isinstance(record.get("structured"), dict) else {}
    label = structured.get("citation_label")
    if isinstance(label, str) and label.strip():
        return _strip_light_markup(label).strip("[]")
    raw = str(record.get("raw") or "")
    authors = structured.get("authors")
    first_author = ""
    if isinstance(authors, list) and authors:
        first_author = str(authors[0])
    elif isinstance(authors, str):
        first_author = authors
    if not first_author:
        inferred = _infer_reference_authors(raw) or []
        first_author = inferred[0] if inferred else ""
    surname = _surname(first_author)
    if surname:
        name = f"{surname} et al." if _has_multiple_authors(structured, raw) else surname
        year = structured.get("year")
        return f"{name} ({year})" if year else name
    return f"[{idx}]"


def _reference_aliases(record: dict[str, Any], idx: int) -> set[str]:
    fallback = f"ref-{idx}"
    aliases = {fallback, f"bib-{idx}", f"bib.{idx}", f"[{idx}]", str(idx)}
    label = record.get("label")
    if isinstance(label, str) and label:
        aliases.update({label, f"#{label}"})
    block_id = record.get("block_id")
    if isinstance(block_id, str) and block_id:
        aliases.add(block_id)
    return aliases


def _citation_label_map(records: list[dict[str, Any]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for idx, record in enumerate(records, start=1):
        display = _reference_citation_display(record, idx)
        for alias in _reference_aliases(record, idx):
            labels[alias] = display
            if alias.startswith("#"):
                labels[alias[1:]] = display
    return labels


def _display_from_cite_key(ref: str) -> str:
    m = re.search(r"([A-Za-z][A-Za-z\-]+).*?((?:19|20)\d{2})", ref)
    if m:
        author = m.group(1)
        return f"{author[:1].upper()}{author[1:]} et al. ({m.group(2)})"
    return "Reference"


def _fallback_ref_display(ref: str, kind: str | None) -> str:
    match = re.search(r"(\d+(?:\.\d+)*)\D*$", ref or "")
    number = match.group(1) if match else ""
    if kind == "figure":
        return f"Fig. {number}" if number else "Fig."
    if kind == "table":
        return f"Table {number}" if number else "Table"
    if kind == "equation":
        return f"Eq. ({number})" if number else "Eq."
    if kind == "section":
        return f"Sec. {number}" if number else "Sec."
    if kind == "algorithm":
        return f"Algorithm {number}" if number else "Algorithm"
    if kind == "theorem":
        return f"Theorem {number}" if number else "Theorem"
    return f"Ref. {number}" if number else "Ref."


def _xref_label_map(content: DocumentContent) -> dict[str, str]:
    labels: dict[str, str] = {}
    counters = {"figure": 0, "table": 0, "equation": 0}

    def add(alias: str | None, display: str) -> None:
        if alias:
            labels[alias] = display
            labels[f"#{alias}"] = display

    for section, block in content.iter_blocks():
        if block.type == "heading":
            section_number = (block.number or section.heading.number or "").strip()
            if section_number:
                add(block.label, f"Sec. {section_number}")
            continue
        if block.type not in counters:
            continue
        counters[block.type] += 1
        number = (block.number or str(counters[block.type])).strip()
        display = _fallback_ref_display(number, block.type)
        add(block.label, display)
        add(block.id, display)
    return labels


def _inline_to_wire(inline: Any) -> dict[str, Any]:
    if hasattr(inline, "model_dump"):
        return inline.model_dump(mode="json", exclude_none=True)
    if isinstance(inline, dict):
        return {k: v for k, v in inline.items() if v is not None}
    return {}


def _decorate_inlines(
    inlines: list[Any], citation_labels: dict[str, str], ref_labels: dict[str, str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for inline in inlines:
        data = _inline_to_wire(inline)
        ref = str(data.get("ref") or "")
        if data.get("t") == "citation" and not data.get("v"):
            data["v"] = citation_labels.get(ref) or _display_from_cite_key(ref)
        elif data.get("t") == "ref" and not data.get("v"):
            data["v"] = ref_labels.get(ref) or ref_labels.get(f"#{ref}") or _fallback_ref_display(
                ref, data.get("kind") if isinstance(data.get("kind"), str) else None
            )
        children = data.get("children")
        if isinstance(children, list):
            data["children"] = _decorate_inlines(children, citation_labels, ref_labels)
        out.append(data)
    return out


def _block_wire(
    block: Block,
    citation_labels: dict[str, str] | None = None,
    ref_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = block.model_dump(mode="json", exclude_none=True)
    citation_labels = citation_labels or {}
    ref_labels = ref_labels or {}
    if data.get("inlines"):
        data["inlines"] = _decorate_inlines(list(block.inlines), citation_labels, ref_labels)
    if data.get("caption"):
        data["caption"] = _decorate_inlines(list(block.caption), citation_labels, ref_labels)
    if data.get("items"):
        data["items"] = [
            _decorate_inlines(list(item), citation_labels, ref_labels) for item in block.items
        ]
    if block.type in ("figure", "table", "equation") and block.asset_key:
        data["asset_url"] = asset_url(block.asset_key)
    return data


def _section_wire(
    section: Section,
    citation_labels: dict[str, str] | None = None,
    ref_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = section.model_dump(mode="json", exclude_none=True, exclude={"blocks", "sections"})
    data["blocks"] = [_block_wire(block, citation_labels, ref_labels) for block in section.blocks]
    data["sections"] = [_section_wire(sub, citation_labels, ref_labels) for sub in section.sections]
    return data


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# --- PaperBib / LibraryItemSummary の構築 ------------------------------------------


def _build_paper_bib(paper: Paper) -> PaperBib:
    version = paper.latest_version
    return PaperBib(
        id=str(paper.id),
        title=paper.title,
        authors=author_names(list(paper.authors or [])),
        authors_short=authors_short(list(paper.authors or [])),
        venue=paper.venue,
        year=paper.published_on.year if paper.published_on else None,
        arxiv_id=paper.arxiv_id,
        arxiv_version=version,
        doi=paper.doi,
        license=paper.license,
        visibility=paper.visibility,
        abstract=paper.abstract,
    )


def _reading_progress_pct(content: DocumentContent, block_id: str | None) -> int:
    """読書位置由来の進捗(§1.7 LibraryItemSummary.progress_pct)。

    文書順のブロック列における読書位置の到達割合(floor)。位置なしは 0。
    """
    if not block_id:
        return 0
    order = [blk.id for _sec, blk in content.iter_blocks()]
    total = len(order)
    if total == 0 or block_id not in order:
        return 0
    return min(100, (100 * (order.index(block_id) + 1)) // total)


def _build_last_position(item: LibraryItem, content: DocumentContent) -> LastPosition | None:
    pos = item.reading_position
    if not isinstance(pos, dict) or not pos.get("block_id"):
        return None
    block_id = str(pos["block_id"])
    smap = _block_section_map(content)
    section = smap.get(block_id)
    return LastPosition(
        revision_id=str(pos.get("revision_id", "")),
        block_id=block_id,
        mode=pos.get("view_mode", pos.get("mode", "translation")),
        section_display=_section_display(section) if section is not None else "",
        saved_at=_iso(item.updated_at) or "",
    )


def _build_library_item_summary(
    item: LibraryItem, paper: Paper, revision: DocumentRevision, content: DocumentContent
) -> LibraryItemSummary:
    pos_block = None
    if isinstance(item.reading_position, dict):
        pos_block = item.reading_position.get("block_id")
    return LibraryItemSummary(
        id=str(item.id),
        paper=_build_paper_bib(paper),
        status=item.status,
        priority=item.priority,
        deadline=item.deadline.isoformat() if item.deadline else None,
        tags=list(item.tags or []),
        suggested_tags=list(item.suggested_tags or []),
        quality_level=revision.quality_level,
        source="arxiv" if paper.arxiv_id else "upload",
        progress_pct=_reading_progress_pct(content, pos_block),
        comprehension=item.understanding,
        importance=item.importance,
        reading_seconds_total=item.total_active_seconds,
        one_line_note=item.one_line_note or None,
        summary_3line=list(paper.summary_lines) if paper.summary_lines else None,
        thumbnail_url=asset_url(item.thumbnail_key or paper.thumbnail_key),
        pipeline=None,  # 完了済み表示。取り込み中の PipelineState は §21 の jobs から解決する
        last_position=_build_last_position(item, content),
        added_at=_iso(item.added_at) or "",
        updated_at=_iso(item.updated_at) or "",
        finished_at=_iso(item.finished_at),
    )


# --- 翻訳進捗・ToC ------------------------------------------------------------------


async def _resolve_style(db: AsyncSession, user: User) -> str:
    settings = user.settings or {}
    translation = settings.get("translation", {}) if isinstance(settings, dict) else {}
    style = translation.get("default_style", "natural")
    return str(style) if style in ("natural", "literal") else "natural"


async def _effective_set(
    db: AsyncSession, revision_id: str, style: str, user_id: str
) -> TranslationSet | None:
    """(revision, style) の表示対象セット。personal フォークがあれば優先、無ければ shared。"""
    rows = (
        (
            await db.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == revision_id,
                    TranslationSet.style == style,
                    or_(
                        TranslationSet.scope == "shared",
                        TranslationSet.user_id == user_id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    personal = next((s for s in rows if s.scope == "personal"), None)
    if personal is not None:
        return personal
    return next((s for s in rows if s.scope == "shared"), None)


async def _displayable_block_ids(
    db: AsyncSession, revision_id: str, style: str, user_id: str, in_scope: set[str]
) -> set[str]:
    units = await resolve_display_units(db, revision_id, style, user_id)
    return {
        bid
        for bid, unit in units.items()
        if bid in in_scope and not (set(unit.quality_flags or []) & BLOCKING_FLAGS)
    }


async def _annotation_maps(
    db: AsyncSession, library_item_id: str, content: DocumentContent
) -> tuple[dict[str, int], set[str]]:
    """セクション ID -> 注釈数 / ブックマークを持つセクション ID 集合。"""
    smap = _block_section_map(content)
    rows = (
        await db.execute(
            select(Annotation.kind, Annotation.anchor).where(
                Annotation.library_item_id == library_item_id,
                Annotation.orphaned.is_(False),
            )
        )
    ).all()
    counts: dict[str, int] = {}
    bookmarked: set[str] = set()
    for kind, anchor in rows:
        block_id = anchor.get("block_id") if isinstance(anchor, dict) else None
        section = smap.get(str(block_id)) if block_id else None
        if section is None:
            continue
        counts[section.id] = counts.get(section.id, 0) + 1
        if kind == "bookmark":
            bookmarked.add(section.id)
    return counts, bookmarked


def _build_toc(
    content: DocumentContent,
    scope: Any,
    displayable: set[str],
    ann_counts: dict[str, int],
    bookmarked: set[str],
) -> list[TocNode]:
    section_scope = {s["section_id"]: s["block_ids"] for s in scope.sections}
    appendix = set(scope.appendix_section_ids)

    def build(section: Section) -> TocNode:
        scope_blocks = section_scope.get(section.id, [])
        translated = bool(scope_blocks) and all(b in displayable for b in scope_blocks)
        number = (section.heading.number or "").strip() or None
        return TocNode(
            section_id=section.id,
            number=number,
            title_ja=None,  # セクション見出しは翻訳ユニットを持たない(見出しは構造メタ)
            title_en=section.heading.title,
            translated=translated,
            in_progress_denominator=bool(scope_blocks),
            on_demand=section.id in appendix,
            annotation_count=ann_counts.get(section.id, 0),
            bookmarked=section.id in bookmarked,
            children=[build(sub) for sub in section.sections],
        )

    return [build(top) for top in content.sections]


async def _today_reading_minutes(db: AsyncSession, user_id: str) -> int:
    """当日(UTC)の ReadingSession active_seconds 合計を分に(§6.1 today_reading_minutes)。"""
    today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    total = await db.scalar(
        select(func.coalesce(func.sum(ReadingSession.active_seconds), 0))
        .join(LibraryItem, LibraryItem.id == ReadingSession.library_item_id)
        .where(LibraryItem.user_id == user_id, ReadingSession.started_at >= today)
    )
    return int(total or 0) // 60


# --- §6.1 ビューア初期化 ------------------------------------------------------------


@router.get(
    "/api/library-items/{item_id}/viewer",
    response_model=ViewerInit,
    operation_id="viewer_init",
)
async def get_viewer(item_id: str, user: CurrentUser, db: DbDep) -> ViewerInit:
    item = await resolve_owned_library_item(db, item_id, user)
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")
    revision_id = paper.latest_revision_id
    revision = await db.get(DocumentRevision, revision_id) if revision_id else None
    if revision is None:
        raise ProblemException("not_found")
    content = _as_content(revision)

    scope = compute_translation_scope(content)
    in_scope = set(scope.in_scope_block_ids)
    style = await _resolve_style(db, user)
    tset = await _effective_set(db, str(revision.id), style, str(user.id))

    translation: ViewerTranslation | None = None
    displayable: set[str] = set()
    if tset is not None:
        displayable = await _displayable_block_ids(
            db, str(revision.id), style, str(user.id), in_scope
        )
        translation = ViewerTranslation(
            style=style,
            set_id=str(tset.id),
            status=tset.status,
            # §13.1: 分子 = スコープ内の表示可能ユニット数、分母 = スコープ対象ブロック数。
            progress_pct=compute_progress(
                [{"quality_flags": []}] * len(displayable), len(in_scope)
            ),
        )

    ann_counts, bookmarked = await _annotation_maps(db, str(item.id), content)
    toc = _build_toc(content, scope, displayable, ann_counts, bookmarked)

    stats = revision.stats or {}
    figure_count = sum(1 for _s, b in content.iter_blocks() if b.type == "figure")
    table_count = sum(1 for _s, b in content.iter_blocks() if b.type == "table")

    # リソースの件数バッジは status='active' のみ数える(docs/12 §5・plans/02)。
    active_resources = await db.scalar(
        select(func.count())
        .select_from(ResourceLink)
        .where(ResourceLink.library_item_id == item.id, ResourceLink.status == "active")
    )
    counts = ViewerCounts(
        annotations=await _count(db, Annotation, Annotation.library_item_id, item.id),
        resources=int(active_resources or 0),
        figures=figure_count + table_count,
        notes=await _count(db, Note, Note.library_item_id, item.id),
    )

    ingest_job = await db.scalar(
        select(Job)
        .where(Job.kind == "ingest", Job.paper_id == paper.id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    timeline = joblog.build_timeline(ingest_job.log if ingest_job is not None else [])

    return ViewerInit(
        library_item=_build_library_item_summary(item, paper, revision, content),
        revision=RevisionInfo(
            id=str(revision.id),
            quality_level=revision.quality_level,
            source_version=revision.source_version,
            parser_version=revision.parser_version,
            page_count=stats.get("pages") if isinstance(stats, dict) else None,
            figure_count=figure_count,
            table_count=table_count,
            created_at=_iso(revision.created_at) or "",
        ),
        newer_revision=None,
        toc=toc,
        translation=translation,
        counts=counts,
        last_position=_build_last_position(item, content),
        license_card=build_license_card(paper.license),
        ingest_timeline=[TimelineEntry(at=str(e["at"]), label=str(e["label"])) for e in timeline],
        today_reading_minutes=await _today_reading_minutes(db, str(user.id)),
    )


async def _count(db: AsyncSession, model: Any, column: Any, value: Any) -> int:
    result = await db.scalar(select(func.count()).select_from(model).where(column == value))
    return int(result or 0)


# --- §6.2 リビジョン一覧 ------------------------------------------------------------


@router.get(
    "/api/papers/{paper_id}/revisions",
    response_model=RevisionListResponse,
    operation_id="viewer_list_revisions",
)
async def list_revisions(paper_id: str, user: CurrentUser, db: DbDep) -> RevisionListResponse:
    paper = await db.get(Paper, paper_id)
    if paper is None or not await _paper_accessible(db, paper, user):
        raise ProblemException("not_found")
    rows = (
        (
            await db.execute(
                select(DocumentRevision)
                .where(DocumentRevision.paper_id == paper_id)
                .order_by(DocumentRevision.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    current = str(paper.latest_revision_id) if paper.latest_revision_id else None
    return RevisionListResponse(
        items=[
            RevisionListItem(
                id=str(r.id),
                quality_level=r.quality_level,
                source_version=r.source_version,
                parser_version=r.parser_version,
                created_at=_iso(r.created_at) or "",
                is_current=str(r.id) == current,
            )
            for r in rows
        ]
    )


# --- §6.3 構造化ドキュメント(ETag / 304) -----------------------------------------


@router.get("/api/revisions/{revision_id}/document", operation_id="viewer_get_document")
async def get_document(
    revision_id: str,
    user: CurrentUser,
    db: DbDep,
    request: Request,
    section_id: str | None = Query(default=None),
) -> Response:
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    etag = f'"{revision_id}:{section_id}"' if section_id else f'"{revision_id}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304, headers={"ETag": etag, "Cache-Control": "private, max-age=0"}
        )

    content = _as_content(revision)
    reference_records = _reference_records(content)
    citation_labels = _citation_label_map(reference_records)
    ref_labels = _xref_label_map(content)
    if section_id is not None:
        section = _find_section(content, section_id)
        if section is None:
            raise ProblemException("not_found")
        sections: list[dict[str, Any]] = [_section_wire(section, citation_labels, ref_labels)]
    else:
        sections = [_section_wire(s, citation_labels, ref_labels) for s in content.sections]
    body: dict[str, Any] = {
        "revision_id": str(revision.id),
        "quality_level": revision.quality_level,
        "sections": sections,
    }
    return JSONResponse(content=body, headers={"ETag": etag, "Cache-Control": "private, max-age=0"})


# --- §6.4 単一ブロック --------------------------------------------------------------


@router.get(
    "/api/revisions/{revision_id}/blocks/{block_id}",
    response_model=BlockDetail,
    operation_id="viewer_get_block",
)
async def get_block(revision_id: str, block_id: str, user: CurrentUser, db: DbDep) -> BlockDetail:
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    content = _as_content(revision)
    section: Section | None = None
    block: Block | None = None
    for sec, blk in content.iter_blocks():
        if blk.id == block_id:
            block, section = blk, sec
            break
    if block is None or section is None:
        raise ProblemException("not_found")

    # display: "§2.2 ¶3"(段落序数はセクション内 paragraph の 1 始まり)。
    para_index = 0
    display_para = ""
    for blk in section.blocks:
        if blk.type == "paragraph":
            para_index += 1
        if blk.id == block_id:
            display_para = f" ¶{para_index}" if blk.type == "paragraph" else ""
            break
    display = f"{_section_display(section)}{display_para}".strip()
    reference_records = _reference_records(content)
    citation_labels = _citation_label_map(reference_records)
    ref_labels = _xref_label_map(content)

    style = await _resolve_style(db, user)
    units = await resolve_display_units(db, str(revision.id), style, str(user.id))
    unit = units.get(block_id)
    translation: BlockTranslation | None = None
    if unit is not None and not (set(unit.quality_flags or []) & BLOCKING_FLAGS):
        translation = BlockTranslation(text_ja=unit.text_ja, state=unit.state)

    return BlockDetail(
        block=_block_wire(block, citation_labels, ref_labels),
        section_id=section.id,
        display=display,
        translation=translation,
    )


# --- §6.5 図表タブ ------------------------------------------------------------------


@router.get(
    "/api/revisions/{revision_id}/figures",
    response_model=FiguresResponse,
    operation_id="viewer_list_figures",
)
async def list_figures(revision_id: str, user: CurrentUser, db: DbDep) -> FiguresResponse:
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    content = _as_content(revision)
    style = await _resolve_style(db, user)
    units = await resolve_display_units(db, str(revision.id), style, str(user.id))

    items: list[FigureItem] = []
    for section, blk in content.iter_blocks():
        if blk.type not in ("figure", "table"):
            continue
        unit = units.get(blk.id)
        caption_ja: str | None = None
        if (
            unit is not None
            and unit.text_ja
            and not (set(unit.quality_flags or []) & BLOCKING_FLAGS)
        ):
            caption_ja = unit.text_ja
        number = (blk.number or "").strip()
        display = (
            (f"図{number}" if blk.type == "figure" else f"表{number}")
            if number
            else ("図" if blk.type == "figure" else "表")
        )
        items.append(
            FigureItem(
                block_id=blk.id,
                kind=blk.type,
                label=blk.label,
                display=display,
                caption_en=inline_to_plain(blk.caption),
                caption_ja=caption_ja,
                image_url=asset_url(blk.asset_key),
                position=FigurePosition(section_display=_section_display(section), page=blk.page),
            )
        )
    return FiguresResponse(items=items)


# --- §6.6 参考文献 ------------------------------------------------------------------


def _extract_arxiv_id(url: str | None) -> str | None:
    if not url or "arxiv.org" not in url:
        return None
    tail = url.rstrip("/").split("/")[-1]
    return tail.split("v")[0] if tail else None


@router.get(
    "/api/revisions/{revision_id}/references",
    response_model=ReferencesResponse,
    operation_id="viewer_list_references",
)
async def list_references(revision_id: str, user: CurrentUser, db: DbDep) -> ReferencesResponse:
    revision, _paper = await resolve_accessible_revision(db, revision_id, user)
    content = _as_content(revision)

    entries = _reference_records(content)
    # arxiv_id -> 自ライブラリ項目(芋づる取り込み済み判定)。
    arxiv_ids: list[str] = []
    parsed: list[dict[str, Any]] = []
    for entry in entries:
        structured = entry.get("structured") if isinstance(entry.get("structured"), dict) else {}
        url = structured.get("url")
        arxiv_raw = structured.get("arxiv_id")
        if arxiv_raw:
            arxiv_id = str(arxiv_raw).split("v")[0]
        else:
            arxiv_id = _extract_arxiv_id(url if isinstance(url, str) else None)
        if arxiv_id:
            arxiv_ids.append(arxiv_id)
        parsed.append(
            {
                "block_id": entry.get("block_id"),
                "label": entry.get("label"),
                "structured": structured,
                "url": url,
                "arxiv_id": arxiv_id,
                "raw": entry.get("raw"),
            }
        )

    in_library_map: dict[str, str] = {}
    if arxiv_ids:
        rows = (
            await db.execute(
                select(Paper.arxiv_id, LibraryItem.id)
                .join(LibraryItem, LibraryItem.paper_id == Paper.id)
                .where(LibraryItem.user_id == user.id, Paper.arxiv_id.in_(arxiv_ids))
            )
        ).all()
        for aid, li_id in rows:
            in_library_map[str(aid)] = str(li_id)

    items: list[ReferenceItem] = []
    for idx, p in enumerate(parsed, start=1):
        structured = p["structured"]
        authors_list = structured.get("authors") or []
        if isinstance(authors_list, str):
            authors = authors_list
        else:
            authors = ", ".join(str(a) for a in authors_list) if authors_list else None
        year = structured.get("year")
        venue = structured.get("venue")
        venue_year = None
        if venue and year:
            venue_year = f"{venue} {year}"
        elif year:
            venue_year = str(year)
        elif venue:
            venue_year = str(venue)
        arxiv_id = p["arxiv_id"]
        in_library = (
            ReferenceInLibrary(library_item_id=in_library_map[arxiv_id])
            if arxiv_id and arxiv_id in in_library_map
            else None
        )
        fallback_id = f"ref-{idx}"
        label = p["label"] if isinstance(p["label"], str) and p["label"] else None
        ref_id = label or fallback_id
        aliases = [fallback_id, f"bib-{idx}", f"bib.{idx}"]
        if label:
            aliases.extend([label, f"#{label}"])
        if isinstance(p["block_id"], str):
            aliases.append(p["block_id"])
        items.append(
            ReferenceItem(
                ref_id=ref_id,
                aliases=sorted(set(aliases)),
                number=f"[{idx}]",
                raw=p["raw"] if isinstance(p["raw"], str) and p["raw"] else None,
                authors=authors,
                title=structured.get("title"),
                venue_year=venue_year,
                arxiv_id=arxiv_id,
                doi=structured.get("doi"),
                url=p["url"] if isinstance(p["url"], str) else None,
                in_library=in_library,
            )
        )
    return ReferencesResponse(items=items)
