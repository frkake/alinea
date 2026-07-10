"""Store one generated article per audience preset.

Revision ID: 0005_article_variants
Revises: 0004_translated_pdf_assets
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_article_variants"
down_revision: str | None = "0004_translated_pdf_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_articles_library_item", "articles", type_="unique")
    op.create_unique_constraint(
        "uq_articles_library_item_preset",
        "articles",
        ["library_item_id", "preset"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_articles_library_item_preset", "articles", type_="unique")
    op.create_unique_constraint("uq_articles_library_item", "articles", ["library_item_id"])
