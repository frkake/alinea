"""Add the durable waiting-for-user-input job state.

Revision ID: 0008_job_waiting_input
Revises: 0007_job_request_key_idx
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_job_waiting_input"
down_revision: str | None = "0007_job_request_key_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACTIVE_INGEST_INDEX = """
CREATE UNIQUE INDEX uq_jobs_ingest_active ON jobs (paper_id)
WHERE kind = 'ingest'
  AND status IN ('queued', 'running', 'waiting_quota', 'waiting_input')
"""

_LEGACY_ACTIVE_INGEST_INDEX = """
CREATE UNIQUE INDEX uq_jobs_ingest_active ON jobs (paper_id)
WHERE kind = 'ingest'
  AND status IN ('queued', 'running', 'waiting_quota')
"""


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_jobs_ingest_active")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_status")
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status CHECK (status IN "
        "('queued', 'running', 'waiting_quota', 'waiting_input', "
        "'succeeded', 'failed', 'canceled'))"
    )
    op.execute(_ACTIVE_INGEST_INDEX)


def downgrade() -> None:
    # An old worker cannot service a user-input checkpoint.  Cancel such jobs instead of
    # silently requeueing them into an implementation that could translate the wrong scope.
    op.execute(
        "UPDATE jobs SET status = 'canceled', finished_at = COALESCE(finished_at, now()) "
        "WHERE status = 'waiting_input'"
    )
    op.execute("DROP INDEX IF EXISTS uq_jobs_ingest_active")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_status")
    op.execute(
        "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status CHECK (status IN "
        "('queued', 'running', 'waiting_quota', 'succeeded', 'failed', 'canceled'))"
    )
    op.execute(_LEGACY_ACTIVE_INGEST_INDEX)
