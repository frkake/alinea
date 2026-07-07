"""SQLAlchemy 2 モデル(全テーブル)。

正の DDL は apps/api/alembic/versions/0001_initial_schema.py(plans/02 §4 の verbatim SQL)。
本モジュールは同一スキーマの ORM マッピングであり、型・制約は DDL と一致させる。
plans/00 §2 の決定に従いモデルは packages/py-core に一本化する(api/worker 共用)。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, CITEXT, JSONB, REAL, UUID
from sqlalchemy.orm import Mapped, mapped_column

from yakudoku_core.db.base import Base


# 共通の列ファクトリ(mapped_column オブジェクトはテーブル間で共有できないため関数で都度生成)
def _uuid_pk() -> Any:
    return mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    avatar_url: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(CITEXT)
    created_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_subject", name="uq_auth_identities_provider_subject"
        ),
    )


class ByokApiKey(Base):
    __tablename__ = "byok_api_keys"
    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_key: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    key_hint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="untested")
    last_tested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_byok_api_keys_user_provider"),
    )


class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[str] = _uuid_pk()
    arxiv_id: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    pdf_sha256: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    abstract: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    abstract_ja: Mapped[str | None] = mapped_column(Text)
    summary_lines: Mapped[list[Any] | None] = mapped_column(JSONB)
    published_on: Mapped[dt.date | None] = mapped_column(Date)
    venue: Mapped[str | None] = mapped_column(Text)
    arxiv_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    license: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    bib_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="public")
    owner_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    latest_version: Mapped[str | None] = mapped_column(Text)
    official_repo_url: Mapped[str | None] = mapped_column(Text)
    extracted_terms: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    thumbnail_key: Mapped[str | None] = mapped_column(Text)
    latest_revision_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class SourceAsset(Base):
    __tablename__ = "source_assets"
    id: Mapped[str] = _uuid_pk()
    paper_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_version: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="application/octet-stream"
    )
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    sha256: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime] = _now()
    created_at: Mapped[dt.datetime] = _now()


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    id: Mapped[str] = _uuid_pk()
    paper_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    source_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="v1")
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    quality_level: Mapped[str] = mapped_column(Text, nullable=False)
    source_format: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "source_version",
            "parser_version",
            name="uq_document_revisions_paper_ver_parser",
        ),
    )


class BlockSearchIndex(Base):
    __tablename__ = "block_search_index"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    revision_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("document_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    block_id: Mapped[str] = mapped_column(Text, nullable=False)
    block_type: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[str] = mapped_column(Text, nullable=False)
    section_label: Mapped[str] = mapped_column(Text, nullable=False)
    paragraph_ordinal: Mapped[int | None] = mapped_column(Integer)
    element_label: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    in_translation_scope: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))
    created_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint("revision_id", "block_id", name="uq_block_search_index_rev_block"),
    )


class TranslationSet(Base):
    __tablename__ = "translation_sets"
    id: Mapped[str] = _uuid_pk()
    revision_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("document_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    style: Mapped[str] = mapped_column(Text, nullable=False, server_default="natural")
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="shared")
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    base_set_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("translation_sets.id", ondelete="CASCADE")
    )
    glossary_snapshot: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    prompt_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="tr-2026-07-06.1"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class TranslationUnit(Base):
    __tablename__ = "translation_units"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    set_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("translation_sets.id", ondelete="CASCADE"), nullable=False
    )
    block_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    content_ja: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    text_ja: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="machine")
    quality_flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint("set_id", "block_id", name="uq_translation_units_set_block"),
    )


class Glossary(Base):
    __tablename__ = "glossaries"
    id: Mapped[str] = _uuid_pk()
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    library_item_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"
    id: Mapped[str] = _uuid_pk()
    glossary_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("glossaries.id", ondelete="CASCADE"), nullable=False
    )
    source_term: Mapped[str] = mapped_column(Text, nullable=False)
    target_term: Mapped[str] = mapped_column(Text, nullable=False)
    pos_label: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    policy: Mapped[str] = mapped_column(Text, nullable=False, server_default="translate")
    auto_extracted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class LibraryItem(Base):
    __tablename__ = "library_items"
    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="planned")
    priority: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[dt.date | None] = mapped_column(Date)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    suggested_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    one_line_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    understanding: Mapped[int | None] = mapped_column(SmallInteger)
    importance: Mapped[str | None] = mapped_column(Text)
    reading_position: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    queue_order: Mapped[int | None] = mapped_column(Integer)
    total_active_seconds: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    thumbnail_key: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[dt.datetime] = _now()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "paper_id", name="uq_library_items_user_paper"),)


class ChatThread(Base):
    __tablename__ = "chat_threads"
    id: Mapped[str] = _uuid_pk()
    library_item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="メイン")
    is_main: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    text_plain: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    context_anchors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    evidence_anchors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="complete")
    error: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[dt.datetime] = _now()


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[str] = _uuid_pk()
    library_item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    anchors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    source_chat_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class Annotation(Base):
    __tablename__ = "annotations"
    id: Mapped[str] = _uuid_pk()
    library_item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    anchor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # GENERATED ALWAYS AS (anchor->>'quote') STORED(0001 §4.7)。Computed マーカーが
    # 無いと ORM INSERT が quote=NULL を送り GeneratedAlwaysError で必ず失敗する。
    quote: Mapped[str | None] = mapped_column(Text, Computed("(anchor ->> 'quote')"))
    orphaned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class VocabEntry(Base):
    __tablename__ = "vocab_entries"
    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    library_item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="word")
    term: Mapped[str] = mapped_column(Text, nullable=False)
    pos_label: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ipa: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    context_anchor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_sentence: Mapped[str] = mapped_column(Text, nullable=False)
    context_hl_start: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    context_hl_end: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    meaning_short: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    meaning_long: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    interpretation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    etymology: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    mnemonic: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    related_forms: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    edited_fields: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    generation_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    generation_error: Mapped[str | None] = mapped_column(Text)
    srs_stage: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    srs_next_review_on: Mapped[dt.date] = mapped_column(
        Date, nullable=False, server_default=text("(CURRENT_DATE + 1)")
    )
    srs_review_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    srs_mastered: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    srs_history: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class ResourceLink(Base):
    __tablename__ = "resource_links"
    id: Mapped[str] = _uuid_pk()
    library_item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    official: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    fetch_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    note_md: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    note_anchors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint("library_item_id", "url_normalized", name="uq_resource_links_item_url"),
    )


class Collection(Base):
    __tablename__ = "collections"
    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    deadline: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class CollectionEntry(Base):
    __tablename__ = "collection_entries"
    id: Mapped[str] = _uuid_pk()
    collection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    library_item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    assignee: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    assignee_is_self: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    presentation_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint(
            "collection_id", "library_item_id", name="uq_collection_entries_coll_item"
        ),
    )


class CollectionShareToken(Base):
    __tablename__ = "collection_share_tokens"
    id: Mapped[str] = _uuid_pk()
    collection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    include_notes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[dt.datetime] = _now()
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("token", name="uq_collection_share_tokens_token"),)


class SavedFilter(Base):
    __tablename__ = "saved_filters"
    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    sort: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default='{"key":"updated_at","order":"desc"}'
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_saved_filters_user_name"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[dt.datetime] = _now()


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[str] = _uuid_pk()
    library_item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    preset: Mapped[str] = mapped_column(Text, nullable=False, server_default="beginner")
    include_math: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    generated_at: Mapped[dt.datetime] = _now()
    instructions_history: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()
    __table_args__ = (UniqueConstraint("library_item_id", name="uq_articles_library_item"),)


class ArticleBlock(Base):
    __tablename__ = "article_blocks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    text_plain: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    evidence_anchors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default="ai")
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = _now()


class OverviewFigure(Base):
    __tablename__ = "overview_figures"
    id: Mapped[str] = _uuid_pk()
    article_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    render_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="svg")
    dsl: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    svg_storage_key: Mapped[str | None] = mapped_column(Text)
    image_storage_key: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    instruction: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    evidence_anchors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    generated_at: Mapped[dt.datetime] = _now()
    created_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint("article_id", "version", name="uq_overview_figures_article_version"),
    )


class ExplainerFigure(Base):
    __tablename__ = "explainer_figures"
    id: Mapped[str] = _uuid_pk()
    article_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    slot: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    image_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    evidence_anchors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    generated_at: Mapped[dt.datetime] = _now()
    created_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint("article_id", "slot", "version", name="uq_explainer_figures_slot_version"),
    )


class ReadingSession(Base):
    __tablename__ = "reading_sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    library_item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[dt.datetime] = _now()
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    view_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="translation")
    created_at: Mapped[dt.datetime] = _now()


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = _uuid_pk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL")
    )
    paper_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("papers.id", ondelete="CASCADE")
    )
    library_item_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="CASCADE")
    )
    article_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("articles.id", ondelete="CASCADE")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    log: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    arq_job_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now()
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = _now()


class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE")
    )
    library_item_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("library_items.id", ondelete="SET NULL")
    )
    job_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    task: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    key_source: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_write_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    fallback_rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_kind: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now()


class QuotaLimit(Base):
    __tablename__ = "quota_limits"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    monthly_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[dt.datetime] = _now()


__all__ = [
    "Annotation",
    "Article",
    "ArticleBlock",
    "AuthIdentity",
    "Base",
    "BlockSearchIndex",
    "ByokApiKey",
    "ChatMessage",
    "ChatThread",
    "Collection",
    "CollectionEntry",
    "CollectionShareToken",
    "DocumentRevision",
    "ExplainerFigure",
    "Glossary",
    "GlossaryTerm",
    "Job",
    "LibraryItem",
    "Note",
    "Notification",
    "OverviewFigure",
    "Paper",
    "QuotaLimit",
    "ReadingSession",
    "ResourceLink",
    "SavedFilter",
    "SourceAsset",
    "TranslationSet",
    "TranslationUnit",
    "UsageRecord",
    "User",
    "VocabEntry",
]
