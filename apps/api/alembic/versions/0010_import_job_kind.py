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
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
        "('ingest', 'translation', 'article', 'figure', 'vocab', 'resource_meta', 'export', "
        "'account_delete'))"
    )
