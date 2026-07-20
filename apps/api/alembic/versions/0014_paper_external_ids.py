"""External-site paper identifiers (S8 — ACL Anthology / OpenReview / PubMed).

Adds the ``paper_external_ids`` table used by the site-import lane to name-match
imported papers (idempotency) and to preserve identifiers across full backups.
``(site, external_id)`` is unique so one paper can hold several identifiers
(e.g. PubMed PMID + PMC PMCID) while a given site id maps to exactly one paper.

Revision ID: 0014_paper_external_ids
Revises: 0011_easy_translation_style

Integration note: chained onto ``0011_easy_translation_style`` per the task brief.
Another lane adds a separate 0013 migration that is NOT in this base; the merge
controller linearizes the resulting heads. The revision id is unique and stable.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_paper_external_ids"
down_revision: str | None = "0011_easy_translation_style"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATE_TABLE = """
CREATE TABLE paper_external_ids (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id      UUID        NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    site          TEXT        NOT NULL,
    external_id   TEXT        NOT NULL,
    canonical_url TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_paper_external_ids_site_external UNIQUE (site, external_id)
)
"""

_PAPER_INDEX = "CREATE INDEX ix_paper_external_ids_paper ON paper_external_ids (paper_id)"


def upgrade() -> None:
    op.execute(_CREATE_TABLE)
    op.execute(_PAPER_INDEX)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_external_ids CASCADE")
