"""initial schema — plans/02 §4 の完全 DDL を verbatim 投入する。

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-06

plans/02 §7 の決定に従い、部分一意インデックス・生成列・トリガ・PGroonga は
autogenerate で扱えないため、初期化は手書き SQL を op.execute() で流す。
"""

from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
-- ============================================================
-- 4.1 拡張・共通関数
-- ============================================================
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgroonga;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

-- ============================================================
-- 4.2 認証系
-- ============================================================
CREATE TABLE users (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email          CITEXT      NOT NULL,
    display_name   TEXT        NOT NULL DEFAULT '',
    avatar_url     TEXT,
    settings       JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_settings_object CHECK (jsonb_typeof(settings) = 'object')
);
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE auth_identities (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider         TEXT        NOT NULL,
    provider_subject TEXT        NOT NULL,
    email            CITEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_auth_identities_provider
        CHECK (provider IN ('google', 'github', 'email')),
    CONSTRAINT uq_auth_identities_provider_subject UNIQUE (provider, provider_subject)
);
CREATE INDEX ix_auth_identities_user_id ON auth_identities (user_id);

CREATE TABLE byok_api_keys (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider      TEXT        NOT NULL,
    encrypted_key BYTEA       NOT NULL,
    key_hint      TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'untested'
                  CHECK (status IN ('untested', 'valid', 'invalid')),
    last_tested_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_byok_api_keys_provider
        CHECK (provider IN ('openai', 'anthropic', 'google', 'deepseek', 'xai')),
    CONSTRAINT uq_byok_api_keys_user_provider UNIQUE (user_id, provider)
);
CREATE TRIGGER trg_byok_api_keys_updated_at BEFORE UPDATE ON byok_api_keys
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 4.3 論文実体
-- ============================================================
CREATE TABLE papers (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    arxiv_id          TEXT,
    doi               TEXT,
    pdf_sha256        TEXT,
    title             TEXT        NOT NULL,
    authors           JSONB       NOT NULL DEFAULT '[]',
    abstract          TEXT        NOT NULL DEFAULT '',
    abstract_ja       TEXT,
    summary_lines     JSONB,
    published_on      DATE,
    venue             TEXT,
    arxiv_categories  TEXT[]      NOT NULL DEFAULT '{}',
    license           TEXT        NOT NULL DEFAULT 'unknown',
    bib_estimated     BOOLEAN     NOT NULL DEFAULT false,
    visibility        TEXT        NOT NULL DEFAULT 'public',
    owner_user_id     UUID        REFERENCES users(id) ON DELETE CASCADE,
    latest_version    TEXT,
    official_repo_url TEXT,
    extracted_terms   JSONB       NOT NULL DEFAULT '[]',
    thumbnail_key     TEXT,
    latest_revision_id UUID,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_papers_visibility CHECK (visibility IN ('public', 'private')),
    CONSTRAINT ck_papers_license CHECK (license IN (
        'cc-by-4.0', 'cc-by-sa-4.0', 'cc-by-nc-4.0', 'cc-by-nc-sa-4.0', 'cc-by-nd-4.0',
        'cc-by-nc-nd-4.0',
        'cc0', 'arxiv-nonexclusive', 'unknown')),
    CONSTRAINT ck_papers_private_has_owner
        CHECK (visibility = 'public' OR owner_user_id IS NOT NULL)
);
CREATE UNIQUE INDEX uq_papers_arxiv_id   ON papers (arxiv_id)   WHERE arxiv_id IS NOT NULL;
CREATE UNIQUE INDEX uq_papers_doi        ON papers (doi)        WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX uq_papers_owner_pdf_sha256
    ON papers (owner_user_id, pdf_sha256) WHERE pdf_sha256 IS NOT NULL;
CREATE INDEX ix_papers_owner_user_id ON papers (owner_user_id) WHERE owner_user_id IS NOT NULL;
CREATE TRIGGER trg_papers_updated_at BEFORE UPDATE ON papers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE source_assets (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id       UUID        NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    kind           TEXT        NOT NULL,
    source_url     TEXT,
    source_version TEXT,
    storage_key    TEXT        NOT NULL,
    content_type   TEXT        NOT NULL DEFAULT 'application/octet-stream',
    byte_size      BIGINT      NOT NULL DEFAULT 0,
    sha256         TEXT,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_source_assets_kind CHECK (kind IN
        ('arxiv_latex', 'arxiv_html', 'pdf', 'translated_pdf', 'bilingual_pdf',
         'metadata_api', 'extension_capture', 'latex_project_manifest'))
);
CREATE INDEX ix_source_assets_paper_id ON source_assets (paper_id);

CREATE TABLE document_revisions (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id       UUID        NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    source_version TEXT        NOT NULL DEFAULT 'v1',
    parser_version TEXT        NOT NULL,
    quality_level  TEXT        NOT NULL,
    source_format  TEXT        NOT NULL,
    content        JSONB       NOT NULL,
    stats          JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_document_revisions_quality CHECK (quality_level IN ('A', 'B')),
    CONSTRAINT ck_document_revisions_format  CHECK (source_format IN ('latex', 'arxiv_html', 'pdf')),
    CONSTRAINT uq_document_revisions_paper_ver_parser
        UNIQUE (paper_id, source_version, parser_version)
);
CREATE INDEX ix_document_revisions_paper_id ON document_revisions (paper_id);

ALTER TABLE papers
    ADD CONSTRAINT fk_papers_latest_revision
    FOREIGN KEY (latest_revision_id) REFERENCES document_revisions(id) ON DELETE SET NULL;

CREATE TABLE block_search_index (
    id                BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    revision_id       UUID    NOT NULL REFERENCES document_revisions(id) ON DELETE CASCADE,
    block_id          TEXT    NOT NULL,
    block_type        TEXT    NOT NULL,
    section_path      TEXT    NOT NULL,
    section_label     TEXT    NOT NULL,
    paragraph_ordinal INT,
    element_label     TEXT,
    position          INT     NOT NULL,
    source_text       TEXT    NOT NULL DEFAULT '',
    in_translation_scope BOOLEAN NOT NULL DEFAULT false,
    page              INT,
    bbox              REAL[],
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_block_search_index_rev_block UNIQUE (revision_id, block_id),
    CONSTRAINT ck_block_search_index_type CHECK (block_type IN
        ('paragraph','heading','figure','table','equation','code','list',
         'quote','theorem','algorithm','footnote','reference_entry'))
);
CREATE INDEX ix_block_search_index_rev_pos  ON block_search_index (revision_id, position);
CREATE INDEX ix_block_search_index_rev_page ON block_search_index (revision_id, page)
    WHERE page IS NOT NULL;

-- ============================================================
-- 4.4 翻訳
-- ============================================================
CREATE TABLE translation_sets (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    revision_id       UUID        NOT NULL REFERENCES document_revisions(id) ON DELETE CASCADE,
    style             TEXT        NOT NULL DEFAULT 'natural',
    scope             TEXT        NOT NULL DEFAULT 'shared',
    user_id           UUID        REFERENCES users(id) ON DELETE CASCADE,
    base_set_id       UUID        REFERENCES translation_sets(id) ON DELETE CASCADE,
    glossary_snapshot JSONB       NOT NULL DEFAULT '[]',
    prompt_version    TEXT        NOT NULL DEFAULT 'tr-2026-07-06.1',
    status            TEXT        NOT NULL DEFAULT 'pending',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_translation_sets_style  CHECK (style IN ('natural', 'literal')),
    CONSTRAINT ck_translation_sets_scope  CHECK (scope IN ('shared', 'personal')),
    CONSTRAINT ck_translation_sets_status CHECK (status IN ('pending', 'partial', 'complete')),
    CONSTRAINT ck_translation_sets_scope_user CHECK (
        (scope = 'shared'   AND user_id IS NULL     AND base_set_id IS NULL) OR
        (scope = 'personal' AND user_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX uq_translation_sets_shared
    ON translation_sets (revision_id, style) WHERE scope = 'shared';
CREATE UNIQUE INDEX uq_translation_sets_personal
    ON translation_sets (revision_id, style, user_id) WHERE scope = 'personal';
CREATE TRIGGER trg_translation_sets_updated_at BEFORE UPDATE ON translation_sets
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE translation_units (
    id            BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    set_id        UUID        NOT NULL REFERENCES translation_sets(id) ON DELETE CASCADE,
    block_id      TEXT        NOT NULL,
    source_hash   TEXT        NOT NULL,
    content_ja    JSONB       NOT NULL,
    text_ja       TEXT        NOT NULL,
    state         TEXT        NOT NULL DEFAULT 'machine',
    quality_flags TEXT[]      NOT NULL DEFAULT '{}',
    proposal      JSONB,
    model         TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_translation_units_state CHECK (state IN ('machine', 'edited', 'protected')),
    CONSTRAINT uq_translation_units_set_block UNIQUE (set_id, block_id)
);
CREATE TRIGGER trg_translation_units_updated_at BEFORE UPDATE ON translation_units
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 4.5 用語集
-- ============================================================
CREATE TABLE glossaries (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scope           TEXT        NOT NULL,
    user_id         UUID        REFERENCES users(id) ON DELETE CASCADE,
    library_item_id UUID,
    name            TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_glossaries_scope CHECK (scope IN ('global', 'user', 'paper')),
    CONSTRAINT ck_glossaries_scope_refs CHECK (
        (scope = 'global' AND user_id IS NULL     AND library_item_id IS NULL) OR
        (scope = 'user'   AND user_id IS NOT NULL AND library_item_id IS NULL) OR
        (scope = 'paper'  AND user_id IS NULL     AND library_item_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX uq_glossaries_user  ON glossaries (user_id)         WHERE scope = 'user';
CREATE UNIQUE INDEX uq_glossaries_paper ON glossaries (library_item_id) WHERE scope = 'paper';
CREATE TRIGGER trg_glossaries_updated_at BEFORE UPDATE ON glossaries
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE glossary_terms (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    glossary_id UUID        NOT NULL REFERENCES glossaries(id) ON DELETE CASCADE,
    source_term TEXT        NOT NULL,
    target_term TEXT        NOT NULL,
    pos_label   TEXT        NOT NULL DEFAULT '',
    policy      TEXT        NOT NULL DEFAULT 'translate',
    auto_extracted BOOLEAN  NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_glossary_terms_policy CHECK (policy IN ('translate', 'keep_original', 'both'))
);
CREATE UNIQUE INDEX uq_glossary_terms_glossary_term
    ON glossary_terms (glossary_id, lower(source_term));
CREATE TRIGGER trg_glossary_terms_updated_at BEFORE UPDATE ON glossary_terms
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 4.6 個人ライブラリ
-- ============================================================
CREATE TABLE library_items (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    paper_id             UUID        NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    status               TEXT        NOT NULL DEFAULT 'planned',
    priority             TEXT,
    deadline             DATE,
    tags                 TEXT[]      NOT NULL DEFAULT '{}',
    suggested_tags       TEXT[]      NOT NULL DEFAULT '{}',
    one_line_note        TEXT        NOT NULL DEFAULT '',
    understanding        SMALLINT,
    importance           TEXT,
    reading_position     JSONB,
    queue_order          INT,
    total_active_seconds BIGINT      NOT NULL DEFAULT 0,
    thumbnail_key        TEXT,
    added_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_library_items_user_paper UNIQUE (user_id, paper_id),
    CONSTRAINT ck_library_items_status CHECK (status IN
        ('planned', 'up_next', 'reading', 'done', 'reread', 'on_hold')),
    CONSTRAINT ck_library_items_priority   CHECK (priority   IN ('high', 'mid', 'low')),
    CONSTRAINT ck_library_items_importance CHECK (importance IN ('low', 'mid', 'high')),
    CONSTRAINT ck_library_items_understanding CHECK (understanding BETWEEN 1 AND 5)
);
CREATE INDEX ix_library_items_user_status   ON library_items (user_id, status);
CREATE INDEX ix_library_items_user_deadline ON library_items (user_id, deadline)
    WHERE deadline IS NOT NULL;
CREATE INDEX ix_library_items_user_updated  ON library_items (user_id, updated_at DESC);
CREATE INDEX ix_library_items_tags          ON library_items USING gin (tags);
CREATE INDEX ix_library_items_paper_id      ON library_items (paper_id);
CREATE TRIGGER trg_library_items_updated_at BEFORE UPDATE ON library_items
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE glossaries
    ADD CONSTRAINT fk_glossaries_library_item
    FOREIGN KEY (library_item_id) REFERENCES library_items(id) ON DELETE CASCADE;

-- ============================================================
-- 4.7 読解資産
-- ============================================================
CREATE TABLE chat_threads (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    library_item_id UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    title           TEXT        NOT NULL DEFAULT 'メイン',
    is_main         BOOLEAN     NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_chat_threads_main
    ON chat_threads (library_item_id) WHERE is_main;
CREATE INDEX ix_chat_threads_library_item ON chat_threads (library_item_id);
CREATE TRIGGER trg_chat_threads_updated_at BEFORE UPDATE ON chat_threads
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE chat_messages (
    id               BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id        UUID        NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    role             TEXT        NOT NULL,
    content          JSONB       NOT NULL,
    text_plain       TEXT        NOT NULL DEFAULT '',
    context_anchors  JSONB       NOT NULL DEFAULT '[]',
    evidence_anchors JSONB       NOT NULL DEFAULT '[]',
    status           TEXT        NOT NULL DEFAULT 'complete',
    error            TEXT,
    provider         TEXT        NOT NULL DEFAULT '',
    model            TEXT        NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_chat_messages_role   CHECK (role IN ('user', 'assistant')),
    CONSTRAINT ck_chat_messages_status CHECK (status IN ('streaming', 'complete', 'error'))
);
CREATE INDEX ix_chat_messages_thread_created ON chat_messages (thread_id, created_at);

CREATE TABLE notes (
    id                     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    library_item_id        UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    title                  TEXT        NOT NULL DEFAULT '',
    body_md                TEXT        NOT NULL DEFAULT '',
    anchors                JSONB       NOT NULL DEFAULT '[]',
    source_chat_message_id BIGINT      REFERENCES chat_messages(id) ON DELETE SET NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_notes_library_item ON notes (library_item_id);
CREATE TRIGGER trg_notes_updated_at BEFORE UPDATE ON notes
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE annotations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    library_item_id UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    kind            TEXT        NOT NULL,
    color           TEXT,
    body            TEXT,
    anchor          JSONB       NOT NULL,
    quote           TEXT        GENERATED ALWAYS AS (anchor->>'quote') STORED,
    orphaned        BOOLEAN     NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_annotations_kind  CHECK (kind IN ('highlight', 'comment', 'bookmark')),
    CONSTRAINT ck_annotations_color CHECK (color IN ('important', 'question', 'idea', 'term')),
    CONSTRAINT ck_annotations_kind_shape CHECK (
        (kind = 'bookmark'  AND color IS NULL     AND body IS NULL) OR
        (kind = 'highlight' AND color IS NOT NULL AND body IS NULL) OR
        (kind = 'comment'   AND color IS NOT NULL AND body IS NOT NULL)
    )
);
CREATE INDEX ix_annotations_library_item ON annotations (library_item_id);
CREATE INDEX ix_annotations_block
    ON annotations (library_item_id, (anchor->>'block_id'));

-- ============================================================
-- 4.8 語彙帳
-- ============================================================
CREATE TABLE vocab_entries (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    library_item_id   UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    kind              TEXT        NOT NULL DEFAULT 'word',
    term              TEXT        NOT NULL,
    pos_label         TEXT        NOT NULL DEFAULT '',
    ipa               TEXT        NOT NULL DEFAULT '',
    context_anchor    JSONB       NOT NULL,
    context_sentence  TEXT        NOT NULL,
    context_hl_start  INT         NOT NULL DEFAULT 0,
    context_hl_end    INT         NOT NULL DEFAULT 0,
    meaning_short     TEXT        NOT NULL DEFAULT '',
    meaning_long      TEXT        NOT NULL DEFAULT '',
    interpretation    TEXT        NOT NULL DEFAULT '',
    etymology         TEXT        NOT NULL DEFAULT '',
    mnemonic          TEXT        NOT NULL DEFAULT '',
    related_forms     TEXT        NOT NULL DEFAULT '',
    edited_fields     TEXT[]      NOT NULL DEFAULT '{}',
    generation_status TEXT        NOT NULL DEFAULT 'pending',
    generation_error  TEXT,
    srs_stage         SMALLINT    NOT NULL DEFAULT 1,
    srs_next_review_on DATE       NOT NULL DEFAULT (CURRENT_DATE + 1),
    srs_review_count  INT         NOT NULL DEFAULT 0,
    srs_mastered      BOOLEAN     NOT NULL DEFAULT false,
    srs_history       JSONB       NOT NULL DEFAULT '[]',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_vocab_entries_kind CHECK (kind IN ('word', 'collocation', 'idiom')),
    CONSTRAINT ck_vocab_entries_genstatus
        CHECK (generation_status IN ('pending', 'complete', 'failed')),
    CONSTRAINT ck_vocab_entries_stage CHECK (srs_stage BETWEEN 1 AND 5)
);
CREATE UNIQUE INDEX uq_vocab_entries_user_term ON vocab_entries (user_id, lower(term));
CREATE INDEX ix_vocab_entries_user_due
    ON vocab_entries (user_id, srs_next_review_on) WHERE NOT srs_mastered;
CREATE INDEX ix_vocab_entries_user_created ON vocab_entries (user_id, created_at DESC);
CREATE INDEX ix_vocab_entries_library_item ON vocab_entries (library_item_id);
CREATE TRIGGER trg_vocab_entries_updated_at BEFORE UPDATE ON vocab_entries
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 4.9 リソース
-- ============================================================
CREATE TABLE resource_links (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    library_item_id UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    status          TEXT        NOT NULL DEFAULT 'active',
    kind            TEXT        NOT NULL,
    url             TEXT        NOT NULL,
    url_normalized  TEXT        NOT NULL,
    official        BOOLEAN     NOT NULL DEFAULT false,
    title           TEXT        NOT NULL DEFAULT '',
    thumbnail_url   TEXT,
    source_domain   TEXT        NOT NULL DEFAULT '',
    meta            JSONB       NOT NULL DEFAULT '{}',
    fetch_status    TEXT        NOT NULL DEFAULT 'pending',
    note_md         TEXT        NOT NULL DEFAULT '',
    note_anchors    JSONB       NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_resource_links_status CHECK (status IN ('suggested', 'active', 'dismissed')),
    CONSTRAINT ck_resource_links_kind   CHECK (kind IN ('github', 'youtube', 'slides', 'article')),
    CONSTRAINT ck_resource_links_fetch  CHECK (fetch_status IN ('pending', 'ok', 'failed')),
    CONSTRAINT uq_resource_links_item_url UNIQUE (library_item_id, url_normalized)
);
CREATE INDEX ix_resource_links_library_item ON resource_links (library_item_id, status);
CREATE TRIGGER trg_resource_links_updated_at BEFORE UPDATE ON resource_links
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 4.10 コレクション・保存フィルタ・通知
-- ============================================================
CREATE TABLE collections (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    deadline    DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_collections_user_id ON collections (user_id);
CREATE TRIGGER trg_collections_updated_at BEFORE UPDATE ON collections
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE collection_entries (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id        UUID        NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    library_item_id      UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    position             INT         NOT NULL,
    assignee             TEXT        NOT NULL DEFAULT '',
    assignee_is_self     BOOLEAN     NOT NULL DEFAULT false,
    presentation_minutes SMALLINT,
    note                 TEXT        NOT NULL DEFAULT '',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_collection_entries_coll_item UNIQUE (collection_id, library_item_id),
    CONSTRAINT uq_collection_entries_coll_pos
        UNIQUE (collection_id, position) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_collection_entries_library_item ON collection_entries (library_item_id);
CREATE TRIGGER trg_collection_entries_updated_at BEFORE UPDATE ON collection_entries
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE collection_share_tokens (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id UUID        NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    token         TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'active',
    include_notes BOOLEAN     NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at    TIMESTAMPTZ,
    CONSTRAINT ck_collection_share_tokens_status CHECK (status IN ('active', 'revoked')),
    CONSTRAINT uq_collection_share_tokens_token UNIQUE (token)
);
CREATE UNIQUE INDEX uq_collection_share_tokens_active
    ON collection_share_tokens (collection_id) WHERE status = 'active';

CREATE TABLE saved_filters (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    conditions JSONB       NOT NULL DEFAULT '{}',
    sort       JSONB       NOT NULL DEFAULT '{"key":"updated_at","order":"desc"}',
    position   INT         NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_saved_filters_user_name UNIQUE (user_id, name)
);
CREATE TRIGGER trg_saved_filters_updated_at BEFORE UPDATE ON saved_filters
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE notifications (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT        NOT NULL,
    payload    JSONB       NOT NULL DEFAULT '{}',
    read       BOOLEAN     NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_notifications_kind CHECK (kind IN
        ('translation_complete', 'status_suggestion', 'deadline_reminder'))
);
CREATE INDEX ix_notifications_user_unread ON notifications (user_id, created_at DESC)
    WHERE NOT read;
CREATE INDEX ix_notifications_user_created ON notifications (user_id, created_at DESC);

-- ============================================================
-- 4.11 記事・図
-- ============================================================
CREATE TABLE articles (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    library_item_id      UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    title                TEXT        NOT NULL,
    preset               TEXT        NOT NULL DEFAULT 'beginner',
    include_math         BOOLEAN     NOT NULL DEFAULT false,
    version              INT         NOT NULL DEFAULT 1,
    generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    instructions_history JSONB       NOT NULL DEFAULT '[]',
    provider             TEXT        NOT NULL DEFAULT '',
    model                TEXT        NOT NULL DEFAULT '',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_articles_library_item UNIQUE (library_item_id),
    CONSTRAINT ck_articles_preset CHECK (preset IN
        ('beginner', 'implementer', 'researcher', 'reading_group'))
);
CREATE TRIGGER trg_articles_updated_at BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE article_blocks (
    id               BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    article_id       UUID        NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    position         INT         NOT NULL,
    type             TEXT        NOT NULL,
    content          JSONB       NOT NULL DEFAULT '{}',
    text_plain       TEXT        NOT NULL DEFAULT '',
    evidence_anchors JSONB       NOT NULL DEFAULT '[]',
    origin           TEXT        NOT NULL DEFAULT 'ai',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_article_blocks_type CHECK (type IN
        ('heading', 'paragraph', 'quote_source', 'figure_embed',
         'explainer_figure', 'discussion', 'attribution')),
    CONSTRAINT ck_article_blocks_origin CHECK (origin IN ('ai', 'user_highlight')),
    CONSTRAINT uq_article_blocks_article_pos
        UNIQUE (article_id, position) DEFERRABLE INITIALLY DEFERRED
);
CREATE TRIGGER trg_article_blocks_updated_at BEFORE UPDATE ON article_blocks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE overview_figures (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id       UUID        NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    version          INT         NOT NULL,
    is_current       BOOLEAN     NOT NULL DEFAULT true,
    render_mode      TEXT        NOT NULL DEFAULT 'svg',
    dsl              JSONB       NOT NULL,
    svg_storage_key  TEXT,
    image_storage_key TEXT,
    provider         TEXT        NOT NULL DEFAULT '',
    model            TEXT        NOT NULL DEFAULT '',
    prompt           TEXT        NOT NULL DEFAULT '',
    instruction      TEXT        NOT NULL DEFAULT '',
    evidence_anchors JSONB       NOT NULL DEFAULT '[]',
    generated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_overview_figures_mode CHECK (render_mode IN ('svg', 'raster')),
    CONSTRAINT uq_overview_figures_article_version UNIQUE (article_id, version)
);
CREATE UNIQUE INDEX uq_overview_figures_current
    ON overview_figures (article_id) WHERE is_current;

CREATE TABLE explainer_figures (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id        UUID        NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    slot              INT         NOT NULL DEFAULT 0,
    version           INT         NOT NULL DEFAULT 1,
    is_current        BOOLEAN     NOT NULL DEFAULT true,
    provider          TEXT        NOT NULL,
    model             TEXT        NOT NULL,
    prompt            TEXT        NOT NULL,
    image_storage_key TEXT        NOT NULL,
    caption           TEXT        NOT NULL DEFAULT '',
    evidence_anchors  JSONB       NOT NULL DEFAULT '[]',
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_explainer_figures_provider CHECK (provider IN ('openai', 'google', 'xai')),
    CONSTRAINT ck_explainer_figures_slot CHECK (slot IN (0, 1)),
    CONSTRAINT uq_explainer_figures_slot_version UNIQUE (article_id, slot, version)
);
CREATE UNIQUE INDEX uq_explainer_figures_current
    ON explainer_figures (article_id, slot) WHERE is_current;

-- ============================================================
-- 4.12 読書セッション
-- ============================================================
CREATE TABLE reading_sessions (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    library_item_id UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    active_seconds  INT         NOT NULL DEFAULT 0,
    view_mode       TEXT        NOT NULL DEFAULT 'translation',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_reading_sessions_view_mode CHECK (view_mode IN
        ('translation', 'parallel', 'source', 'pdf', 'article')),
    CONSTRAINT ck_reading_sessions_active CHECK (active_seconds >= 0)
);
CREATE INDEX ix_reading_sessions_item_started ON reading_sessions (library_item_id, started_at);

-- ============================================================
-- 4.13 ジョブ・コスト計測
-- ============================================================
CREATE TABLE jobs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT        NOT NULL,
    stage           TEXT        NOT NULL DEFAULT 'queued',
    status          TEXT        NOT NULL DEFAULT 'queued',
    progress        SMALLINT    NOT NULL DEFAULT 0,
    priority        INT         NOT NULL DEFAULT 0,
    user_id         UUID        REFERENCES users(id) ON DELETE SET NULL,
    paper_id        UUID        REFERENCES papers(id) ON DELETE CASCADE,
    library_item_id UUID        REFERENCES library_items(id) ON DELETE CASCADE,
    article_id      UUID        REFERENCES articles(id) ON DELETE CASCADE,
    payload         JSONB       NOT NULL DEFAULT '{}',
    result          JSONB       NOT NULL DEFAULT '{}',
    error           TEXT,
    attempt         INT         NOT NULL DEFAULT 0,
    max_attempts    INT         NOT NULL DEFAULT 3,
    log             JSONB       NOT NULL DEFAULT '[]',
    arq_job_id      TEXT,
    idempotency_key TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    next_retry_at   TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_jobs_kind CHECK (kind IN
        ('ingest', 'translation', 'article', 'figure', 'vocab', 'resource_meta', 'export',
         'account_delete')),
    CONSTRAINT ck_jobs_status CHECK (status IN
        ('queued', 'running', 'waiting_quota', 'succeeded', 'failed', 'canceled')),
    CONSTRAINT ck_jobs_progress CHECK (progress BETWEEN 0 AND 100)
);
CREATE INDEX ix_jobs_pick ON jobs (status, priority DESC, created_at)
    WHERE status IN ('queued', 'running');
CREATE INDEX ix_jobs_paper_id        ON jobs (paper_id)        WHERE paper_id IS NOT NULL;
CREATE INDEX ix_jobs_library_item_id ON jobs (library_item_id) WHERE library_item_id IS NOT NULL;
CREATE UNIQUE INDEX uq_jobs_ingest_active ON jobs (paper_id)
    WHERE kind = 'ingest' AND status IN ('queued', 'running', 'waiting_quota');
CREATE UNIQUE INDEX uq_jobs_idempotency_key ON jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE TRIGGER trg_jobs_updated_at BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE usage_records (
    id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id                  UUID REFERENCES users(id) ON DELETE CASCADE,
    library_item_id          UUID REFERENCES library_items(id) ON DELETE SET NULL,
    job_id                   UUID,
    task                     TEXT NOT NULL CHECK (task IN (
                               'translation', 'retranslation_escalation', 'chat', 'summary',
                               'article', 'overview_figure_dsl', 'vocab', 'explainer_image',
                               'key_test')),
    provider                 TEXT NOT NULL CHECK (provider IN
                               ('openai', 'anthropic', 'google', 'deepseek', 'xai')),
    model                    TEXT NOT NULL,
    key_source               TEXT NOT NULL CHECK (key_source IN ('operator', 'user')),
    input_tokens             INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_write_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens            INTEGER NOT NULL DEFAULT 0,
    image_count              INTEGER NOT NULL DEFAULT 0,
    cost_usd                 NUMERIC(12, 8) NOT NULL DEFAULT 0,
    status                   TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    attempt                  INTEGER NOT NULL DEFAULT 1,
    fallback_rank            INTEGER NOT NULL DEFAULT 0,
    error_kind               TEXT,
    latency_ms               INTEGER,
    request_id               TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_usage_records_user_month ON usage_records (user_id, created_at);
CREATE INDEX idx_usage_records_task ON usage_records (task, created_at);

CREATE TABLE quota_limits (
    key           TEXT        PRIMARY KEY,
    monthly_limit INT         NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_quota_limits_updated_at BEFORE UPDATE ON quota_limits
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 4.14 PGroonga 全文検索インデックス(9本。plans/11 §2.2)
-- ============================================================
CREATE INDEX pgroonga_block_search_index_source_text
    ON block_search_index USING pgroonga (source_text)
    WITH (tokenizer     = 'TokenBigram',
          normalizers   = 'NormalizerNFKC150',
          plugins       = 'token_filters/stem',
          token_filters = 'TokenFilterStem');

CREATE INDEX pgroonga_translation_units_text_ja
    ON translation_units USING pgroonga (text_ja)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

CREATE INDEX pgroonga_notes_body
    ON notes USING pgroonga (title, body_md)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

CREATE INDEX pgroonga_annotations_text
    ON annotations USING pgroonga (body)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

CREATE INDEX pgroonga_chat_messages_text
    ON chat_messages USING pgroonga (text_plain)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

CREATE INDEX pgroonga_article_blocks_text
    ON article_blocks USING pgroonga (text_plain)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

CREATE INDEX pgroonga_papers_biblio_en
    ON papers USING pgroonga (title, abstract)
    WITH (tokenizer     = 'TokenBigram',
          normalizers   = 'NormalizerNFKC150',
          plugins       = 'token_filters/stem',
          token_filters = 'TokenFilterStem');

CREATE INDEX pgroonga_papers_biblio_ja
    ON papers USING pgroonga (abstract_ja)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

CREATE INDEX pgroonga_vocab_entries_text
    ON vocab_entries USING pgroonga (term, meaning_short, meaning_long)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');
"""


DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS quota_limits CASCADE;
DROP TABLE IF EXISTS usage_records CASCADE;
DROP TABLE IF EXISTS jobs CASCADE;
DROP TABLE IF EXISTS reading_sessions CASCADE;
DROP TABLE IF EXISTS explainer_figures CASCADE;
DROP TABLE IF EXISTS overview_figures CASCADE;
DROP TABLE IF EXISTS article_blocks CASCADE;
DROP TABLE IF EXISTS articles CASCADE;
DROP TABLE IF EXISTS notifications CASCADE;
DROP TABLE IF EXISTS saved_filters CASCADE;
DROP TABLE IF EXISTS collection_share_tokens CASCADE;
DROP TABLE IF EXISTS collection_entries CASCADE;
DROP TABLE IF EXISTS collections CASCADE;
DROP TABLE IF EXISTS resource_links CASCADE;
DROP TABLE IF EXISTS vocab_entries CASCADE;
DROP TABLE IF EXISTS annotations CASCADE;
DROP TABLE IF EXISTS notes CASCADE;
DROP TABLE IF EXISTS chat_messages CASCADE;
DROP TABLE IF EXISTS chat_threads CASCADE;
DROP TABLE IF EXISTS glossary_terms CASCADE;
DROP TABLE IF EXISTS glossaries CASCADE;
DROP TABLE IF EXISTS library_items CASCADE;
DROP TABLE IF EXISTS translation_units CASCADE;
DROP TABLE IF EXISTS translation_sets CASCADE;
DROP TABLE IF EXISTS block_search_index CASCADE;
DROP TABLE IF EXISTS document_revisions CASCADE;
DROP TABLE IF EXISTS source_assets CASCADE;
DROP TABLE IF EXISTS papers CASCADE;
DROP TABLE IF EXISTS byok_api_keys CASCADE;
DROP TABLE IF EXISTS auth_identities CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP FUNCTION IF EXISTS set_updated_at() CASCADE;
"""


def _split_sql_statements(sql: str) -> list[str]:
    """SQL を個別ステートメントに分割する($$ ドル引用符内の ; は保護)。

    asyncpg は 1 prepared statement に複数コマンドを入れられないため、
    op.execute() をステートメントごとに呼ぶ。
    """
    statements: list[str] = []
    buf: list[str] = []
    in_dollar = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if sql[i : i + 2] == "$$":
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    # コメントのみ・空行のみのチャンクを除去
    return [
        s
        for s in statements
        if any(not ln.strip().startswith("--") and ln.strip() for ln in s.splitlines())
    ]


def _run(sql: str) -> None:
    for stmt in _split_sql_statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
