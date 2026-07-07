"""export ルータ(M1-16/M2-15。plans/03 §18・docs/00 P5・docs/06 §10・§11)。

- ``GET /api/library-items/{id}/export/markdown``: 論文単位 Markdown(Obsidian 互換
  front-matter + 書誌 + メモ + 注釈 + チャット履歴 + リソース一覧)。
- ``GET /api/library-items/{id}/export/annotations``: 注釈のみの Markdown(1b「⤓ Markdown
  エクスポート」)。注釈一覧は ``routers.annotations.list_annotations`` を直接呼び、一覧パネルの
  フィルタ結果と表示内容(§ チップのテキスト化・引用・コメント)を完全に一致させる(PY-ANN-03)。
- ``GET /api/export/bibtex``: ライブラリ単位 BibTeX。クエリは §5.1 と同一のフィルタ群の主要
  な部分集合(status/tag/year。無指定=全件)。
- ``GET /api/export/csv``: ライブラリ単位 CSV(UTF-8 BOM・16 列固定。§18 逐語。PY-EXP-03)。
- ``POST /api/export/full`` / ``GET /api/export/full/{job_id}``: 全量 JSON 一括(``jobs.kind=
  'export'``。実処理は :mod:`yakudoku_worker.tasks.export_user_data` — M2-15 新規。HANDLERS
  registration is a followup, see module docstring there。PY-EXP-04)。
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from yakudoku_core.db.models import (
    ChatMessage,
    ChatThread,
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    ResourceLink,
)
from yakudoku_core.db.models import Note as NoteModel
from yakudoku_core.jobs.store import JobStore

from yakudoku_api.deps import CurrentUser, DbDep, SettingsDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.routers.annotations import list_annotations
from yakudoku_api.schemas.common import PaperBib
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
from yakudoku_api.schemas.jobs import JobOut, job_to_out
from yakudoku_api.schemas.library import build_paper_bib

router = APIRouter(tags=["export"])
log = structlog.get_logger("yakudoku.api.export")

_MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_BIBTEX_MEDIA_TYPE = "application/x-bibtex; charset=utf-8"
_CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
# plans/01 §4.3(apps/worker/settings.BULK_QUEUE と同値。apps 間 import 禁止のため定数で持つ。
# routers/ingest.py・articles.py 等の既存 wakeup ヘルパと同方針)。
_BULK_QUEUE = "yk:bulk"


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


# ============================================================================
# ライブラリ単位 CSV(§18・M2-15。UTF-8 BOM・16 列固定。無指定=全件。PY-EXP-03)
# ============================================================================
# 列定義(plans/03 §18 逐語): title,authors,year,venue,arxiv_id,doi,status,priority,
# deadline,tags,quality,added_at,finished_at,reading_hours,comprehension,importance
_CSV_HEADER = [
    "title",
    "authors",
    "year",
    "venue",
    "arxiv_id",
    "doi",
    "status",
    "priority",
    "deadline",
    "tags",
    "quality",
    "added_at",
    "finished_at",
    "reading_hours",
    "comprehension",
    "importance",
]


@dataclass(frozen=True)
class _ExportCsvRow:
    """CSV 1 行分の表示済み値(DB 非依存の値オブジェクト。``render_csv`` の単体テスト用)。"""

    paper: PaperBib
    status: str
    priority: str | None
    deadline: str
    tags: list[str]
    quality: str
    added_at: str
    finished_at: str
    reading_hours: float
    comprehension: int | None
    importance: str | None


def render_csv(rows: list[_ExportCsvRow]) -> str:
    """``GET /api/export/csv``(plans/03 §18)。UTF-8 BOM 付き・16 列ヘッダ固定(PY-EXP-03)。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADER)
    for row in rows:
        writer.writerow(
            [
                row.paper.title,
                "; ".join(row.paper.authors),
                row.paper.year if row.paper.year is not None else "",
                row.paper.venue or "",
                row.paper.arxiv_id or "",
                row.paper.doi or "",
                row.status,
                row.priority or "",
                row.deadline,
                ", ".join(row.tags),
                row.quality,
                row.added_at,
                row.finished_at,
                f"{row.reading_hours:.2f}",
                row.comprehension if row.comprehension is not None else "",
                row.importance or "",
            ]
        )
    return "\ufeff" + buf.getvalue()


def _quality_revision_id(item: LibraryItem, paper: Paper) -> str | None:
    rp = item.reading_position
    if isinstance(rp, dict) and rp.get("revision_id"):
        return str(rp["revision_id"])
    return str(paper.latest_revision_id) if paper.latest_revision_id else None


@router.get("/api/export/csv", operation_id="export_csv")
async def export_csv(user: CurrentUser, db: DbDep) -> Response:
    rows = (
        await db.execute(
            select(LibraryItem, Paper)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(LibraryItem.user_id == user.id)
            .order_by(LibraryItem.added_at.desc())
        )
    ).all()

    rev_ids = {
        rid for item, paper in rows if (rid := _quality_revision_id(item, paper)) is not None
    }
    quality_map: dict[str, str] = {}
    if rev_ids:
        qrows = (
            await db.execute(
                select(DocumentRevision.id, DocumentRevision.quality_level).where(
                    DocumentRevision.id.in_(rev_ids)
                )
            )
        ).all()
        quality_map = {str(rid): q for rid, q in qrows}

    csv_rows = [
        _ExportCsvRow(
            paper=build_paper_bib(paper),
            status=item.status,
            priority=item.priority,
            deadline=item.deadline.isoformat() if item.deadline else "",
            tags=list(item.tags or []),
            quality=quality_map.get(_quality_revision_id(item, paper) or "", ""),
            added_at=item.added_at.isoformat(),
            finished_at=item.finished_at.isoformat() if item.finished_at else "",
            reading_hours=item.total_active_seconds / 3600,
            comprehension=item.understanding,
            importance=item.importance,
        )
        for item, paper in rows
    ]
    return _attachment(render_csv(csv_rows), media_type=_CSV_MEDIA_TYPE, filename="library.csv")


# ============================================================================
# 全量 JSON 一括(§18・M2-15。``jobs.kind='export'``。実処理は worker。PY-EXP-04)
# ============================================================================
JobWakeup = Callable[[str], Awaitable[None]]


async def _default_export_wakeup(redis_url: str, job_id: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        await pool.enqueue_job("run_job", job_id, _queue_name=_BULK_QUEUE)
    finally:
        await pool.aclose()


def get_export_job_wakeup(settings: SettingsDep) -> JobWakeup:
    """arq への起床通知を返す。失敗してもジョブ作成自体は成功させる(DB が真実)。"""

    async def wakeup(job_id: str) -> None:
        try:
            await _default_export_wakeup(settings.redis_url, job_id)
        except Exception:
            await log.awarning("export_wakeup_failed", job_id=job_id)

    return wakeup


ExportJobWakeupDep = Annotated[JobWakeup, Depends(get_export_job_wakeup)]


class ExportFullStartResponse(BaseModel):
    job_id: str


class ExportFullStatusResponse(BaseModel):
    job: JobOut
    download_url: str | None


@router.post(
    "/api/export/full",
    response_model=ExportFullStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="export_full_start",
)
async def start_export_full(
    user: CurrentUser, db: DbDep, wakeup: ExportJobWakeupDep
) -> ExportFullStartResponse:
    store = JobStore(db)
    job_id = await store.enqueue(kind="export", priority="bulk", user_id=str(user.id), payload={})
    await wakeup(job_id)
    return ExportFullStartResponse(job_id=job_id)


@router.get(
    "/api/export/full/{job_id}",
    response_model=ExportFullStatusResponse,
    operation_id="export_full_status",
)
async def get_export_full(job_id: str, user: CurrentUser, db: DbDep) -> ExportFullStatusResponse:
    if not _valid_uuid(job_id):
        raise ProblemException("not_found")
    job = await db.get(Job, job_id)
    if job is None or str(job.user_id) != str(user.id):
        raise ProblemException("not_found")
    download_url = job.result.get("download_url") if isinstance(job.result, dict) else None
    return ExportFullStatusResponse(job=job_to_out(job), download_url=download_url)
