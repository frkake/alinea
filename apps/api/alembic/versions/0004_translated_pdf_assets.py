"""allow translated and bilingual PDF source assets.

Revision ID: 0004_translated_pdf_assets
Revises: 0003_latex_project_manifest
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op

revision = "0004_translated_pdf_assets"
down_revision = "0003_latex_project_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE source_assets
          DROP CONSTRAINT ck_source_assets_kind,
          ADD CONSTRAINT ck_source_assets_kind CHECK (kind IN
            ('arxiv_latex', 'arxiv_html', 'pdf', 'translated_pdf', 'bilingual_pdf',
             'metadata_api', 'extension_capture', 'latex_project_manifest'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE source_assets
          DROP CONSTRAINT ck_source_assets_kind,
          ADD CONSTRAINT ck_source_assets_kind CHECK (kind IN
            ('arxiv_latex', 'arxiv_html', 'pdf', 'metadata_api',
             'extension_capture', 'latex_project_manifest'))
        """
    )
