"""AI word-extraction candidates (S7).

Adds the ``vocab_candidates`` table and the ``vocab_extract`` job kind.

Revision ID: 0010_vocab_candidates
Revises: 0009_user_scoped_ingest
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_vocab_candidates"
down_revision: str | None = "0009_user_scoped_ingest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATE_TABLE = """
CREATE TABLE vocab_candidates (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    library_item_id   UUID        NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    term              TEXT        NOT NULL,
    kind              TEXT        NOT NULL DEFAULT 'word',
    context_anchor    JSONB       NOT NULL,
    context_sentence  TEXT        NOT NULL DEFAULT '',
    context_hl_start  INT         NOT NULL DEFAULT 0,
    context_hl_end    INT         NOT NULL DEFAULT 0,
    reason            TEXT        NOT NULL DEFAULT '',
    status            TEXT        NOT NULL DEFAULT 'pending',
    vocab_entry_id    UUID        REFERENCES vocab_entries(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_vocab_candidates_kind CHECK (kind IN ('word', 'collocation', 'idiom')),
    CONSTRAINT ck_vocab_candidates_status CHECK (status IN ('pending', 'accepted', 'dismissed'))
)
"""

# 同一論文 (library_item) 内で見出し語の正規化一致を一意にする。再抽出・dismiss/accept の
# 冪等性を担保し、一度提案した語を重複提案しない(vocab_entries.uq_vocab_entries_user_term と同型)。
_UNIQUE_INDEX = (
    "CREATE UNIQUE INDEX uq_vocab_candidates_item_term "
    "ON vocab_candidates (library_item_id, lower(term))"
)
_STATUS_INDEX = (
    "CREATE INDEX ix_vocab_candidates_item_status ON vocab_candidates (library_item_id, status)"
)
_TRIGGER = (
    "CREATE TRIGGER trg_vocab_candidates_updated_at BEFORE UPDATE ON vocab_candidates "
    "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
)

# ck_jobs_kind に 'vocab_extract' を追加する(既存の値域を保ったまま超集合にする)。
_JOBS_KIND_WITH_EXTRACT = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'account_delete'))"
)
_JOBS_KIND_ORIGINAL = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', "
    "'resource_meta', 'export', 'account_delete'))"
)


def upgrade() -> None:
    op.execute(_CREATE_TABLE)
    op.execute(_UNIQUE_INDEX)
    op.execute(_STATUS_INDEX)
    op.execute(_TRIGGER)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_WITH_EXTRACT)


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_ORIGINAL)
    op.execute("DROP TABLE IF EXISTS vocab_candidates CASCADE")
