"""Index exact translation work request-key lookups.

Revision ID: 0007_job_request_key_idx
Revises: 0006_translation_set_plan
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_job_request_key_idx"
down_revision: str | None = "0006_translation_set_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_jobs_translation_request_key "
        "ON jobs ((payload ->> 'request_key'), status, created_at DESC, id DESC) "
        "WHERE kind = 'translation' AND (payload ->> 'request_key') IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_jobs_translation_legacy_work "
        "ON jobs ((payload ->> 'set_id'), (payload ->> 'section_id'), "
        "(payload ->> 'reason'), md5((payload -> 'block_ids')::text), "
        "(payload ->> 'table_block_id'), created_at DESC, id DESC) "
        "WHERE kind = 'translation' AND (payload ->> 'request_key') IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_translation_legacy_work")
    op.execute("DROP INDEX IF EXISTS ix_jobs_translation_request_key")
