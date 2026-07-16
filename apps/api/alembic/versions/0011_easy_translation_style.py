"""Add 'easy' as a valid translation set style.

Revision ID: 0011_easy_translation_style
Revises: 0010_import_job_kind
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_easy_translation_style"
down_revision: str | None = "0010_import_job_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE translation_sets DROP CONSTRAINT IF EXISTS ck_translation_sets_style")
    op.execute(
        "ALTER TABLE translation_sets ADD CONSTRAINT ck_translation_sets_style "
        "CHECK (style IN ('natural', 'literal', 'easy'))"
    )


def downgrade() -> None:
    # Remove any 'easy' sets before restoring the narrower constraint to avoid failures.
    op.execute("DELETE FROM translation_sets WHERE style = 'easy'")
    op.execute("ALTER TABLE translation_sets DROP CONSTRAINT IF EXISTS ck_translation_sets_style")
    op.execute(
        "ALTER TABLE translation_sets ADD CONSTRAINT ck_translation_sets_style "
        "CHECK (style IN ('natural', 'literal'))"
    )
