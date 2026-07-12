"""Scope active ingest uniqueness to its owning user.

Revision ID: 0009_user_scoped_ingest
Revises: 0008_job_waiting_input
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_user_scoped_ingest"
down_revision: str | None = "0008_job_waiting_input"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_USER_SCOPED_ACTIVE_INGEST_INDEX = """
CREATE UNIQUE INDEX uq_jobs_ingest_active ON jobs (paper_id, user_id)
WHERE paper_id IS NOT NULL
  AND user_id IS NOT NULL
  AND kind = 'ingest'
  AND status IN ('queued', 'running', 'waiting_quota', 'waiting_input')
"""

_GLOBAL_ACTIVE_INGEST_INDEX = """
CREATE UNIQUE INDEX uq_jobs_ingest_active ON jobs (paper_id)
WHERE kind = 'ingest'
  AND status IN ('queued', 'running', 'waiting_quota', 'waiting_input')
"""


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_jobs_ingest_active")
    op.execute(_USER_SCOPED_ACTIVE_INGEST_INDEX)


def downgrade() -> None:
    # The previous schema cannot represent two active owners for one paper.  Cancel every job in
    # those ambiguous groups instead of retaining one that the old API could expose to another
    # user.  Each owner can explicitly retry after the downgrade.
    op.execute(
        "WITH ambiguous_papers AS ("
        "SELECT paper_id FROM jobs "
        "WHERE kind = 'ingest' AND paper_id IS NOT NULL "
        "AND status IN ('queued', 'running', 'waiting_quota', 'waiting_input') "
        "GROUP BY paper_id HAVING count(*) > 1"
        ") UPDATE jobs SET status = 'canceled', "
        "finished_at = COALESCE(finished_at, now()) "
        "WHERE kind = 'ingest' "
        "AND status IN ('queued', 'running', 'waiting_quota', 'waiting_input') "
        "AND paper_id IN (SELECT paper_id FROM ambiguous_papers)"
    )
    op.execute("DROP INDEX IF EXISTS uq_jobs_ingest_active")
    op.execute(_GLOBAL_ACTIVE_INGEST_INDEX)
