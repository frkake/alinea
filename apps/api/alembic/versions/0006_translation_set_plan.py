"""Persist the translation target plan on each translation set.

Revision ID: 0006_translation_set_plan
Revises: 0005_article_variants
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_translation_set_plan"
down_revision: str | None = "0005_article_variants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "translation_sets",
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("translation_sets", "plan")
