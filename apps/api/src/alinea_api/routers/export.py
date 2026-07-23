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
  'export'``。実処理は :mod:`alinea_worker.tasks.export_user_data` — M2-15 新規。HANDLERS
  registration is a followup, see module docstring there。PY-EXP-04)。
"""

from __future__ import annotations

import base64
import csv
import io
import mimetypes
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import structlog
from alinea_core.db.models import (
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    ResourceLink,
    SourceAsset,
    TranslationUnit,
)
from alinea_core.db.models import Note as NoteModel
from alinea_core.db.revisions import (
    get_latest_paper_revision,
    get_paper_revisions,
    reading_position_revision_id,
)
from alinea_core.document.blocks import DocumentContent
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_core.translation import (
    BLOCKING_FLAGS,
    find_effective_set,
    resolve_translation_set_units,
)
from fastapi import APIRouter, Depends, Header, Query, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from alinea_api.deps import CurrentUser, DbDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.routers.annotations import list_annotations
from alinea_api.routers.papers import StorageDep
from alinea_api.schemas.common import PaperBib
from alinea_api.schemas.export import (
    ExportAnnotation,
    ExportChatMessage,
    ExportChatThread,
    ExportNote,
    ExportResource,
    PaperExportRequest,
    export_filename,
    render_annotations_markdown,
    render_bibtex,
    render_paper_markdown,
)
from alinea_api.schemas.jobs import JobOut, job_to_out
from alinea_api.schemas.library import build_paper_bib
from alinea_api.schemas.standalone import StandaloneAvailability
from alinea_api.schemas.standalone_html import (
    ArticleBlockView,
    StandaloneMeta,
    TranslationView,
    render_article_html,
    render_document_html,
)

router = APIRouter(tags=["export"])
log = structlog.get_logger("alinea.api.export")

_MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_BIBTEX_MEDIA_TYPE = "application/x-bibtex; charset=utf-8"
_CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
# plans/01 §4.3(apps/worker/settings.BULK_QUEUE と同値。apps 間 import 禁止のため定数で持つ。
# routers/ingest.py・articles.py 等の既存 wakeup ヘルパと同方針)。
_BULK_QUEUE = "alinea:bulk"
_MAX_IMPORT_ARCHIVE_BYTES = 100 * 1024 * 1024
_IMPORT_READ_CHUNK_BYTES = 1024 * 1024


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

    requested_pairs: list[tuple[object, object]] = []
    for item, paper in rows:
        reading_revision_id = reading_position_revision_id(item.reading_position)
        if reading_revision_id is not None:
            requested_pairs.append((paper.id, reading_revision_id))
        if paper.latest_revision_id is not None:
            requested_pairs.append((paper.id, paper.latest_revision_id))
    revisions = await get_paper_revisions(db, requested_pairs)

    def quality_of(item: LibraryItem, paper: Paper) -> str:
        reading_revision_id = reading_position_revision_id(item.reading_position)
        reading_revision = (
            revisions.get((str(paper.id), reading_revision_id))
            if reading_revision_id is not None
            else None
        )
        latest_revision = (
            revisions.get((str(paper.id), str(paper.latest_revision_id)))
            if paper.latest_revision_id is not None
            else None
        )
        revision = reading_revision or latest_revision
        return revision.quality_level if revision is not None else ""

    csv_rows = [
        _ExportCsvRow(
            paper=build_paper_bib(paper),
            status=item.status,
            priority=item.priority,
            deadline=item.deadline.isoformat() if item.deadline else "",
            tags=list(item.tags or []),
            quality=quality_of(item, paper),
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


# ============================================================================
# インポート API(完全データ移行 Task 5)
# POST /api/import/full   — multipart zip → S3 一時 key → import Job 作成
# GET  /api/import/full/{job_id} — import Job の進捗確認
# ============================================================================


class ImportFullStartResponse(BaseModel):
    job_id: str


class ImportFullStatusResponse(BaseModel):
    job: JobOut
    summary: dict[str, Any] | None


def get_import_job_wakeup(settings: SettingsDep) -> JobWakeup:
    """import Job の arq 起床通知(export と同一 bulk キューを使う)。"""

    async def wakeup(job_id: str) -> None:
        try:
            await _default_export_wakeup(settings.redis_url, job_id)
        except Exception:
            await log.awarning("import_wakeup_failed", job_id=job_id)

    return wakeup


ImportJobWakeupDep = Annotated[JobWakeup, Depends(get_import_job_wakeup)]


async def _read_limited_import_upload(file: UploadFile) -> bytes:
    """インポートアーカイブを上限付きで読み込む。"""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_IMPORT_READ_CHUNK_BYTES):
        total += len(chunk)
        if total > _MAX_IMPORT_ARCHIVE_BYTES:
            raise ProblemException("payload_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/api/import/full",
    response_model=ImportFullStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="import_full_start",
)
async def start_import_full(
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    wakeup: ImportJobWakeupDep,
    file: UploadFile,
) -> ImportFullStartResponse:
    """multipart zip を受け取り S3 一時 key に保存して import Job を作成する。"""
    data = await _read_limited_import_upload(file)
    upload_id = str(uuid.uuid4())
    upload_key = StorageKeys.import_upload(str(user.id), upload_id)

    storage = S3Storage(settings)
    await storage.put(
        storage.assets_bucket, upload_key, data, content_type="application/zip"
    )

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="import",
        priority="bulk",
        user_id=str(user.id),
        payload={"upload_key": upload_key},
    )
    await wakeup(job_id)
    return ImportFullStartResponse(job_id=job_id)


@router.get(
    "/api/import/full/{job_id}",
    response_model=ImportFullStatusResponse,
    operation_id="import_full_status",
)
async def get_import_full(
    job_id: str, user: CurrentUser, db: DbDep
) -> ImportFullStatusResponse:
    if not _valid_uuid(job_id):
        raise ProblemException("not_found")
    job = await db.get(Job, job_id)
    if job is None or str(job.user_id) != str(user.id):
        raise ProblemException("not_found")
    summary = job.result.get("summary") if isinstance(job.result, dict) else None
    return ImportFullStatusResponse(job=job_to_out(job), summary=summary)


# ============================================================================
# 論文単位スタンドアロンエクスポート(Feature S3)
# ============================================================================
# spec: docs/superpowers/specs/2026-07-16-standalone-paper-export-design.md
# 原本 PDF とみなす source_assets.kind(papers.py の _PDF_KINDS と同値。apps 間 import の
# 循環を避けるため定数で持つ)。
_STANDALONE_PDF_KINDS = ("pdf", "arxiv_pdf", "pdf_upload", "extension_capture")
_HTML_MEDIA_TYPE = "text/html; charset=utf-8"


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.UTC).isoformat()


async def _latest_revision(db: DbDep, item: LibraryItem) -> DocumentRevision | None:
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        return None
    return await get_latest_paper_revision(db, paper)


def _document_from_revision(revision: DocumentRevision) -> DocumentContent | None:
    try:
        content = DocumentContent.model_validate(revision.content)
    except Exception:  # 壊れた content は「原文なし」として扱う(P3)
        return None
    return content if content.iter_blocks() else None


async def _has_original_pdf(db: DbDep, revision: DocumentRevision) -> bool:
    row = (
        await db.execute(
            select(SourceAsset.id)
            .where(
                SourceAsset.paper_id == revision.paper_id,
                SourceAsset.kind.in_(_STANDALONE_PDF_KINDS),
                SourceAsset.source_version == revision.source_version,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _translated_pdf_key(
    db: DbDep, revision: DocumentRevision, user_id: str
) -> str | None:
    """有効 natural セット由来の訳文 PDF の正規キー(無ければ None)。papers.py と同一規則。"""
    tset = await find_effective_set(db, str(revision.id), "natural", user_id)
    if tset is None:
        return None
    return StorageKeys.translated_pdf(
        str(revision.paper_id),
        revision.source_version,
        "natural",
        translation_set_id=(str(tset.id) if tset.scope == "personal" else None),
    )


async def _has_translated_pdf(db: DbDep, revision: DocumentRevision, user_id: str) -> bool:
    key = await _translated_pdf_key(db, revision, user_id)
    if key is None:
        return False
    row = (
        await db.execute(
            select(SourceAsset.id)
            .where(
                SourceAsset.paper_id == revision.paper_id,
                SourceAsset.kind == "translated_pdf",
                SourceAsset.storage_key == key,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _has_article(db: DbDep, item_id: str) -> bool:
    row = (
        await db.execute(
            select(Article.id).where(Article.library_item_id == item_id).limit(1)
        )
    ).first()
    return row is not None


async def _translation_complete(db: DbDep, revision: DocumentRevision, user_id: str) -> bool:
    tset = await find_effective_set(db, str(revision.id), "natural", user_id)
    return tset is not None and tset.status == "complete"


async def _compute_availability(
    db: DbDep, item: LibraryItem, user_id: str
) -> StandaloneAvailability:
    """成果物ごとの生成有無を最新リビジョン基準で計算する(所有権チェックは呼び出し側)。"""
    revision = await _latest_revision(db, item)
    if revision is None:
        return StandaloneAvailability(
            source_html=False,
            translation_html=False,
            bilingual_html=False,
            article_html=await _has_article(db, str(item.id)),
            pdf_original=False,
            pdf_translated=False,
            pdf_bilingual=False,
        )

    source_ready = _document_from_revision(revision) is not None
    translation_ready = await _translation_complete(db, revision, user_id)
    pdf_original = await _has_original_pdf(db, revision)
    pdf_translated = await _has_translated_pdf(db, revision, user_id)
    return StandaloneAvailability(
        source_html=source_ready,
        translation_html=source_ready and translation_ready,
        bilingual_html=source_ready and translation_ready,
        article_html=await _has_article(db, str(item.id)),
        pdf_original=pdf_original,
        pdf_translated=pdf_translated,
        # 決定 D(暫定): 対訳 PDF は原文 PDF + 訳文 PDF の結合で作れる時のみ可。
        pdf_bilingual=pdf_original and pdf_translated,
    )


@router.get(
    "/api/library-items/{item_id}/export/standalone/availability",
    response_model=StandaloneAvailability,
    operation_id="export_standaloneAvailability",
)
async def standalone_availability(
    item_id: str, user: CurrentUser, db: DbDep
) -> StandaloneAvailability:
    """成果物ごとの生成有無(UI の選択可否判定。最新リビジョン基準でビューアと一致)。"""
    item = await _get_owned_item(db, user.id, item_id)
    return await _compute_availability(db, item, str(user.id))


def _unit_displayable(unit: TranslationUnit) -> bool:
    text_ja = unit.text_ja or ""
    content_ja = unit.content_ja
    typed_table = isinstance(content_ja, dict) and content_ja.get("kind") == "table"
    flags = set(unit.quality_flags or [])
    return bool(text_ja or typed_table) and not (flags & BLOCKING_FLAGS)


async def _translation_views(
    db: DbDep, revision: DocumentRevision, user_id: str
) -> dict[str, TranslationView]:
    tset = await find_effective_set(db, str(revision.id), "natural", user_id)
    if tset is None:
        return {}
    units = await resolve_translation_set_units(db, tset)
    return {
        block_id: TranslationView(
            content_ja=unit.content_ja,
            text_ja=unit.text_ja or "",
            displayable=_unit_displayable(unit),
        )
        for block_id, unit in units.items()
    }


async def _image_data_uris(storage: StorageDep, asset_keys: set[str]) -> dict[str, str]:
    """図の S3 バイトを data URI 化(best-effort。欠損はプレースホルダに委ねる)。"""
    out: dict[str, str] = {}
    for key in sorted(asset_keys):
        try:
            data = await storage.get(storage.assets_bucket, key)
        except Exception:  # noqa: S112 — 欠損アセットは skip(P3。レンダラが代替表示)
            continue
        mime = mimetypes.guess_type(key)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        out[key] = f"data:{mime};base64,{encoded}"
    return out


def _document_asset_keys(content: DocumentContent) -> set[str]:
    return {
        block.asset_key
        for _section, block in content.iter_blocks()
        if block.type in ("figure", "table", "equation") and block.asset_key
    }


def _standalone_meta(paper_bib: PaperBib, quality: str, mode_label: str) -> StandaloneMeta:
    return StandaloneMeta(
        title=paper_bib.title,
        authors=list(paper_bib.authors),
        arxiv_id=paper_bib.arxiv_id,
        generated_at=_now_iso(),
        mode_label=mode_label,
        quality_level=quality,
    )


async def _resolved_document(
    db: DbDep, user: CurrentUser, item_id: str
) -> tuple[LibraryItem, DocumentRevision, DocumentContent, PaperBib]:
    item = await _get_owned_item(db, user.id, item_id)
    revision = await _latest_revision(db, item)
    if revision is None:
        raise ProblemException("not_found")
    content = _document_from_revision(revision)
    if content is None:
        raise ProblemException("not_found")
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")
    return item, revision, content, build_paper_bib(paper)


def _html_filename(paper: PaperBib, suffix: str) -> str:
    """HTML 版のファイル名(export_filename の .md を .html に差し替え)。"""
    return export_filename(paper, suffix=suffix).removesuffix(".md") + ".html"


@router.get(
    "/api/library-items/{item_id}/export/standalone/source.html",
    operation_id="export_standaloneSourceHtml",
)
async def standalone_source_html(
    item_id: str, user: CurrentUser, db: DbDep, storage: StorageDep
) -> Response:
    _item, revision, content, paper_bib = await _resolved_document(db, user, item_id)
    image_data_uris = await _image_data_uris(storage, _document_asset_keys(content))
    html_doc = render_document_html(
        content,
        mode="source",
        units={},
        image_data_uris=image_data_uris,
        meta=_standalone_meta(paper_bib, revision.quality_level, "原文"),
    )
    return _attachment(
        html_doc, media_type=_HTML_MEDIA_TYPE, filename=_html_filename(paper_bib, "-source")
    )


@router.get(
    "/api/library-items/{item_id}/export/standalone/translation.html",
    operation_id="export_standaloneTranslationHtml",
)
async def standalone_translation_html(
    item_id: str, user: CurrentUser, db: DbDep, storage: StorageDep
) -> Response:
    _item, revision, content, paper_bib = await _resolved_document(db, user, item_id)
    if not await _translation_complete(db, revision, str(user.id)):
        raise ProblemException("not_found")
    units = await _translation_views(db, revision, str(user.id))
    image_data_uris = await _image_data_uris(storage, _document_asset_keys(content))
    html_doc = render_document_html(
        content,
        mode="translation",
        units=units,
        image_data_uris=image_data_uris,
        meta=_standalone_meta(paper_bib, revision.quality_level, "訳文"),
    )
    return _attachment(
        html_doc, media_type=_HTML_MEDIA_TYPE, filename=_html_filename(paper_bib, "-translation")
    )


@router.get(
    "/api/library-items/{item_id}/export/standalone/bilingual.html",
    operation_id="export_standaloneBilingualHtml",
)
async def standalone_bilingual_html(
    item_id: str, user: CurrentUser, db: DbDep, storage: StorageDep
) -> Response:
    _item, revision, content, paper_bib = await _resolved_document(db, user, item_id)
    if not await _translation_complete(db, revision, str(user.id)):
        raise ProblemException("not_found")
    units = await _translation_views(db, revision, str(user.id))
    image_data_uris = await _image_data_uris(storage, _document_asset_keys(content))
    html_doc = render_document_html(
        content,
        mode="bilingual",
        units=units,
        image_data_uris=image_data_uris,
        meta=_standalone_meta(paper_bib, revision.quality_level, "対訳"),
    )
    return _attachment(
        html_doc, media_type=_HTML_MEDIA_TYPE, filename=_html_filename(paper_bib, "-bilingual")
    )


@router.get(
    "/api/library-items/{item_id}/export/standalone/article.html",
    operation_id="export_standaloneArticleHtml",
)
async def standalone_article_html(
    item_id: str, user: CurrentUser, db: DbDep, storage: StorageDep
) -> Response:
    item = await _get_owned_item(db, user.id, item_id)
    article = (
        await db.execute(
            select(Article)
            .where(Article.library_item_id == item_id)
            .order_by(Article.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if article is None:
        raise ProblemException("not_found")
    blocks = (
        (
            await db.execute(
                select(ArticleBlock)
                .where(ArticleBlock.article_id == article.id)
                .order_by(ArticleBlock.position.asc())
            )
        )
        .scalars()
        .all()
    )
    views = [ArticleBlockView(type=b.type, content=dict(b.content or {})) for b in blocks]
    asset_keys = {
        str(b.content["asset_key"])
        for b in blocks
        if isinstance(b.content, dict) and b.content.get("asset_key")
    }
    image_data_uris = await _image_data_uris(storage, asset_keys)

    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")
    paper_bib = build_paper_bib(paper)
    meta = StandaloneMeta(
        title=article.title or paper_bib.title,
        authors=list(paper_bib.authors),
        arxiv_id=paper_bib.arxiv_id,
        generated_at=_now_iso(),
        mode_label="記事",
        quality_level="A",
    )
    html_doc = render_article_html(views, image_data_uris=image_data_uris, meta=meta)
    return _attachment(
        html_doc, media_type=_HTML_MEDIA_TYPE, filename=_html_filename(paper_bib, "-article")
    )


# ============================================================================
# 論文単位スタンドアロンエクスポート(非同期 API・Task 12)
# POST /api/library-items/{id}/export/standalone       — 複数選択 → paper_export job
# GET  /api/library-items/{id}/export/standalone/{job_id} — job 進捗 + download_url
# ============================================================================
# 単一 HTML の選択は既存の同期エンドポイントへ誘導する(S3・job を挟まない)。artifact →
# 同期 HTML エンドポイントのパス suffix 対応(item_id を埋めて相対 URL を返す)。
_SYNC_HTML_ENDPOINT: dict[str, str] = {
    "source_html": "source.html",
    "translation_html": "translation.html",
    "bilingual_html": "bilingual.html",
    "article_html": "article.html",
}


class PaperExportStartResponse(BaseModel):
    """``POST .../export/standalone`` のレスポンス。

    - ``mode="sync"``: 単一 HTML 選択。``download_url`` は同期 HTML エンドポイントの相対 URL。
    - ``mode="job"``: paper_export job を enqueue。``job_id`` を status でポーリングする。
    """

    mode: str  # "sync" | "job"
    job_id: str | None = None
    download_url: str | None = None


class PaperExportStatusResponse(BaseModel):
    job: JobOut
    download_url: str | None


@router.post(
    "/api/library-items/{item_id}/export/standalone",
    response_model=PaperExportStartResponse,
    operation_id="export_standaloneStart",
)
async def start_standalone_export(
    item_id: str,
    body: PaperExportRequest,
    user: CurrentUser,
    db: DbDep,
    wakeup: ExportJobWakeupDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PaperExportStartResponse:
    """選択成果物をエクスポートする。単一 HTML は同期 URL、それ以外は paper_export job。

    所有権・artifact 値域(Literal で検証済み)・availability を enqueue 前に再検証し、
    未生成成果物を含む場合は生成を始めずに 409 で弾く(worker ハンドラの契約を写す)。
    """
    item = await _get_owned_item(db, user.id, item_id)

    # 重複排除しつつ選択順は保つ。
    artifacts: list[str] = list(dict.fromkeys(body.artifacts))
    if not artifacts:
        raise ProblemException("bad_request", detail="少なくとも 1 つの成果物を選択してください")

    # availability を再検証(未生成成果物は生成を始める前に弾く)。artifact 値は
    # StandaloneAvailability の属性名と 1:1 対応する。
    availability = await _compute_availability(db, item, str(user.id))
    unavailable = [a for a in artifacts if not getattr(availability, a)]
    if unavailable:
        raise ProblemException(
            "conflict",
            detail=f"未生成の成果物が含まれています: {', '.join(sorted(unavailable))}",
        )

    # 単一 HTML の選択は同期 HTML エンドポイントへ誘導する(job を作らない)。
    if len(artifacts) == 1 and artifacts[0] in _SYNC_HTML_ENDPOINT:
        suffix = _SYNC_HTML_ENDPOINT[artifacts[0]]
        return PaperExportStartResponse(
            mode="sync",
            job_id=None,
            download_url=f"/api/library-items/{item_id}/export/standalone/{suffix}",
        )

    # 複数選択 / PDF は paper_export job を enqueue する(bulk キュー。export.full と同型)。
    store = JobStore(db)
    job_id = await store.enqueue(
        kind="paper_export",
        priority="bulk",
        user_id=str(user.id),
        paper_id=str(item.paper_id),
        library_item_id=str(item.id),
        payload={"artifacts": artifacts},
        idempotency_key=idempotency_key,
    )
    await wakeup(job_id)
    response.status_code = status.HTTP_202_ACCEPTED
    return PaperExportStartResponse(mode="job", job_id=job_id, download_url=None)


@router.get(
    "/api/library-items/{item_id}/export/standalone/{job_id}",
    response_model=PaperExportStatusResponse,
    operation_id="export_standaloneStatus",
)
async def get_standalone_export(
    item_id: str, job_id: str, user: CurrentUser, db: DbDep
) -> PaperExportStatusResponse:
    """paper_export job の進捗と(完了後は)署名付き download_url を返す。"""
    # 所有権は job.user_id で担保する(item は導線の一貫性のため経路に残す)。
    if not _valid_uuid(job_id):
        raise ProblemException("not_found")
    job = await db.get(Job, job_id)
    if job is None or str(job.user_id) != str(user.id) or job.kind != "paper_export":
        raise ProblemException("not_found")
    download_url = job.result.get("download_url") if isinstance(job.result, dict) else None
    return PaperExportStatusResponse(job=job_to_out(job), download_url=download_url)
