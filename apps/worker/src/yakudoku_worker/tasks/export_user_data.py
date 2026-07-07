"""``jobs.kind='export'`` ハンドラ(全量 JSON 一括エクスポート。plans/03 §18・plans/13 M2-15)。

対象ユーザーのライブラリ(論文書誌+LibraryItem)・注釈・メモ・チャット・語彙(SRS 含む)・
リソース・記事(ArticleBlock 含む)・コレクション・設定を 1 つの JSON にまとめ zip 化して
S3(assets バケット)へアップロードし、署名付き URL(24 時間。plans/03 §18)を
``jobs.result.download_url`` に格納する(docs/00 P5「ロックインしない」)。

呼び出し元は ``POST /api/export/full``(``apps/api/src/yakudoku_api/routers/export.py``)。
API はジョブ作成までを担い、実処理は本モジュール(worker)が担う。

**HANDLERS 登録(followups)**: ``apps/worker/src/yakudoku_worker/tasks/__init__.py``
(共有ファイル・所有範囲外)に以下を追加する必要がある(``fetch_resource_meta.py`` の
followups と同方針)::

    from yakudoku_worker.tasks.export_user_data import run_export_full_job
    HANDLERS["export"] = run_export_full_job
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    Collection,
    CollectionEntry,
    Job,
    LibraryItem,
    Note,
    Paper,
    ResourceLink,
    User,
    VocabEntry,
)
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.storage.s3 import S3Storage, StorageKeys

_EXPORT_URL_TTL_SECONDS = 24 * 60 * 60  # 有効 24 時間(plans/03 §18)
_ZIP_ENTRY_NAME = "yakudoku-export.json"


def _iso(value: dt.date | dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _author_names(authors: list[Any] | None) -> list[str]:
    return [str(a.get("name", a)) if isinstance(a, dict) else str(a) for a in (authors or [])]


# ---------------------------------------------------------------------------
# カテゴリ別シリアライズ(全キー存在。docs/00 P5・PY-EXP-04)
# ---------------------------------------------------------------------------
async def _serialize_library(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(LibraryItem, Paper)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(LibraryItem.user_id == user_id)
            .order_by(LibraryItem.added_at.asc())
        )
    ).all()
    return [
        {
            "library_item_id": str(item.id),
            "paper_id": str(paper.id),
            "title": paper.title,
            "authors": _author_names(paper.authors),
            "venue": paper.venue,
            "year": paper.published_on.year if paper.published_on else None,
            "arxiv_id": paper.arxiv_id,
            "doi": paper.doi,
            "status": item.status,
            "priority": item.priority,
            "deadline": _iso(item.deadline),
            "tags": list(item.tags or []),
            "one_line_note": item.one_line_note,
            "understanding": item.understanding,
            "importance": item.importance,
            "total_active_seconds": item.total_active_seconds,
            "added_at": _iso(item.added_at),
            "finished_at": _iso(item.finished_at),
        }
        for item, paper in rows
    ]


async def _serialize_notes(
    session: AsyncSession, library_item_ids: list[str]
) -> list[dict[str, Any]]:
    if not library_item_ids:
        return []
    rows = (
        (
            await session.execute(
                select(Note)
                .where(Note.library_item_id.in_(library_item_ids))
                .order_by(Note.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(n.id),
            "library_item_id": str(n.library_item_id),
            "title": n.title,
            "body_md": n.body_md,
            "created_at": _iso(n.created_at),
            "updated_at": _iso(n.updated_at),
        }
        for n in rows
    ]


async def _serialize_annotations(
    session: AsyncSession, library_item_ids: list[str]
) -> list[dict[str, Any]]:
    if not library_item_ids:
        return []
    rows = (
        (
            await session.execute(
                select(Annotation)
                .where(Annotation.library_item_id.in_(library_item_ids))
                .order_by(Annotation.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(a.id),
            "library_item_id": str(a.library_item_id),
            "kind": a.kind,
            "color": a.color,
            "body": a.body,
            "anchor": a.anchor,
            "orphaned": a.orphaned,
            "created_at": _iso(a.created_at),
        }
        for a in rows
    ]


async def _serialize_chat_threads(
    session: AsyncSession, library_item_ids: list[str]
) -> list[dict[str, Any]]:
    if not library_item_ids:
        return []
    threads = (
        (
            await session.execute(
                select(ChatThread)
                .where(ChatThread.library_item_id.in_(library_item_ids))
                .order_by(ChatThread.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for thread in threads:
        messages = (
            (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread.id)
                    .order_by(ChatMessage.id.asc())
                )
            )
            .scalars()
            .all()
        )
        out.append(
            {
                "thread_id": str(thread.id),
                "library_item_id": str(thread.library_item_id),
                "title": thread.title,
                "is_main": thread.is_main,
                "messages": [
                    {
                        "role": m.role,
                        "text": m.text_plain,
                        "status": m.status,
                        "created_at": _iso(m.created_at),
                    }
                    for m in messages
                ],
            }
        )
    return out


async def _serialize_vocab(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(VocabEntry)
                .where(VocabEntry.user_id == user_id)
                .order_by(VocabEntry.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(v.id),
            "library_item_id": str(v.library_item_id),
            "kind": v.kind,
            "term": v.term,
            "pos_label": v.pos_label,
            "ipa": v.ipa,
            "context_sentence": v.context_sentence,
            "meaning_short": v.meaning_short,
            "meaning_long": v.meaning_long,
            "interpretation": v.interpretation,
            "etymology": v.etymology,
            "mnemonic": v.mnemonic,
            "related_forms": v.related_forms,
            # SRS 状態(docs/00 P5「語彙(SRS 含む)」)。
            "srs": {
                "stage": v.srs_stage,
                "next_review_on": _iso(v.srs_next_review_on),
                "review_count": v.srs_review_count,
                "mastered": v.srs_mastered,
                "history": v.srs_history,
            },
            "created_at": _iso(v.created_at),
        }
        for v in rows
    ]


async def _serialize_resources(
    session: AsyncSession, library_item_ids: list[str]
) -> list[dict[str, Any]]:
    if not library_item_ids:
        return []
    rows = (
        (
            await session.execute(
                select(ResourceLink)
                .where(ResourceLink.library_item_id.in_(library_item_ids))
                .order_by(ResourceLink.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "library_item_id": str(r.library_item_id),
            "status": r.status,
            "kind": r.kind,
            "url": r.url,
            "title": r.title,
            "official": r.official,
            "note_md": r.note_md,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def _serialize_articles(
    session: AsyncSession, library_item_ids: list[str]
) -> list[dict[str, Any]]:
    if not library_item_ids:
        return []
    articles = (
        (
            await session.execute(
                select(Article).where(Article.library_item_id.in_(library_item_ids))
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for article in articles:
        blocks = (
            (
                await session.execute(
                    select(ArticleBlock)
                    .where(ArticleBlock.article_id == article.id)
                    .order_by(ArticleBlock.position.asc())
                )
            )
            .scalars()
            .all()
        )
        out.append(
            {
                "article_id": str(article.id),
                "library_item_id": str(article.library_item_id),
                "title": article.title,
                "preset": article.preset,
                "version": article.version,
                "generated_at": _iso(article.generated_at),
                "blocks": [
                    {
                        "id": str(b.id),
                        "type": b.type,
                        "content": b.content,
                        "text_plain": b.text_plain,
                        "origin": b.origin,
                    }
                    for b in blocks
                ],
            }
        )
    return out


async def _serialize_collections(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    collections = (
        (
            await session.execute(
                select(Collection)
                .where(Collection.user_id == user_id)
                .order_by(Collection.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for collection in collections:
        entries = (
            (
                await session.execute(
                    select(CollectionEntry)
                    .where(CollectionEntry.collection_id == collection.id)
                    .order_by(CollectionEntry.position.asc())
                )
            )
            .scalars()
            .all()
        )
        out.append(
            {
                "id": str(collection.id),
                "name": collection.name,
                "description": collection.description,
                "deadline": _iso(collection.deadline),
                "library_item_ids": [str(e.library_item_id) for e in entries],
                "created_at": _iso(collection.created_at),
            }
        )
    return out


async def build_export_payload(session: AsyncSession, user_id: str) -> dict[str, Any]:
    """全量 JSON の本体(docs/00 P5 の全カテゴリの全キー存在。PY-EXP-04)。"""
    user = await session.get(User, user_id)
    library = await _serialize_library(session, user_id)
    library_item_ids = [str(row["library_item_id"]) for row in library]
    return {
        "exported_at": dt.datetime.now(dt.UTC).isoformat(),
        "user": {
            "id": user_id,
            "email": user.email if user is not None else "",
            "display_name": user.display_name if user is not None else "",
        },
        "library": library,
        "notes": await _serialize_notes(session, library_item_ids),
        "annotations": await _serialize_annotations(session, library_item_ids),
        "chat_threads": await _serialize_chat_threads(session, library_item_ids),
        "vocab": await _serialize_vocab(session, user_id),
        "resources": await _serialize_resources(session, library_item_ids),
        "articles": await _serialize_articles(session, library_item_ids),
        "collections": await _serialize_collections(session, user_id),
        "settings": user.settings if user is not None and isinstance(user.settings, dict) else {},
    }


def _zip_payload(payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_ZIP_ENTRY_NAME, data)
    return buf.getvalue()


async def run_export_full_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='export'`` ハンドラ。全量 JSON を zip 化して S3 へ保存し署名 URL を返す。"""
    session = store.session
    user_id = str(job.user_id)
    payload = await build_export_payload(session, user_id)
    archive = _zip_payload(payload)

    storage: S3Storage = ctx.get("s3") or S3Storage(ctx.get("settings"))
    key = StorageKeys.export(user_id, str(job.id))
    await storage.put(storage.assets_bucket, key, archive, content_type="application/zip")
    url = await storage.presign_get(storage.assets_bucket, key, expires_in=_EXPORT_URL_TTL_SECONDS)

    await store.succeed(str(job.id), {"download_url": url})
