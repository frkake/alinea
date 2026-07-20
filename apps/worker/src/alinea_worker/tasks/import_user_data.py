"""``data.json`` の冪等マージ復元コア(完全データ移行 Task 3)。

zip の ``data.json``(:func:`export_user_data.build_export_payload` が出力)を依存順に
別ユーザーへマージ復元する純粋ロジック。再取り込み(re-ingest)不要で本文・翻訳・注釈・
チャット根拠・語彙アンカーまで無損失に戻す(docs/00「完全移行」)。

復元方針
--------
- **依存順**: papers → source_assets → document_revisions → library_items →
  translation_sets → translation_units → glossaries → glossary_terms → notes →
  annotations → chat_threads → chat_messages → vocab → resources → articles →
  article_blocks → collections → collection_entries → share_tokens →
  saved_filters → reading_sessions → notifications。
- **papers** は共有エンティティ。``arxiv_id``(なければ ``doi``)で名寄せし、既存があれば
  再利用(新規挿入しない)。無ければ新 UUID で作成し ``owner_user_id=target``。
- **library_items** は ``(user_id, paper_id)`` で存在判定して再利用 or 新規。
- **UUID-PK 子テーブル**は元 id を保持したまま挿入する。よって 2 回目以降は
  ``session.get(Model, old_id)`` が既存を検出して skip(冪等)。JSONB アンカー内の
  ``revision_id`` / ``block_id`` も元 UUID のまま有効。リマップするのは
  ``paper_id``(papers)と ``library_item_id``(items)の 2 つの外部キーのみ。
- **INT autoincrement-PK テーブル**(chat_messages/translation_units/article_blocks/
  collection_entries/reading_sessions/notifications)は PK を値で保存できないため、親が
  「今回新規作成された」ものだけ挿入する(履歴の二重化防止)。notifications だけは親
  (ユーザー)が常に事前作成されるため、自然キー ``(user_id, kind, created_at)`` で冪等化する。
- 復元後、新規挿入した各 document_revision について
  :func:`rebuild_block_search_index` を呼び block_search_index を再構築する
  (これがエクスポートに block_search_index を含めない理由)。
- 個別行の失敗は SAVEPOINT で隔離し ``summary["failed"]`` に記録して継続する
  (docs/00 P3「黙って壊れない」。1 行の不正で全体を中断しない)。
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import json
import uuid
import zipfile
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from alinea_core.db.base import Base
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
from alinea_core.document.blocks import DocumentContent
from alinea_core.jobs.store import JobStore
from alinea_core.search.rebuild import rebuild_block_search_index
from alinea_core.storage.s3 import S3Storage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

def merge_missing(target: dict[str, object], source: Mapping[str, object]) -> dict[str, object]:
    """再帰的不足キー補完マージ。

    target に既にあるキーは変更しない。target にないキーのみ source から補完する。
    両方が dict の場合は再帰的にマージする。
    """
    merged = copy.deepcopy(target)
    for key, value in source.items():
        if key not in merged:
            merged[key] = copy.deepcopy(value)
        elif isinstance(merged[key], dict) and isinstance(value, Mapping):
            merged[key] = merge_missing(merged[key], value)
    return merged


# インポート zip のスキーマバージョン(エクスポート側の EXPORT_SCHEMA_VERSION と一致する)。
IMPORT_SCHEMA_VERSION = 2
_MAX_ZIP_ENTRIES = 2_000
_MAX_ZIP_MEMBER_BYTES = 100 * 1024 * 1024
_MAX_ZIP_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_MAX_ZIP_COMPRESSION_RATIO = 100


def _validated_members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """ZIP を展開する前にパスと展開リソースを検証する。"""
    infos = zf.infolist()
    if len(infos) > _MAX_ZIP_ENTRIES:
        raise ValueError("too_many_zip_entries")
    total = 0
    members: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        parts = info.filename.split("/")
        if info.is_dir() or info.filename.startswith("/") or ".." in parts:
            raise ValueError("unsafe_zip_member")
        if info.filename in members:
            raise ValueError("duplicate_zip_member")
        if info.file_size > _MAX_ZIP_MEMBER_BYTES:
            raise ValueError("zip_member_too_large")
        if info.compress_size and info.file_size / info.compress_size > _MAX_ZIP_COMPRESSION_RATIO:
            raise ValueError("zip_compression_ratio_exceeded")
        total += info.file_size
        if total > _MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError("zip_uncompressed_total_too_large")
        members[info.filename] = info
    if "manifest.json" not in members or "data.json" not in members:
        raise ValueError("missing_required_zip_member")
    return members


def _dt(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def _date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


def _restored_asset_key(user_id: str, kind: str, entity_id: str, source_key: str) -> str:
    suffix = Path(source_key).suffix.lower()
    safe_suffix = suffix if len(suffix) <= 12 else ""
    return f"imports/restored/{user_id}/{kind}/{entity_id}{safe_suffix}"


def _prepare_asset_destinations(data: dict[str, Any], user_id: str) -> dict[str, tuple[str, str]]:
    """アーカイブ由来のキーを、移行先専用キーへ置換して許可表を返す。"""
    destinations: dict[str, tuple[str, str]] = {}

    def register(row: dict[str, Any], field: str, bucket: str, kind: str) -> None:
        old_key = row.get(field)
        if not isinstance(old_key, str) or not old_key:
            return
        existing = destinations.get(old_key)
        if existing is not None:
            if existing[0] != bucket:
                raise ValueError("conflicting_asset_reference")
            row[field] = existing[1]
            return
        new_key = _restored_asset_key(user_id, kind, str(row["id"]), old_key)
        destinations[old_key] = (bucket, new_key)
        row[field] = new_key

    for row in data.get("source_assets") or []:
        if isinstance(row, dict):
            register(row, "storage_key", "sources", "source-assets")
    for row in data.get("overview_figures") or []:
        if isinstance(row, dict):
            register(row, "svg_storage_key", "assets", "overview-svg")
            register(row, "image_storage_key", "assets", "overview-image")
    for row in data.get("explainer_figures") or []:
        if isinstance(row, dict):
            register(row, "image_storage_key", "assets", "explainer-image")
    return destinations


def _validated_manifest_assets(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("invalid_manifest_assets")
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("storage_key"), str):
            raise ValueError("invalid_manifest_asset")
        if not isinstance(asset.get("sha256"), str):
            raise ValueError("invalid_manifest_asset")
    return assets


class _Importer:
    """1 回の import 呼び出しの状態(id マップ・件数集計)を保持する。"""

    def __init__(self, session: AsyncSession, target_user_id: str) -> None:
        self.session = session
        self.uid = str(target_user_id)
        self.created: dict[str, int] = defaultdict(int)
        self.skipped: dict[str, int] = defaultdict(int)
        self.failed: list[dict[str, Any]] = []
        # 外部キー張り替え用の old→new マップ(UUID を新規採番するのは 2 種のみ)。
        self.paper_map: dict[str, str] = {}
        self.created_paper_ids: set[str] = set()
        self.item_map: dict[str, str] = {}
        # 「今回新規作成された」親の元 id 集合(INT-PK 子の挿入可否を決める)。
        self.item_created: set[str] = set()
        self.set_created: set[str] = set()
        self.thread_created: set[str] = set()
        self.article_created: set[str] = set()
        self.collection_created: set[str] = set()
        # 索引再構築対象: (new_revision_id, content_dict)
        self._pending_index: list[tuple[str, dict[str, Any]]] = []

    # -- 低レベルヘルパ ------------------------------------------------------
    async def _insert(self, table: str, obj: Base, ref: str | None) -> bool:
        """SAVEPOINT 内で 1 行を INSERT。失敗は failed に記録して継続(P3)。"""
        try:
            async with self.session.begin_nested():
                self.session.add(obj)
                await self.session.flush()
        except Exception as exc:
            self.failed.append({"table": table, "id": ref, "error": repr(exc)})
            return False
        self.created[table] += 1
        return True

    # -- 各テーブル ----------------------------------------------------------
    async def restore_papers(self, library: list[dict[str, Any]]) -> None:
        for entry in library:
            old_id = str(entry["paper_id"])
            if old_id in self.paper_map:
                continue
            arxiv_id = entry.get("arxiv_id")
            doi = entry.get("doi")
            existing: Paper | None = None
            if arxiv_id:
                existing = (
                    await self.session.execute(select(Paper).where(Paper.arxiv_id == arxiv_id))
                ).scalar_one_or_none()
            if existing is None and doi:
                existing = (
                    await self.session.execute(select(Paper).where(Paper.doi == doi))
                ).scalar_one_or_none()
            if existing is not None:
                # 共有エンティティを再利用(所有者は書き換えない)。
                self.paper_map[old_id] = str(existing.id)
                self.skipped["papers"] += 1
                continue
            year = entry.get("year")
            new_id = str(uuid.uuid4())
            paper = Paper(
                id=new_id,
                arxiv_id=arxiv_id,
                doi=doi,
                title=entry.get("title") or "(untitled)",
                authors=[{"name": n} for n in (entry.get("authors") or [])],
                venue=entry.get("venue"),
                published_on=dt.date(int(year), 1, 1) if year else None,
                owner_user_id=self.uid,
                visibility="private",
            )
            if await self._insert("papers", paper, old_id):
                self.paper_map[old_id] = new_id
                self.created_paper_ids.add(new_id)

    async def restore_source_assets(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(SourceAsset, old_id) is not None:
                self.skipped["source_assets"] += 1
                continue
            paper_id = self.paper_map.get(str(r["paper_id"]))
            if paper_id is None:
                self.failed.append(
                    {"table": "source_assets", "id": old_id, "error": "unmapped paper_id"}
                )
                continue
            await self._insert(
                "source_assets",
                SourceAsset(
                    id=old_id,
                    paper_id=paper_id,
                    kind=r["kind"],
                    source_url=r.get("source_url"),
                    source_version=r.get("source_version"),
                    storage_key=r["storage_key"],
                    content_type=r.get("content_type") or "application/octet-stream",
                    byte_size=r.get("byte_size") or 0,
                    sha256=r.get("sha256"),
                    fetched_at=_dt(r.get("fetched_at")),
                    created_at=_dt(r.get("created_at")),
                ),
                old_id,
            )

    async def restore_document_revisions(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(DocumentRevision, old_id) is not None:
                self.skipped["document_revisions"] += 1
                continue
            paper_id = self.paper_map.get(str(r["paper_id"]))
            if paper_id is None:
                self.failed.append(
                    {"table": "document_revisions", "id": old_id, "error": "unmapped paper_id"}
                )
                continue
            ok = await self._insert(
                "document_revisions",
                DocumentRevision(
                    id=old_id,
                    paper_id=paper_id,
                    source_version=r.get("source_version") or "v1",
                    parser_version=r["parser_version"],
                    quality_level=r["quality_level"],
                    source_format=r["source_format"],
                    content=r["content"],
                    stats=r.get("stats") or {},
                    created_at=_dt(r.get("created_at")),
                ),
                old_id,
            )
            if ok and isinstance(r.get("content"), dict):
                self._pending_index.append((old_id, r["content"]))

    async def restore_library(self, library: list[dict[str, Any]]) -> None:
        for entry in library:
            old_item = str(entry["library_item_id"])
            paper_id = self.paper_map.get(str(entry["paper_id"]))
            if paper_id is None:
                self.failed.append(
                    {"table": "library", "id": old_item, "error": "unmapped paper_id"}
                )
                continue
            existing = (
                await self.session.execute(
                    select(LibraryItem).where(
                        LibraryItem.user_id == self.uid, LibraryItem.paper_id == paper_id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                self.item_map[old_item] = str(existing.id)
                self.skipped["library"] += 1
                continue
            new_id = str(uuid.uuid4())
            ok = await self._insert(
                "library",
                LibraryItem(
                    id=new_id,
                    user_id=self.uid,
                    paper_id=paper_id,
                    status=entry.get("status") or "planned",
                    priority=entry.get("priority"),
                    deadline=_date(entry.get("deadline")),
                    tags=list(entry.get("tags") or []),
                    one_line_note=entry.get("one_line_note") or "",
                    understanding=entry.get("understanding"),
                    importance=entry.get("importance"),
                    total_active_seconds=entry.get("total_active_seconds") or 0,
                    added_at=_dt(entry.get("added_at")),
                    finished_at=_dt(entry.get("finished_at")),
                ),
                old_item,
            )
            if ok:
                self.item_map[old_item] = new_id
                self.item_created.add(old_item)

    async def restore_translation_sets(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(TranslationSet, old_id) is not None:
                self.skipped["translation_sets"] += 1
                continue
            personal = r.get("scope") == "personal"
            ok = await self._insert(
                "translation_sets",
                TranslationSet(
                    id=old_id,
                    revision_id=str(r["revision_id"]),
                    style=r.get("style") or "natural",
                    scope=r.get("scope") or "shared",
                    user_id=self.uid if personal else None,
                    base_set_id=str(r["base_set_id"]) if r.get("base_set_id") else None,
                    glossary_snapshot=r.get("glossary_snapshot") or [],
                    plan=r.get("plan"),
                    prompt_version=r.get("prompt_version") or "tr-2026-07-06.1",
                    status=r.get("status") or "pending",
                    created_at=_dt(r.get("created_at")),
                    updated_at=_dt(r.get("updated_at")),
                ),
                old_id,
            )
            if ok:
                self.set_created.add(old_id)

    async def restore_translation_units(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            set_id = str(r["set_id"])
            if set_id not in self.set_created:
                self.skipped["translation_units"] += 1
                continue
            await self._insert(
                "translation_units",
                TranslationUnit(
                    set_id=set_id,
                    block_id=r["block_id"],
                    source_hash=r["source_hash"],
                    content_ja=r["content_ja"],
                    text_ja=r["text_ja"],
                    state=r.get("state") or "machine",
                    quality_flags=list(r.get("quality_flags") or []),
                    proposal=r.get("proposal"),
                    model=r.get("model") or "",
                    created_at=_dt(r.get("created_at")),
                    updated_at=_dt(r.get("updated_at")),
                ),
                f"{set_id}/{r.get('block_id')}",
            )

    async def restore_glossaries(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(Glossary, old_id) is not None:
                self.skipped["glossaries"] += 1
                continue
            scope = r.get("scope") or "global"
            user_id = self.uid if scope == "user" else None
            lib_id = None
            if scope == "paper" and r.get("library_item_id"):
                lib_id = self.item_map.get(str(r["library_item_id"]))
            await self._insert(
                "glossaries",
                Glossary(
                    id=old_id,
                    scope=scope,
                    user_id=user_id,
                    library_item_id=lib_id,
                    name=r.get("name") or "",
                    created_at=_dt(r.get("created_at")),
                    updated_at=_dt(r.get("updated_at")),
                ),
                old_id,
            )

    async def restore_glossary_terms(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(GlossaryTerm, old_id) is not None:
                self.skipped["glossary_terms"] += 1
                continue
            await self._insert(
                "glossary_terms",
                GlossaryTerm(
                    id=old_id,
                    glossary_id=str(r["glossary_id"]),
                    source_term=r["source_term"],
                    target_term=r["target_term"],
                    pos_label=r.get("pos_label") or "",
                    policy=r.get("policy") or "translate",
                    auto_extracted=bool(r.get("auto_extracted")),
                    created_at=_dt(r.get("created_at")),
                    updated_at=_dt(r.get("updated_at")),
                ),
                old_id,
            )

    async def restore_notes(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(Note, old_id) is not None:
                self.skipped["notes"] += 1
                continue
            lib_id = self.item_map.get(str(r["library_item_id"]))
            if lib_id is None:
                self.failed.append(
                    {"table": "notes", "id": old_id, "error": "unmapped library_item_id"}
                )
                continue
            await self._insert(
                "notes",
                Note(
                    id=old_id,
                    library_item_id=lib_id,
                    title=r.get("title") or "",
                    body_md=r.get("body_md") or "",
                    anchors=r.get("anchors") or [],
                    # 由来チャットメッセージ(INT PK)は移行先で再採番されるため解決不能。
                    # nullable / ON DELETE SET NULL なので NULL に落とす(来歴の軽微な劣化)。
                    source_chat_message_id=None,
                    created_at=_dt(r.get("created_at")),
                    updated_at=_dt(r.get("updated_at")),
                ),
                old_id,
            )

    async def restore_annotations(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(Annotation, old_id) is not None:
                self.skipped["annotations"] += 1
                continue
            lib_id = self.item_map.get(str(r["library_item_id"]))
            if lib_id is None:
                self.failed.append(
                    {"table": "annotations", "id": old_id, "error": "unmapped library_item_id"}
                )
                continue
            # quote は GENERATED 列。渡すと GeneratedAlwaysError で必ず失敗するため設定しない。
            await self._insert(
                "annotations",
                Annotation(
                    id=old_id,
                    library_item_id=lib_id,
                    kind=r["kind"],
                    color=r.get("color"),
                    body=r.get("body"),
                    anchor=r["anchor"],
                    orphaned=bool(r.get("orphaned")),
                    created_at=_dt(r.get("created_at")),
                    updated_at=_dt(r.get("updated_at")),
                ),
                old_id,
            )

    async def restore_chat_threads(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["thread_id"])
            if await self.session.get(ChatThread, old_id) is not None:
                self.skipped["chat_threads"] += 1
                continue
            lib_id = self.item_map.get(str(r["library_item_id"]))
            if lib_id is None:
                self.failed.append(
                    {"table": "chat_threads", "id": old_id, "error": "unmapped library_item_id"}
                )
                continue
            ok = await self._insert(
                "chat_threads",
                ChatThread(
                    id=old_id,
                    library_item_id=lib_id,
                    title=r.get("title") or "メイン",
                    is_main=bool(r.get("is_main")),
                ),
                old_id,
            )
            if ok:
                self.thread_created.add(old_id)

    async def restore_chat_messages(self, threads: list[dict[str, Any]]) -> None:
        for thread in threads:
            old_thread = str(thread["thread_id"])
            # 親スレッドが今回新規作成された場合のみ挿入(既存なら履歴の二重化を避け skip)。
            inserted = old_thread in self.thread_created
            for m in thread.get("messages", []):
                if not inserted:
                    self.skipped["chat_messages"] += 1
                    continue
                await self._insert(
                    "chat_messages",
                    ChatMessage(
                        thread_id=old_thread,
                        role=m["role"],
                        content=m.get("content") if m.get("content") is not None else {},
                        text_plain=m.get("text") or "",
                        context_anchors=m.get("context_anchors") or [],
                        evidence_anchors=m.get("evidence_anchors") or [],
                        status=m.get("status") or "complete",
                        error=m.get("error"),
                        provider=m.get("provider") or "",
                        model=m.get("model") or "",
                        created_at=_dt(m.get("created_at")),
                    ),
                    old_thread,
                )

    async def restore_vocab(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(VocabEntry, old_id) is not None:
                self.skipped["vocab"] += 1
                continue
            lib_id = self.item_map.get(str(r["library_item_id"]))
            if lib_id is None:
                self.failed.append(
                    {"table": "vocab", "id": old_id, "error": "unmapped library_item_id"}
                )
                continue
            srs = r.get("srs") or {}
            await self._insert(
                "vocab",
                VocabEntry(
                    id=old_id,
                    user_id=self.uid,
                    library_item_id=lib_id,
                    kind=r.get("kind") or "word",
                    term=r["term"],
                    pos_label=r.get("pos_label") or "",
                    ipa=r.get("ipa") or "",
                    context_anchor=r.get("context_anchor") or {},
                    context_sentence=r.get("context_sentence") or "",
                    context_hl_start=r.get("context_hl_start") or 0,
                    context_hl_end=r.get("context_hl_end") or 0,
                    meaning_short=r.get("meaning_short") or "",
                    meaning_long=r.get("meaning_long") or "",
                    interpretation=r.get("interpretation") or "",
                    etymology=r.get("etymology") or "",
                    mnemonic=r.get("mnemonic") or "",
                    related_forms=r.get("related_forms") or "",
                    edited_fields=list(r.get("edited_fields") or []),
                    generation_status=r.get("generation_status") or "pending",
                    generation_error=r.get("generation_error"),
                    srs_stage=srs.get("stage", 1),
                    srs_next_review_on=_date(srs.get("next_review_on")),
                    srs_review_count=srs.get("review_count", 0),
                    srs_mastered=bool(srs.get("mastered")),
                    srs_history=srs.get("history") or [],
                    created_at=_dt(r.get("created_at")),
                ),
                old_id,
            )

    async def restore_resources(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(ResourceLink, old_id) is not None:
                self.skipped["resources"] += 1
                continue
            lib_id = self.item_map.get(str(r["library_item_id"]))
            if lib_id is None:
                self.failed.append(
                    {"table": "resources", "id": old_id, "error": "unmapped library_item_id"}
                )
                continue
            url = r.get("url") or ""
            await self._insert(
                "resources",
                ResourceLink(
                    id=old_id,
                    library_item_id=lib_id,
                    status=r.get("status") or "active",
                    kind=r["kind"],
                    url=url,
                    # url_normalized はエクスポートに無い。一意制約 (item, url_normalized) を
                    # 満たすため raw url を流用する(軽微な劣化: 正規化重複判定が緩む)。
                    url_normalized=url,
                    official=bool(r.get("official")),
                    title=r.get("title") or "",
                    note_md=r.get("note_md") or "",
                    created_at=_dt(r.get("created_at")),
                ),
                old_id,
            )

    async def restore_articles(self, rows: list[dict[str, Any]]) -> None:
        for a in rows:
            old_id = str(a["article_id"])
            if await self.session.get(Article, old_id) is not None:
                self.skipped["articles"] += 1
                continue
            lib_id = self.item_map.get(str(a["library_item_id"]))
            if lib_id is None:
                self.failed.append(
                    {"table": "articles", "id": old_id, "error": "unmapped library_item_id"}
                )
                continue
            ok = await self._insert(
                "articles",
                Article(
                    id=old_id,
                    library_item_id=lib_id,
                    title=a.get("title") or "",
                    preset=a.get("preset") or "beginner",
                    version=a.get("version") or 1,
                    generated_at=_dt(a.get("generated_at")),
                ),
                old_id,
            )
            if ok:
                self.article_created.add(old_id)

    async def restore_article_blocks(self, articles: list[dict[str, Any]]) -> None:
        for a in articles:
            old_article = str(a["article_id"])
            inserted = old_article in self.article_created
            for pos, b in enumerate(a.get("blocks", [])):
                if not inserted:
                    self.skipped["article_blocks"] += 1
                    continue
                await self._insert(
                    "article_blocks",
                    ArticleBlock(
                        article_id=old_article,
                        # position はエクスポートに無い。ブロックは position 昇順で出力される
                        # ため列挙インデックスで復元する。
                        position=pos,
                        type=b["type"],
                        content=b.get("content") or {},
                        text_plain=b.get("text_plain") or "",
                        origin=b.get("origin") or "ai",
                    ),
                    old_article,
                )

    async def restore_collections(self, rows: list[dict[str, Any]]) -> None:
        for c in rows:
            old_id = str(c["id"])
            if await self.session.get(Collection, old_id) is not None:
                self.skipped["collections"] += 1
                continue
            ok = await self._insert(
                "collections",
                Collection(
                    id=old_id,
                    user_id=self.uid,
                    name=c.get("name") or "",
                    description=c.get("description") or "",
                    deadline=_date(c.get("deadline")),
                    created_at=_dt(c.get("created_at")),
                ),
                old_id,
            )
            if ok:
                self.collection_created.add(old_id)

    async def restore_collection_entries(self, collections: list[dict[str, Any]]) -> None:
        for c in collections:
            old_id = str(c["id"])
            inserted = old_id in self.collection_created
            for pos, old_item in enumerate(c.get("library_item_ids", [])):
                if not inserted:
                    self.skipped["collection_entries"] += 1
                    continue
                lib_id = self.item_map.get(str(old_item))
                if lib_id is None:
                    self.failed.append(
                        {
                            "table": "collection_entries",
                            "id": old_id,
                            "error": "unmapped library_item_id",
                        }
                    )
                    continue
                await self._insert(
                    "collection_entries",
                    CollectionEntry(
                        id=str(uuid.uuid4()),
                        collection_id=old_id,
                        library_item_id=lib_id,
                        position=pos,
                    ),
                    old_id,
                )

    async def restore_share_tokens(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(CollectionShareToken, old_id) is not None:
                self.skipped["share_tokens"] += 1
                continue
            await self._insert(
                "share_tokens",
                CollectionShareToken(
                    id=old_id,
                    collection_id=str(r["collection_id"]),
                    token=r["token"],
                    status=r.get("status") or "active",
                    include_notes=bool(r.get("include_notes")),
                    created_at=_dt(r.get("created_at")),
                    revoked_at=_dt(r.get("revoked_at")),
                ),
                old_id,
            )

    async def restore_saved_filters(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_id = str(r["id"])
            if await self.session.get(SavedFilter, old_id) is not None:
                self.skipped["saved_filters"] += 1
                continue
            await self._insert(
                "saved_filters",
                SavedFilter(
                    id=old_id,
                    user_id=self.uid,
                    name=r["name"],
                    conditions=r.get("conditions") or {},
                    sort=r.get("sort") or {"key": "updated_at", "order": "desc"},
                    position=r.get("position") or 0,
                    created_at=_dt(r.get("created_at")),
                    updated_at=_dt(r.get("updated_at")),
                ),
                old_id,
            )

    async def restore_reading_sessions(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            old_item = str(r["library_item_id"])
            # 親 library_item が今回新規作成された時だけ復元(既存なら履歴二重化を避ける)。
            if old_item not in self.item_created:
                self.skipped["reading_sessions"] += 1
                continue
            lib_id = self.item_map[old_item]
            await self._insert(
                "reading_sessions",
                ReadingSession(
                    library_item_id=lib_id,
                    started_at=_dt(r.get("started_at")),
                    ended_at=_dt(r.get("ended_at")),
                    active_seconds=r.get("active_seconds") or 0,
                    view_mode=r.get("view_mode") or "translation",
                    created_at=_dt(r.get("created_at")),
                ),
                old_item,
            )

    async def restore_notifications(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            created_at = _dt(r.get("created_at"))
            # 親(ユーザー)は常に事前作成されるため親生成ゲートは使えない。
            # 自然キー (user_id, kind, created_at) で冪等化する。
            exists = (
                await self.session.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        Notification.user_id == self.uid,
                        Notification.kind == r["kind"],
                        Notification.created_at == created_at,
                    )
                )
            ).scalar_one()
            if exists:
                self.skipped["notifications"] += 1
                continue
            await self._insert(
                "notifications",
                Notification(
                    user_id=self.uid,
                    kind=r["kind"],
                    payload=r.get("payload") or {},
                    read=bool(r.get("read")),
                    created_at=created_at,
                ),
                None,
            )

    async def restore_user_settings(self, settings: object) -> None:
        if not isinstance(settings, dict):
            return
        user = await self.session.get(User, self.uid)
        if user is not None:
            # 既存 settings キーは変更しない。バックアップ側の新規キーのみ補完する。
            user.settings = merge_missing(user.settings or {}, settings)

    async def restore_overview_figures(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            figure_id = str(row["id"])
            if str(row["article_id"]) not in self.article_created:
                self.failed.append(
                    {"table": "overview_figures", "id": figure_id, "error": "unmapped article_id"}
                )
                continue
            if await self.session.get(OverviewFigure, figure_id) is not None:
                self.skipped["overview_figures"] += 1
                continue
            await self._insert(
                "overview_figures",
                OverviewFigure(
                    id=figure_id,
                    article_id=str(row["article_id"]),
                    version=row["version"],
                    is_current=bool(row.get("is_current")),
                    render_mode=row.get("render_mode") or "svg",
                    dsl=row.get("dsl") or {},
                    svg_storage_key=row.get("svg_storage_key"),
                    image_storage_key=row.get("image_storage_key"),
                    provider=row.get("provider") or "",
                    model=row.get("model") or "",
                    prompt=row.get("prompt") or "",
                    instruction=row.get("instruction") or "",
                    evidence_anchors=row.get("evidence_anchors") or [],
                    generated_at=_dt(row.get("generated_at")),
                    created_at=_dt(row.get("created_at")),
                ),
                figure_id,
            )

    async def restore_explainer_figures(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            figure_id = str(row["id"])
            if str(row["article_id"]) not in self.article_created:
                self.failed.append(
                    {"table": "explainer_figures", "id": figure_id, "error": "unmapped article_id"}
                )
                continue
            if await self.session.get(ExplainerFigure, figure_id) is not None:
                self.skipped["explainer_figures"] += 1
                continue
            await self._insert(
                "explainer_figures",
                ExplainerFigure(
                    id=figure_id,
                    article_id=str(row["article_id"]),
                    slot=row.get("slot", 0),
                    version=row.get("version", 1),
                    is_current=bool(row.get("is_current")),
                    provider=row.get("provider") or "",
                    model=row.get("model") or "",
                    prompt=row.get("prompt") or "",
                    image_storage_key=row["image_storage_key"],
                    caption=row.get("caption") or "",
                    evidence_anchors=row.get("evidence_anchors") or [],
                    generated_at=_dt(row.get("generated_at")),
                    created_at=_dt(row.get("created_at")),
                ),
                figure_id,
            )

    async def restore_vocab_candidates(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            candidate_id = str(row["id"])
            if await self.session.get(VocabCandidate, candidate_id) is not None:
                self.skipped["vocab_candidates"] += 1
                continue
            library_item_id = self.item_map.get(str(row["library_item_id"]))
            if library_item_id is None:
                self.failed.append(
                    {
                        "table": "vocab_candidates",
                        "id": candidate_id,
                        "error": "unmapped library_item_id",
                    }
                )
                continue
            vocab_entry_id = row.get("vocab_entry_id")
            if vocab_entry_id and await self.session.get(VocabEntry, str(vocab_entry_id)) is None:
                vocab_entry_id = None
            await self._insert(
                "vocab_candidates",
                VocabCandidate(
                    id=candidate_id,
                    user_id=self.uid,
                    library_item_id=library_item_id,
                    term=row["term"],
                    kind=row.get("kind") or "word",
                    context_anchor=row.get("context_anchor") or {},
                    context_sentence=row.get("context_sentence") or "",
                    context_hl_start=row.get("context_hl_start") or 0,
                    context_hl_end=row.get("context_hl_end") or 0,
                    reason=row.get("reason") or "",
                    status=row.get("status") or "pending",
                    vocab_entry_id=str(vocab_entry_id) if vocab_entry_id else None,
                    created_at=_dt(row.get("created_at")),
                    updated_at=_dt(row.get("updated_at")),
                ),
                candidate_id,
            )

    async def restore_latest_revisions(self, library: list[dict[str, Any]]) -> None:
        for entry in library:
            revision_id = entry.get("latest_revision_id")
            paper_id = self.paper_map.get(str(entry["paper_id"]))
            if not revision_id or paper_id is None:
                continue
            if paper_id not in self.created_paper_ids:
                continue
            revision = await self.session.get(DocumentRevision, str(revision_id))
            paper = await self.session.get(Paper, paper_id)
            if (
                revision is not None
                and paper is not None
                and str(revision.paper_id) == str(paper.id)
            ):
                paper.latest_revision_id = str(revision.id)

    async def rebuild_indexes(self) -> None:
        for rev_id, content in self._pending_index:
            try:
                async with self.session.begin_nested():
                    await rebuild_block_search_index(
                        self.session, rev_id, DocumentContent.model_validate(content)
                    )
            except Exception as exc:
                self.failed.append(
                    {"table": "block_search_index", "id": rev_id, "error": repr(exc)}
                )


async def import_data_json(
    session: AsyncSession, target_user_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """``data.json`` を ``target_user_id`` へ冪等マージ復元する。

    戻り値: ``{"created": {<table>: 件数}, "skipped": {...}, "failed": [{...}]}``。
    """
    imp = _Importer(session, target_user_id)
    library = data.get("library") or []

    await imp.restore_papers(library)
    await imp.restore_source_assets(data.get("source_assets") or [])
    await imp.restore_document_revisions(data.get("document_revisions") or [])
    await imp.restore_library(library)
    await imp.restore_translation_sets(data.get("translation_sets") or [])
    await imp.restore_translation_units(data.get("translation_units") or [])
    await imp.restore_glossaries(data.get("glossaries") or [])
    await imp.restore_glossary_terms(data.get("glossary_terms") or [])
    await imp.restore_notes(data.get("notes") or [])
    await imp.restore_annotations(data.get("annotations") or [])
    await imp.restore_chat_threads(data.get("chat_threads") or [])
    await imp.restore_chat_messages(data.get("chat_threads") or [])
    await imp.restore_vocab(data.get("vocab") or [])
    await imp.restore_resources(data.get("resources") or [])
    articles = data.get("articles") or []
    await imp.restore_articles(articles)
    await imp.restore_article_blocks(articles)
    collections = data.get("collections") or []
    await imp.restore_collections(collections)
    await imp.restore_collection_entries(collections)
    await imp.restore_share_tokens(data.get("share_tokens") or [])
    await imp.restore_saved_filters(data.get("saved_filters") or [])
    await imp.restore_reading_sessions(data.get("reading_sessions") or [])
    await imp.restore_notifications(data.get("notifications") or [])
    await imp.restore_user_settings(data.get("settings"))
    await imp.restore_overview_figures(data.get("overview_figures") or [])
    await imp.restore_explainer_figures(data.get("explainer_figures") or [])
    await imp.restore_vocab_candidates(data.get("vocab_candidates") or [])
    await imp.restore_latest_revisions(library)

    # 新規 document_revision について block_search_index を再構築(索引はエクスポートしない)。
    await imp.rebuild_indexes()

    await session.commit()
    # created/skipped は defaultdict(int) を返す(未計上テーブルへのアクセスは 0)。
    return {
        "created": imp.created,
        "skipped": imp.skipped,
        "failed": imp.failed,
    }


async def run_import_full_job(ctx: dict[str, Any], store: JobStore, job: Any) -> None:
    """``kind='import'`` ハンドラ。

    zip(アップロード済み S3 一時 key)を検証・展開し、``import_data_json`` でデータを
    復元し、manifest の assets を sha256 照合で S3 へ書き戻す。
    - manifest.schema_version != 2 は即座に fail。
    - zip 不正・JSON 破損は fail_with_retry。
    - 部分失敗(個別 asset の sha256 不一致)は summary["failed"] に記録して継続(P3)。
    """
    session = store.session
    storage: S3Storage = ctx.get("s3") or S3Storage(ctx.get("settings"))
    upload_key = (job.payload or {}).get("upload_key")
    if not isinstance(upload_key, str) or not upload_key:
        await store.fail_with_retry(str(job.id), {"code": "import_bad_payload"})
        return

    # 1. zip ダウンロード
    try:
        archive = await storage.get(storage.assets_bucket, upload_key)
    except Exception as exc:
        await store.fail_with_retry(
            str(job.id), {"code": "import_download_failed", "detail": str(exc)}
        )
        return

    # 2. zip 展開・検証・復元
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            members = _validated_members(zf)
            manifest = json.loads(zf.read(members["manifest.json"]))
            if not isinstance(manifest, dict):
                raise ValueError("invalid_manifest")
            if manifest.get("schema_version") != IMPORT_SCHEMA_VERSION:
                await store.fail_with_retry(
                    str(job.id),
                    {
                        "code": "import_schema_mismatch",
                        "detail": str(manifest.get("schema_version")),
                    },
                )
                return
            manifest_assets = _validated_manifest_assets(manifest)

            data = json.loads(zf.read(members["data.json"]))
            if not isinstance(data, dict):
                raise ValueError("invalid_data")
            asset_destinations = _prepare_asset_destinations(data, str(job.user_id))
            summary = await import_data_json(session, str(job.user_id), data)

            # 3. アセット復元(sha256 照合。未一致は skip してサマリへ記録)
            for a in manifest_assets:
                key = a["storage_key"]
                destination = asset_destinations.get(key)
                if destination is None:
                    summary["failed"].append({"asset": key, "reason": "not_referenced"})
                    continue
                logical_bucket, destination_key = destination
                real_bucket = (
                    storage.sources_bucket if logical_bucket == "sources" else storage.assets_bucket
                )
                asset_path = f"assets/{key}"
                if asset_path not in members:
                    summary["failed"].append({"asset": key, "reason": "not_in_zip"})
                    continue
                payload_bytes = zf.read(members[asset_path])
                if hashlib.sha256(payload_bytes).hexdigest() != a.get("sha256", ""):
                    summary["failed"].append({"asset": key, "reason": "sha256_mismatch"})
                    continue
                content_type = a.get("content_type", "application/octet-stream")
                await storage.put(
                    real_bucket, destination_key, payload_bytes, content_type=content_type
                )

    except (
        KeyError,
        ValueError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        await store.fail_with_retry(str(job.id), {"code": "import_bad_archive", "detail": str(exc)})
        return

    await store.succeed(str(job.id), {"summary": summary})
