"""``jobs.kind='export'`` ハンドラ(全量 JSON 一括エクスポート。plans/03 §18・plans/13 M2-15)。

対象ユーザーのライブラリ(論文書誌+LibraryItem)・注釈・メモ・チャット・語彙(SRS 含む)・
リソース・記事(ArticleBlock 含む)・コレクション・設定を 1 つの JSON にまとめ zip 化して
S3(assets バケット)へアップロードし、署名付き URL(24 時間。plans/03 §18)を
``jobs.result.download_url`` に格納する(docs/00 P5「ロックインしない」)。

呼び出し元は ``POST /api/export/full``(``apps/api/src/alinea_api/routers/export.py``)。
API はジョブ作成までを担い、実処理は本モジュール(worker)が担う。

**HANDLERS 登録(followups)**: ``apps/worker/src/alinea_worker/tasks/__init__.py``
(共有ファイル・所有範囲外)に以下を追加する必要がある(``fetch_resource_meta.py`` の
followups と同方針)::

    from alinea_worker.tasks.export_user_data import run_export_full_job
    HANDLERS["export"] = run_export_full_job
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import zipfile
from typing import Any

from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    Collection,
    CollectionEntry,
    CollectionShareToken,
    DocumentRevision,
    ExplainerFigure,
    Glossary,
    GlossaryTerm,
    Job,
    LibraryItem,
    Note,
    Notification,
    OverviewFigure,
    Paper,
    ReadingSession,
    ResourceLink,
    SavedFilter,
    SourceAsset,
    TranslationSet,
    TranslationUnit,
    User,
    VocabCandidate,
    VocabEntry,
)
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_EXPORT_URL_TTL_SECONDS = 24 * 60 * 60  # 有効 24 時間(plans/03 §18)
EXPORT_SCHEMA_VERSION = 2


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
            "latest_revision_id": str(paper.latest_revision_id)
            if paper.latest_revision_id
            else None,
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
            # メモの本文アンカー(復元でメモ位置を保つ。無損失復元に必須)。
            "anchors": n.anchors,
            # 由来チャットメッセージ(int PK。復元時に old→new でリマップ or NULL)。
            "source_chat_message_id": n.source_chat_message_id,
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
            # quote は GENERATED 列(anchor->>'quote')なので出力しない(復元時に自動再生成)。
            "orphaned": a.orphaned,
            "created_at": _iso(a.created_at),
            "updated_at": _iso(a.updated_at),
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
                        # text は平文(text_plain へマップ)。content は構造化セグメント(無損失必須)。
                        "text": m.text_plain,
                        "content": m.content,
                        "context_anchors": m.context_anchors,
                        "evidence_anchors": m.evidence_anchors,
                        "provider": m.provider,
                        "model": m.model,
                        "error": m.error,
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
            # 「原文で見る」ジャンプのアンカーとハイライト範囲(無損失に必須)。
            "context_anchor": v.context_anchor,
            "context_hl_start": v.context_hl_start,
            "context_hl_end": v.context_hl_end,
            "context_sentence": v.context_sentence,
            "meaning_short": v.meaning_short,
            "meaning_long": v.meaning_long,
            "interpretation": v.interpretation,
            "etymology": v.etymology,
            "mnemonic": v.mnemonic,
            "related_forms": v.related_forms,
            # 手編集フィールド・生成状態(無損失に必須)。
            "edited_fields": list(v.edited_fields or []),
            "generation_status": v.generation_status,
            "generation_error": v.generation_error,
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


async def _serialize_vocab_candidates(
    session: AsyncSession, library_item_ids: list[str]
) -> list[dict[str, Any]]:
    if not library_item_ids:
        return []
    rows = (
        (
            await session.execute(
                select(VocabCandidate)
                .where(VocabCandidate.library_item_id.in_(library_item_ids))
                .order_by(VocabCandidate.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(row.id),
            "library_item_id": str(row.library_item_id),
            "term": row.term,
            "kind": row.kind,
            "context_anchor": row.context_anchor,
            "context_sentence": row.context_sentence,
            "context_hl_start": row.context_hl_start,
            "context_hl_end": row.context_hl_end,
            "reason": row.reason,
            "status": row.status,
            "vocab_entry_id": str(row.vocab_entry_id) if row.vocab_entry_id else None,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }
        for row in rows
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


async def _serialize_document_revisions(
    session: AsyncSession, paper_ids: list[str]
) -> list[dict[str, Any]]:
    if not paper_ids:
        return []
    rows = (
        (
            await session.execute(
                select(DocumentRevision)
                .where(DocumentRevision.paper_id.in_(paper_ids))
                .order_by(DocumentRevision.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "paper_id": str(r.paper_id),
            "source_version": r.source_version,
            "parser_version": r.parser_version,
            "quality_level": r.quality_level,
            "source_format": r.source_format,
            "content": r.content,
            "stats": r.stats,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def _serialize_translation_sets(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(TranslationSet)
                .where(TranslationSet.user_id == user_id)
                .order_by(TranslationSet.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "revision_id": str(r.revision_id),
            "style": r.style,
            "scope": r.scope,
            "user_id": str(r.user_id) if r.user_id else None,
            "base_set_id": str(r.base_set_id) if r.base_set_id else None,
            "glossary_snapshot": r.glossary_snapshot,
            "plan": r.plan,
            "prompt_version": r.prompt_version,
            "status": r.status,
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


async def _serialize_translation_units(
    session: AsyncSession, set_ids: list[str]
) -> list[dict[str, Any]]:
    if not set_ids:
        return []
    rows = (
        (
            await session.execute(
                select(TranslationUnit)
                .where(TranslationUnit.set_id.in_(set_ids))
                .order_by(TranslationUnit.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "set_id": str(r.set_id),
            "block_id": r.block_id,
            "source_hash": r.source_hash,
            "content_ja": r.content_ja,
            "text_ja": r.text_ja,
            "state": r.state,
            "quality_flags": list(r.quality_flags or []),
            "proposal": r.proposal,
            "model": r.model,
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


async def _serialize_glossaries(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Glossary)
                .where(Glossary.user_id == user_id)
                .order_by(Glossary.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "scope": r.scope,
            "user_id": str(r.user_id) if r.user_id else None,
            "library_item_id": str(r.library_item_id) if r.library_item_id else None,
            "name": r.name,
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


async def _serialize_glossary_terms(
    session: AsyncSession, glossary_ids: list[str]
) -> list[dict[str, Any]]:
    if not glossary_ids:
        return []
    rows = (
        (
            await session.execute(
                select(GlossaryTerm)
                .where(GlossaryTerm.glossary_id.in_(glossary_ids))
                .order_by(GlossaryTerm.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "glossary_id": str(r.glossary_id),
            "source_term": r.source_term,
            "target_term": r.target_term,
            "pos_label": r.pos_label,
            "policy": r.policy,
            "auto_extracted": r.auto_extracted,
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


async def _serialize_saved_filters(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(SavedFilter)
                .where(SavedFilter.user_id == user_id)
                .order_by(SavedFilter.position.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "user_id": str(r.user_id),
            "name": r.name,
            "conditions": r.conditions,
            "sort": r.sort,
            "position": r.position,
            "created_at": _iso(r.created_at),
            "updated_at": _iso(r.updated_at),
        }
        for r in rows
    ]


async def _serialize_reading_sessions(
    session: AsyncSession, library_item_ids: list[str]
) -> list[dict[str, Any]]:
    if not library_item_ids:
        return []
    rows = (
        (
            await session.execute(
                select(ReadingSession)
                .where(ReadingSession.library_item_id.in_(library_item_ids))
                .order_by(ReadingSession.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "library_item_id": str(r.library_item_id),
            "started_at": _iso(r.started_at),
            "ended_at": _iso(r.ended_at),
            "active_seconds": r.active_seconds,
            "view_mode": r.view_mode,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def _serialize_notifications(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "user_id": str(r.user_id),
            "kind": r.kind,
            "payload": r.payload,
            "read": r.read,
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def _serialize_overview_figures(
    session: AsyncSession, article_ids: list[str]
) -> list[dict[str, Any]]:
    if not article_ids:
        return []
    rows = (
        (
            await session.execute(
                select(OverviewFigure)
                .where(OverviewFigure.article_id.in_(article_ids))
                .order_by(OverviewFigure.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "article_id": str(r.article_id),
            "version": r.version,
            "is_current": r.is_current,
            "render_mode": r.render_mode,
            "dsl": r.dsl,
            "svg_storage_key": r.svg_storage_key,
            "image_storage_key": r.image_storage_key,
            "provider": r.provider,
            "model": r.model,
            "prompt": r.prompt,
            "instruction": r.instruction,
            "evidence_anchors": r.evidence_anchors,
            "generated_at": _iso(r.generated_at),
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def _serialize_explainer_figures(
    session: AsyncSession, article_ids: list[str]
) -> list[dict[str, Any]]:
    if not article_ids:
        return []
    rows = (
        (
            await session.execute(
                select(ExplainerFigure)
                .where(ExplainerFigure.article_id.in_(article_ids))
                .order_by(ExplainerFigure.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "article_id": str(r.article_id),
            "slot": r.slot,
            "version": r.version,
            "is_current": r.is_current,
            "provider": r.provider,
            "model": r.model,
            "prompt": r.prompt,
            "image_storage_key": r.image_storage_key,
            "caption": r.caption,
            "evidence_anchors": r.evidence_anchors,
            "generated_at": _iso(r.generated_at),
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def _serialize_source_assets(
    session: AsyncSession, paper_ids: list[str]
) -> list[dict[str, Any]]:
    if not paper_ids:
        return []
    rows = (
        (
            await session.execute(
                select(SourceAsset)
                .where(SourceAsset.paper_id.in_(paper_ids))
                .order_by(SourceAsset.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "paper_id": str(r.paper_id),
            "kind": r.kind,
            "source_url": r.source_url,
            "source_version": r.source_version,
            "storage_key": r.storage_key,
            "content_type": r.content_type,
            "byte_size": r.byte_size,
            "sha256": r.sha256,
            "fetched_at": _iso(r.fetched_at),
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]


async def _serialize_share_tokens(
    session: AsyncSession, collection_ids: list[str]
) -> list[dict[str, Any]]:
    if not collection_ids:
        return []
    rows = (
        (
            await session.execute(
                select(CollectionShareToken)
                .where(CollectionShareToken.collection_id.in_(collection_ids))
                .order_by(CollectionShareToken.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "collection_id": str(r.collection_id),
            "token": r.token,
            "status": r.status,
            "include_notes": r.include_notes,
            "created_at": _iso(r.created_at),
            "revoked_at": _iso(r.revoked_at),
        }
        for r in rows
    ]


async def build_export_payload(session: AsyncSession, user_id: str) -> dict[str, Any]:
    """全量 JSON の本体(docs/00 P5 の全カテゴリの全キー存在。PY-EXP-04)。"""
    user = await session.get(User, user_id)
    library = await _serialize_library(session, user_id)
    library_item_ids = [str(row["library_item_id"]) for row in library]
    paper_ids = [str(row["paper_id"]) for row in library]

    translation_sets = await _serialize_translation_sets(session, user_id)
    set_ids = [ts["id"] for ts in translation_sets]

    glossaries = await _serialize_glossaries(session, user_id)
    glossary_ids = [g["id"] for g in glossaries]

    articles = await _serialize_articles(session, library_item_ids)
    article_ids = [a["article_id"] for a in articles]

    collections = await _serialize_collections(session, user_id)
    collection_ids = [c["id"] for c in collections]

    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
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
        "vocab_candidates": await _serialize_vocab_candidates(session, library_item_ids),
        "resources": await _serialize_resources(session, library_item_ids),
        "articles": articles,
        "collections": collections,
        "settings": user.settings if user is not None and isinstance(user.settings, dict) else {},
        "document_revisions": await _serialize_document_revisions(session, paper_ids),
        "translation_sets": translation_sets,
        "translation_units": await _serialize_translation_units(session, set_ids),
        "glossaries": glossaries,
        "glossary_terms": await _serialize_glossary_terms(session, glossary_ids),
        "saved_filters": await _serialize_saved_filters(session, user_id),
        "reading_sessions": await _serialize_reading_sessions(session, library_item_ids),
        "notifications": await _serialize_notifications(session, user_id),
        "overview_figures": await _serialize_overview_figures(session, article_ids),
        "explainer_figures": await _serialize_explainer_figures(session, article_ids),
        "source_assets": await _serialize_source_assets(session, paper_ids),
        "share_tokens": await _serialize_share_tokens(session, collection_ids),
    }


def collect_asset_keys(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """payload から到達可能な (logical_bucket, storage_key) を集約(重複排除・決定的順序)。

    logical_bucket ∈ {"sources", "assets"}.
    """
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(bucket: str, key: str | None) -> None:
        if key and (bucket, key) not in seen:
            seen.add((bucket, key))
            keys.append((bucket, key))

    # source_assets(sources バケット)
    for a in payload.get("source_assets", []):
        add("sources", a.get("storage_key"))

    # overview figures: svg と raster 画像の両方(いずれも None の場合あり)
    for f in payload.get("overview_figures", []):
        add("assets", f.get("svg_storage_key"))
        add("assets", f.get("image_storage_key"))

    # explainer figures: raster 画像
    for f in payload.get("explainer_figures", []):
        add("assets", f.get("image_storage_key"))

    return keys


async def build_export_archive(session: AsyncSession, user_id: str, storage: S3Storage) -> bytes:
    """manifest.json + data.json + assets/<storage_key> を含む zip バイト列を返す。"""
    payload = await build_export_payload(session, user_id)
    buf = io.BytesIO()
    assets_meta: list[dict[str, Any]] = []

    bucket_map = {"sources": storage.sources_bucket, "assets": storage.assets_bucket}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.json", json.dumps(payload, ensure_ascii=False, indent=2))

        for logical_bucket, key in collect_asset_keys(payload):
            try:
                data = await storage.get(bucket_map[logical_bucket], key)
            except Exception:  # noqa: S112 — 欠落アセットは skip(P3)
                continue
            zf.writestr(f"assets/{key}", data)
            assets_meta.append(
                {
                    "storage_key": key,
                    "bucket": logical_bucket,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "byte_size": len(data),
                }
            )

        manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "exported_at": payload["exported_at"],
            "assets": assets_meta,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return buf.getvalue()


async def run_export_full_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='export'`` ハンドラ。全量 JSON を zip 化して S3 へ保存し署名 URL を返す。"""
    session = store.session
    user_id = str(job.user_id)

    storage: S3Storage = ctx.get("s3") or S3Storage(ctx.get("settings"))
    archive = await build_export_archive(session, user_id, storage)

    key = StorageKeys.export(user_id, str(job.id))
    await storage.put(storage.assets_bucket, key, archive, content_type="application/zip")
    url = await storage.presign_get(storage.assets_bucket, key, expires_in=_EXPORT_URL_TTL_SECONDS)

    await store.succeed(str(job.id), {"download_url": url})
