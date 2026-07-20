"""Add 'paper_export' to ck_jobs_kind check constraint (Feature S3・Task 11).

Revision ID: 0013_paper_export_job_kind
Revises: 0011_easy_translation_style

論文単位のスタンドアロンエクスポート(``jobs.kind='paper_export'``)を許可する。既存の許可値域を
そのまま保った超集合にする(0010_import_job_kind / 0010_vocab_candidates の同方針)。

Integration note: 実際の alembic head は revision id ``0011_easy_translation_style``
(ファイル名 0012_easy_translation_style.py)。plan の記載(0013 の down_revision)とは異なる
ため、ここでは head に正しく連結する。統合後は base kinds + ``import`` + ``vocab_extract`` を
既に許可しており、その上に ``paper_export`` を union する
(final: base + import + vocab_extract + paper_export)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_paper_export_job_kind"
down_revision: str | None = "0011_easy_translation_style"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ck_jobs_kind に 'paper_export' を追加する(既存値域を保ったまま超集合にする)。
_JOBS_KIND_WITH_PAPER_EXPORT = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'paper_export', 'import', 'account_delete'))"
)
# downgrade 先(親 = 0011_easy_translation_style 適用後の状態: base + import + vocab_extract)。
_JOBS_KIND_ORIGINAL = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'import', 'account_delete'))"
)


def upgrade() -> None:
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_WITH_PAPER_EXPORT)


def downgrade() -> None:
    # 制約を狭める前に 'paper_export' の job 行を除去する(0011/0012 の同方針・可逆性の担保)。
    op.execute("DELETE FROM jobs WHERE kind = 'paper_export'")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_ORIGINAL)
