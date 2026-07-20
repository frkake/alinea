"""Allow ``jats`` as a document_revisions.source_format (Task 17 — PMC JATS 品質 A).

PMC の Open Access 記事は JATS XML から品質 A の DocumentContent へ構造化される。既存の
``ck_document_revisions_format`` は ('latex', 'arxiv_html', 'pdf') のみを許すため、``jats`` を
値域へ追加する(superset — 既存値はすべて維持)。

Revision ID: 0016_jats_source_format
Revises: 0015_article_publications

Integration note: 現行 head の 0015_article_publications へチェーンする。並行タスク
(T19 / T25)も 0016 番の別マイグレーションを作るため複数 head が生じるが、マージコント
ローラがリニアライズする。本 revision id は一意で説明的。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019_jats_source_format"
down_revision: str | None = "0018_presentation_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED_WITH_JATS = "('latex', 'arxiv_html', 'pdf', 'jats')"
_ALLOWED_WITHOUT_JATS = "('latex', 'arxiv_html', 'pdf')"


def _replace_format_check(allowed: str) -> None:
    op.execute("ALTER TABLE document_revisions DROP CONSTRAINT IF EXISTS ck_document_revisions_format")
    op.execute(
        "ALTER TABLE document_revisions "
        "ADD CONSTRAINT ck_document_revisions_format "
        f"CHECK (source_format IN {allowed})"
    )


def upgrade() -> None:
    _replace_format_check(_ALLOWED_WITH_JATS)


def downgrade() -> None:
    # jats を使う既存リビジョンがあると CHECK 追加に失敗するため、先に pdf へ寄せてから戻す。
    op.execute("UPDATE document_revisions SET source_format = 'pdf' WHERE source_format = 'jats'")
    _replace_format_check(_ALLOWED_WITHOUT_JATS)
