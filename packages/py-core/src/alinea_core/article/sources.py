"""記事生成の素材収集(plans/07 §4.2、stage=collecting_sources)。

``collect_article_sources`` は「訳文・メモ・チャット履歴」+疑問ハイライトを 1 つの
:class:`ArticleSources` にまとめる。プロンプト(:mod:`alinea_core.article.prompts`)は
この結果だけを見て組み立てる(DB を直接叩かない)。
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field

import tiktoken
from selectolax.parser import HTMLParser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_core.db.models import (
    Annotation,
    ChatMessage,
    ChatThread,
    DocumentRevision,
    LibraryItem,
    Note,
    Paper,
    ResourceLink,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import DocumentContent, Section
from alinea_core.document.plaintext import block_to_plain, inline_to_plain
from alinea_core.licenses import LicensePolicy, classify_license
from alinea_core.search.rebuild import BlockIndexRow, compute_index_rows
from alinea_core.translation.pipeline import BLOCKING_FLAGS, resolve_display_units

# 各素材のトークン予算(plans/07 §4.2)。
BODY_BUDGET = 50_000
NOTES_BUDGET = 6_000
ANNOTATIONS_BUDGET = 4_000
CHAT_BUDGET = 10_000
RESOURCES_BUDGET = 8_000

_COLOR_LABEL: dict[str, str] = {
    "important": "重要",
    "question": "疑問",
    "idea": "アイデア",
    "term": "用語",
}


@functools.lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("o200k_base")


def estimate_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _truncate_tail_to_budget(text: str, budget: int) -> str:
    """予算を超える場合は末尾(後方セクション相当)を切り詰める(§2.2.3 圧縮モード未実装の代替)。"""
    enc = _encoder()
    ids = enc.encode(text)
    if len(ids) <= budget:
        return text
    return enc.decode(ids[:budget]) + "\n…(以降は文字数上限のため省略しました)"


def _truncate_head_to_budget(lines: list[str], budget: int, *, joiner: str = "\n") -> str:
    """新しい順(先頭)から予算に収まる分だけ採用する(§4.2 各素材の「新しい順」上限)。"""
    selected: list[str] = []
    used = 0
    for line in lines:
        cost = estimate_tokens(line)
        if selected and used + cost > budget:
            break
        selected.append(line)
        used += cost
    return joiner.join(selected)


@dataclass(frozen=True)
class FigureInfo:
    block_id: str
    kind: str  # "figure" | "table"
    display: str  # "図2" / "表1"
    caption_en: str
    caption_ja: str | None
    asset_key: str | None
    policy: str  # LicensePolicy.figure_embed の値
    table_rows: list[list[str]] | None = None


@dataclass(frozen=True)
class AnnotationRef:
    """素材一覧の短縮参照(``ann_01`` 等)。discussion.annotation_id の検証に使う。"""

    ref: str
    annotation_id: str
    color: str | None
    is_question: bool


@dataclass
class ArticleSources:
    library_item: LibraryItem
    paper: Paper
    revision: DocumentRevision
    content: DocumentContent
    style: str
    license_policy: LicensePolicy
    bibliography_text: str
    summary_text: str
    body_text: str
    figures: list[FigureInfo]
    figures_text: str
    notes_text: str
    annotations_text: str
    resources_text: str = ""
    annotation_refs: list[AnnotationRef] = field(default_factory=list)
    chat_text: str = ""
    block_ids: set[str] = field(default_factory=set)
    section_ids: set[str] = field(default_factory=set)
    block_source_text: dict[str, str] = field(default_factory=dict)

    def question_refs(self) -> set[str]:
        return {r.ref for r in self.annotation_refs if r.is_question}

    def resolve_ref(self, ref: str) -> AnnotationRef | None:
        for r in self.annotation_refs:
            if r.ref == ref or r.annotation_id == ref:
                return r
        return None


def _resolve_style(user: User) -> str:
    settings = user.settings or {}
    translation = settings.get("translation", {}) if isinstance(settings, dict) else {}
    style = translation.get("default_style", "natural")
    return str(style) if style in ("natural", "literal") else "natural"


def _section_label(sec: Section) -> str:
    num = sec.heading.number
    return f"§{num}" if num else (sec.heading.title or sec.id)


def _display_position(row: BlockIndexRow | None, fallback: str) -> str:
    if row is None:
        return fallback
    if row.element_label:
        return row.element_label
    if row.paragraph_ordinal is not None:
        return f"{row.section_label} ¶{row.paragraph_ordinal}"
    return row.section_label


def _authors_short(authors: list[object]) -> str:
    names: list[str] = []
    for a in authors[:3]:
        name = str(a.get("name", "")) if isinstance(a, dict) else str(a)
        last = name.split()[-1] if name.split() else name
        if last:
            names.append(last)
    short = ", ".join(names)
    if len(authors) > 3:
        short = f"{short} ほか" if short else "ほか"
    return short


def authors_all(authors: list[object]) -> str:
    """出典ブロック用の「著者全員」表記(plans/07 §4.5 step5)。"""
    names: list[str] = []
    for a in authors:
        name = str(a.get("name", "")) if isinstance(a, dict) else str(a)
        if name:
            names.append(name)
    return ", ".join(names) if names else "著者不明"


def _bibliography_text(paper: Paper) -> str:
    year = paper.published_on.year if paper.published_on else "年不明"
    venue = paper.venue or "(未発表 / venue 不明)"
    arxiv = paper.arxiv_id or "(arXiv ID 不明)"
    return (
        f"# 書誌\n"
        f"タイトル: {paper.title}\n"
        f"著者: {authors_all(paper.authors or [])}\n"
        f"venue: {venue} ({year})\n"
        f"arXiv: {arxiv}\n"
        f"ライセンス: {paper.license}"
    )


def _summary_text(paper: Paper) -> str:
    lines = paper.summary_lines or []
    if not lines:
        return ""
    return "# ✦3行要約\n" + "\n".join(f"- {line}" for line in lines)


def _render_translated_body(
    content: DocumentContent,
    units: dict[str, TranslationUnit],
    *,
    include_math: bool,
) -> tuple[str, dict[str, str]]:
    """訳文本文(§4.2)。未翻訳ブロックは原文で補う。figure/table は本文に含めない(別素材)。"""
    rows_by_id = {r.block_id: r for r in compute_index_rows(content)}
    lines: list[str] = []
    block_source_text: dict[str, str] = {}

    def walk(sec: Section) -> None:
        header = f"## [{sec.id}|{_section_label(sec)}] {sec.heading.title or ''}".rstrip()
        lines.append(header)
        for blk in sec.blocks:
            block_source_text[blk.id] = block_to_plain(blk)
            if blk.type in ("figure", "table", "reference_entry"):
                continue
            text: str
            if blk.type == "equation":
                if not include_math:
                    continue
                latex = (blk.latex or "").strip()
                text = f"$$ {latex} $$" if latex else ""
            else:
                unit = units.get(blk.id)
                if (
                    unit is not None
                    and unit.text_ja
                    and not (set(unit.quality_flags or []) & BLOCKING_FLAGS)
                ):
                    text = str(unit.text_ja)
                else:
                    # 未翻訳セクション: translation_units に無い block は原文で補う(§4.2)。
                    text = block_to_plain(blk)
            if not text:
                continue
            row = rows_by_id.get(blk.id)
            position = _display_position(row, _section_label(sec))
            lines.append(f"[{blk.id}|{position}] {text}")
        for sub in sec.sections:
            walk(sub)

    for s in content.sections:
        walk(s)
    return "\n".join(lines), block_source_text


def _figures(
    content: DocumentContent,
    units: dict[str, TranslationUnit],
    *,
    policy: LicensePolicy,
) -> list[FigureInfo]:
    out: list[FigureInfo] = []
    for _sec, blk in content.iter_blocks():
        if blk.type not in ("figure", "table"):
            continue
        number = (blk.number or "").strip()
        display = (
            (f"図{number}" if blk.type == "figure" else f"表{number}")
            if number
            else ("図" if blk.type == "figure" else "表")
        )
        unit = units.get(blk.id)
        caption_ja: str | None = None
        if (
            unit is not None
            and unit.text_ja
            and not (set(unit.quality_flags or []) & BLOCKING_FLAGS)
        ):
            caption_ja = str(unit.text_ja)
        table_rows: list[list[str]] | None = None
        if blk.type == "table" and blk.raw:
            parsed_rows: list[list[str]] = []
            for row in HTMLParser(blk.raw).css("tr"):
                cells = [
                    re.sub(
                        r"\\[A-Za-z]+",
                        "",
                        " ".join(cell.text(separator=" ", strip=True).split()),
                    ).strip()
                    for cell in row.css("th, td")
                ]
                if cells:
                    parsed_rows.append(cells)
            table_rows = parsed_rows or None
        out.append(
            FigureInfo(
                block_id=blk.id,
                kind=blk.type,
                display=display,
                caption_en=inline_to_plain(blk.caption),
                caption_ja=caption_ja,
                asset_key=blk.asset_key,
                table_rows=table_rows,
                policy=policy.figure_embed,
            )
        )
    return out


def _figures_text(figures: list[FigureInfo]) -> str:
    if not figures:
        return ""
    lines = ["# 図表リスト"]
    for fig in figures:
        reuse = "不可" if fig.policy == "link_card" else "可"
        caption = fig.caption_ja or fig.caption_en
        lines.append(f"[{fig.block_id}|{fig.display}] {caption} (転載: {reuse})")
    return "\n".join(lines)


async def _notes_text(session: AsyncSession, library_item_id: str) -> str:
    rows = (
        (
            await session.execute(
                select(Note)
                .where(Note.library_item_id == library_item_id)
                .order_by(Note.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return ""
    lines = [f"- ({note.id}) {note.title or '(無題)'}: {note.body_md[:1000]}" for note in rows]
    body = _truncate_head_to_budget(lines, NOTES_BUDGET)
    return "# メモ\n" + body if body else ""


async def _annotations(
    session: AsyncSession,
    library_item_id: str,
    rows_by_id: dict[str, BlockIndexRow],
) -> tuple[str, list[AnnotationRef]]:
    rows = (
        (
            await session.execute(
                select(Annotation)
                .where(
                    Annotation.library_item_id == library_item_id,
                    Annotation.kind.in_(("highlight", "comment")),
                )
                .order_by(Annotation.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return "", []

    refs: list[AnnotationRef] = []
    lines: list[str] = []
    for i, ann in enumerate(rows, start=1):
        ref = f"ann_{i:02d}"
        color = ann.color
        is_question = color == "question"
        refs.append(
            AnnotationRef(ref=ref, annotation_id=str(ann.id), color=color, is_question=is_question)
        )
        block_id = str((ann.anchor or {}).get("block_id", ""))
        row = rows_by_id.get(block_id)
        position = _display_position(row, block_id or "?")
        quote = (ann.quote or "").strip()
        label = _COLOR_LABEL.get(color or "", color or "")
        line = f'- ({ref})[{label}] [{block_id}|{position}] "{quote}"'
        if ann.body:
            line += f" (コメント: {ann.body})"
        if is_question:
            line += " ★疑問"
        lines.append(line)

    body = _truncate_head_to_budget(lines, ANNOTATIONS_BUDGET)
    text = "# 注釈\n" + body if body else ""
    return text, refs


async def _chat_text(session: AsyncSession, library_item_id: str) -> str:
    threads = (
        (
            await session.execute(
                select(ChatThread)
                .where(ChatThread.library_item_id == library_item_id)
                .order_by(ChatThread.is_main.desc(), ChatThread.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not threads:
        return ""
    all_lines: list[str] = []
    for thread in threads:
        messages = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread.id, ChatMessage.status != "error")
                    .order_by(ChatMessage.id.asc())
                )
            )
            .scalars()
            .all()
        )
        for msg in messages:
            speaker = "あなた" if msg.role == "user" else "アシスタント"
            all_lines.append(f"[スレッド: {thread.title}] {speaker}: {msg.text_plain}")
    if not all_lines:
        return ""
    # 新しい順(末尾から)に予算まで採用し、時系列順に戻す(§2.2.6 と同型)。
    reversed_selected: list[str] = []
    used = 0
    for line in reversed(all_lines):
        cost = estimate_tokens(line)
        if reversed_selected and used + cost > CHAT_BUDGET:
            break
        reversed_selected.append(line)
        used += cost
    reversed_selected.reverse()
    return "# チャット履歴\n" + "\n".join(reversed_selected)


async def _resources_text(session: AsyncSession, library_item_id: str) -> str:
    rows = (
        (
            await session.execute(
                select(ResourceLink)
                .where(
                    ResourceLink.library_item_id == library_item_id,
                    ResourceLink.status == "active",
                )
                .order_by(ResourceLink.official.desc(), ResourceLink.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    lines: list[str] = []
    for row in rows:
        meta = row.meta if isinstance(row.meta, dict) else {}
        details = ", ".join(
            f"{key}={value}" for key, value in meta.items() if value not in (None, "", [])
        )
        parts = [
            f"[{row.kind}] {row.title or row.url}",
            f"URL: {row.url}",
            f"公式: {'yes' if row.official else 'no'}",
        ]
        if details:
            parts.append(f"メタデータ: {details}")
        if row.note_md:
            parts.append(f"ユーザーメモ: {row.note_md}")
        lines.append(" | ".join(parts))
    if not lines:
        return ""
    return "# 追加リソース\n" + _truncate_head_to_budget(lines, RESOURCES_BUDGET)


async def collect_article_sources(
    session: AsyncSession,
    *,
    library_item: LibraryItem,
    paper: Paper,
    revision: DocumentRevision,
    user: User,
    include_math: bool,
) -> ArticleSources:
    """§4.2 の素材一式を収集する(stage=collecting_sources)。"""
    content = DocumentContent.model_validate(revision.content)
    style = _resolve_style(user)
    units = await resolve_display_units(session, str(revision.id), style, str(user.id))
    policy = classify_license(paper.license)

    body_text, block_source_text = _render_translated_body(
        content, dict(units), include_math=include_math
    )
    body_text = _truncate_tail_to_budget(body_text, BODY_BUDGET)

    rows = compute_index_rows(content)
    rows_by_id = {r.block_id: r for r in rows}
    section_ids: set[str] = set()
    for r in rows:
        section_ids.update(p for p in r.section_path.split("/") if p)

    figures = _figures(content, dict(units), policy=policy)
    figures_text = _figures_text(figures)
    notes_text = await _notes_text(session, str(library_item.id))
    annotations_text, annotation_refs = await _annotations(
        session, str(library_item.id), rows_by_id
    )
    chat_text = await _chat_text(session, str(library_item.id))
    resources_text = await _resources_text(session, str(library_item.id))

    return ArticleSources(
        library_item=library_item,
        paper=paper,
        revision=revision,
        content=content,
        style=style,
        license_policy=policy,
        bibliography_text=_bibliography_text(paper),
        summary_text=_summary_text(paper),
        body_text=body_text,
        figures=figures,
        figures_text=figures_text,
        notes_text=notes_text,
        annotations_text=annotations_text,
        resources_text=resources_text,
        annotation_refs=annotation_refs,
        chat_text=chat_text,
        block_ids={r.block_id for r in rows},
        section_ids=section_ids,
        block_source_text=block_source_text,
    )


__all__ = [
    "ANNOTATIONS_BUDGET",
    "BODY_BUDGET",
    "CHAT_BUDGET",
    "NOTES_BUDGET",
    "RESOURCES_BUDGET",
    "AnnotationRef",
    "ArticleSources",
    "FigureInfo",
    "authors_all",
    "collect_article_sources",
    "estimate_tokens",
]
