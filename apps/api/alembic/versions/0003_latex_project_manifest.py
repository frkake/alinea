"""allow LaTeX project manifest source assets.

Revision ID: 0003_latex_project_manifest
Revises: 0002_llm_routing
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op

revision = "0003_latex_project_manifest"
down_revision = "0002_llm_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE source_assets
          DROP CONSTRAINT ck_source_assets_kind,
          ADD CONSTRAINT ck_source_assets_kind CHECK (kind IN
            ('arxiv_latex', 'arxiv_html', 'pdf', 'metadata_api',
             'extension_capture', 'latex_project_manifest'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE source_assets
          DROP CONSTRAINT ck_source_assets_kind,
          ADD CONSTRAINT ck_source_assets_kind CHECK (kind IN
            ('arxiv_latex', 'arxiv_html', 'pdf', 'metadata_api', 'extension_capture'))
        """
    )
