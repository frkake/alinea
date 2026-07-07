"""export ルータ(M1-16。plans/03 §18 の M1 分・docs/00 P5・docs/06 §10・§11)。

- ``GET /api/library-items/{id}/export/markdown``: 論文単位 Markdown(Obsidian 互換
  front-matter + 書誌 + メモ + 注釈 + チャット履歴 + リソース一覧)。
- ``GET /api/library-items/{id}/export/annotations``: 注釈のみの Markdown(1b「⤓ Markdown
  エクスポート」)。注釈一覧は ``routers.annotations.list_annotations`` を直接呼び、一覧パネルの
  フィルタ結果と表示内容(§ チップのテキスト化・引用・コメント)を完全に一致させる(PY-ANN-03)。
- ``GET /api/export/bibtex``: ライブラリ単位 BibTeX。クエリは §5.1 と同一のフィルタ群の主要
  な部分集合(status/tag/year。無指定=全件)。CSV・全量 JSON エクスポートは M2-15。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import select
from yakudoku_core.db.models import ChatMessage, ChatThread, LibraryItem, Paper, ResourceLink
from yakudoku_core.db.models import Note as NoteModel

from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.routers.annotations import list_annotations
from yakudoku_api.schemas.export import (
    ExportAnnotation,
    ExportChatMessage,
    ExportChatThread,
    ExportNote,
    ExportResource,
    export_filename,
    render_annotations_markdown,
    render_bibtex,
    render_paper_markdown,
)
from yakudoku_api.schemas.library import build_paper_bib

router = APIRouter(tags=["export"])

_MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_BIBTEX_MEDIA_TYPE = "application/x-bibtex; charset=utf-8"


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _get_owned_item(db: DbDep, user_id: str, item_id: str) -> LibraryItem:
    if not _valid_uuid(item_id):
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return item


def _attachment(content: str, *, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================================
# 素材の取得(DB → schemas/export.py の値オブジェクト)
# ============================================================================
async def _export_notes(db: DbDep, item_id: str) -> list[ExportNote]:
    rows = (
        (
            await db.execute(
                select(NoteModel)
                .where(NoteModel.library_item_id == item_id)
                .order_by(NoteModel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [ExportNote(title=n.title, body_md=n.body_md) for n in rows]


async def _export_annotations(item_id: str, user: CurrentUser, db: DbDep) -> list[ExportAnnotation]:
    # 一覧パネルと同一の関数を直接呼び、表示内容(§ チップ・引用・コメント)を一致させる(PY-ANN-03)。
    listing = await list_annotations(item_id, user, db)
    return [
        ExportAnnotation(
            kind=a.kind,
            color=a.color,
            comment=a.comment,
            quote=a.anchor.quote,
            display=a.anchor.display,
            placed=a.placed,
        )
        for a in listing.items
    ]


async def _export_chat_threads(db: DbDep, item_id: str) -> list[ExportChatThread]:
    threads = (
        (
            await db.execute(
                select(ChatThread)
                .where(ChatThread.library_item_id == item_id)
                .order_by(ChatThread.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    out: list[ExportChatThread] = []
    for thread in threads:
        rows = (
            (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread.id, ChatMessage.status != "error")
                    .order_by(ChatMessage.id.asc())
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            continue
        out.append(
            ExportChatThread(
                title=thread.title,
                messages=[ExportChatMessage(role=m.role, text=m.text_plain) for m in rows],
            )
        )
    return out


async def _export_resources(db: DbDep, item_id: str) -> list[ExportResource]:
    rows = (
        (
            await db.execute(
                select(ResourceLink)
                .where(ResourceLink.library_item_id == item_id, ResourceLink.status == "active")
                .order_by(ResourceLink.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [ExportResource(kind=r.kind, title=r.title, url=r.url, note_md=r.note_md) for r in rows]


# ============================================================================
# 論文単位 Markdown(§18)
# ============================================================================
@router.get(
    "/api/library-items/{item_id}/export/markdown",
    operation_id="export_paperMarkdown",
)
async def export_paper_markdown(item_id: str, user: CurrentUser, db: DbDep) -> Response:
    item = await _get_owned_item(db, user.id, item_id)
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")
    paper_bib = build_paper_bib(paper)

    content = render_paper_markdown(
        paper=paper_bib,
        status=item.status,
        priority=item.priority,
        tags=list(item.tags or []),
        added_at=item.added_at.isoformat(),
        finished_at=item.finished_at.isoformat() if item.finished_at else None,
        one_line_note=item.one_line_note or "",
        notes=await _export_notes(db, item_id),
        annotations=await _export_annotations(item_id, user, db),
        chat_threads=await _export_chat_threads(db, item_id),
        resources=await _export_resources(db, item_id),
    )
    filename = export_filename(paper_bib)
    return _attachment(content, media_type=_MARKDOWN_MEDIA_TYPE, filename=filename)


# ============================================================================
# 注釈のみの Markdown(1b「⤓ Markdown エクスポート」)
# ============================================================================
@router.get(
    "/api/library-items/{item_id}/export/annotations",
    operation_id="export_annotationsMarkdown",
)
async def export_annotations_markdown(item_id: str, user: CurrentUser, db: DbDep) -> Response:
    item = await _get_owned_item(db, user.id, item_id)
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")
    paper_bib = build_paper_bib(paper)

    content = render_annotations_markdown(
        paper_title=paper_bib.title,
        annotations=await _export_annotations(item_id, user, db),
    )
    return _attachment(
        content,
        media_type=_MARKDOWN_MEDIA_TYPE,
        filename=export_filename(paper_bib, suffix="-annotations"),
    )


# ============================================================================
# ライブラリ単位 BibTeX(§18。クエリは §5.1 の部分集合。無指定=全件)
# ============================================================================
@router.get("/api/export/bibtex", operation_id="export_bibtex")
async def export_bibtex(
    user: CurrentUser,
    db: DbDep,
    status: Annotated[list[str] | None, Query()] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    year: Annotated[list[int] | None, Query()] = None,
) -> Response:
    q = (
        select(Paper)
        .join(LibraryItem, LibraryItem.paper_id == Paper.id)
        .where(LibraryItem.user_id == user.id)
    )
    if status:
        q = q.where(LibraryItem.status.in_(status))
    if tag:
        q = q.where(LibraryItem.tags.overlap(tag))
    papers = list((await db.execute(q.order_by(Paper.title.asc()))).scalars().all())
    if year:
        years = set(year)
        papers = [p for p in papers if p.published_on and p.published_on.year in years]

    content = render_bibtex([build_paper_bib(p) for p in papers])
    return _attachment(content, media_type=_BIBTEX_MEDIA_TYPE, filename="library.bib")
