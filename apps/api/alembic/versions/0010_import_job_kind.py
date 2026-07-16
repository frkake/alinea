"""Add 'import' to ck_jobs_kind check constraint.

Revision ID: 0010_import_job_kind
Revises: 0009_user_scoped_ingest
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_import_job_kind"
down_revision: str | None = "0009_user_scoped_ingest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
        "('ingest', 'translation', 'article', 'figure', 'vocab', 'resource_meta', 'export', "
        "'import', 'account_delete'))"
    )


def downgrade() -> None:
    # 制約を狭める前に 'import' の job 行を除去する(0011/0012 の同方針・可逆性の担保)。
    op.execute("DELETE FROM jobs WHERE kind = 'import'")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
        "('ingest', 'translation', 'article', 'figure', 'vocab', 'resource_meta', 'export', "
        "'account_delete'))"
    )
