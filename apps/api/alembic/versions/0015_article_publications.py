"""Add article_publications table (Task 24・記事公開のデータモデル).

Revision ID: 0015_article_publications
Revises: 0013_paper_export_job_kind

生成記事のサニタイズ済み公開スナップショットを保持する ``article_publications`` を追加する。

Integration note: plan は 0019 を指示するが、統合後の実 alembic head は
``0013_paper_export_job_kind``(ファイル 0013)。ここではその head へ正しく連結する
(revision id は一意な ``0015_article_publications``)。並行タスク T15 が 0011 起点で
``0014_paper_external_ids`` を作るため複数 head が生じ得るが、統合コントローラが線形化する
(必要なら down_revision を merge 時に付け替える)。DB 適用は Task 32(統合後)に委譲する。

制約:
- UNIQUE(article_id): 1 記事につき公開は 1 つ。
- UNIQUE(slug): slug は全体で一意。公開解除後も行を残し slug を予約する
  (visibility='private')ためリンクの乗っ取りを防ぐ。
- ck_article_publications_visibility: unlisted | public | private のみ許可。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_article_publications"
down_revision: str | None = "0013_paper_export_job_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATE_TABLE = """
CREATE TABLE article_publications (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id        UUID        NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    user_id           UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slug              TEXT        NOT NULL,
    visibility        TEXT        NOT NULL DEFAULT 'unlisted',
    snapshot_version  INT         NOT NULL DEFAULT 1,
    title             TEXT        NOT NULL DEFAULT '',
    paper_meta        JSONB       NOT NULL DEFAULT '{}',
    blocks            JSONB       NOT NULL DEFAULT '[]',
    published_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_article_publications_article UNIQUE (article_id),
    CONSTRAINT uq_article_publications_slug UNIQUE (slug),
    CONSTRAINT ck_article_publications_visibility
        CHECK (visibility IN ('unlisted', 'public', 'private'))
)
"""

# 公開中(unlisted/public)の slug 読み取りを速くする部分インデックス。
_ACTIVE_SLUG_INDEX = (
    "CREATE INDEX ix_article_publications_active_slug ON article_publications (slug) "
    "WHERE visibility IN ('unlisted', 'public')"
)
_USER_INDEX = "CREATE INDEX ix_article_publications_user ON article_publications (user_id)"
_TRIGGER = (
    "CREATE TRIGGER trg_article_publications_updated_at BEFORE UPDATE ON article_publications "
    "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
)


def upgrade() -> None:
    op.execute(_CREATE_TABLE)
    op.execute(_ACTIVE_SLUG_INDEX)
    op.execute(_USER_INDEX)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS article_publications CASCADE")
