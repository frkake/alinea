"""AI word-extraction candidates (S7).

Adds the ``vocab_candidates`` table and the ``vocab_extract`` job kind.

Revision ID: 0010_vocab_candidates
Revises: 0010_import_job_kind

Integration note: linearized after ``0010_import_job_kind`` (S2). At this point
``ck_jobs_kind`` already allows ``import``; this migration unions ``vocab_extract``
on top so the final constraint permits every base kind PLUS ``import`` PLUS
``vocab_extract``. Downgrade restores the parent state (base kinds + ``import``).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_vocab_candidates"
down_revision: str | None = "0010_import_job_kind"
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
# 統合後は 0010_import_job_kind が先行し 'import' を既に許可しているため、その上に
# 'vocab_extract' を union する(final: base + import + vocab_extract)。
_JOBS_KIND_WITH_EXTRACT = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'import', 'account_delete'))"
)
# downgrade 先(親 = 0010_import_job_kind 適用後の状態: base + import)。
_JOBS_KIND_ORIGINAL = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', "
    "'resource_meta', 'export', 'import', 'account_delete'))"
)


def upgrade() -> None:
    op.execute(_CREATE_TABLE)
    op.execute(_UNIQUE_INDEX)
    op.execute(_STATUS_INDEX)
    op.execute(_TRIGGER)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_WITH_EXTRACT)


def downgrade() -> None:
    # 制約を狭める前に 'vocab_extract' の job 行を除去する(0012 の easy 除去と同方針)。
    op.execute("DELETE FROM jobs WHERE kind = 'vocab_extract'")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_ORIGINAL)
    op.execute("DROP TABLE IF EXISTS vocab_candidates CASCADE")
