"""Add publication_comments table (Task 25・公開記事コメントとモデレーション).

Revision ID: 0016_publication_comments
Revises: 0015_article_publications

公開記事(``article_publications``)へのモデレーション付きコメントを保持する
``publication_comments`` を追加する。

- 投稿は認証ユーザーのみ、閲覧は匿名可(権限は API 層)。
- ``status`` = visible | hidden | deleted。hidden は publisher のモデレーション、
  deleted は投稿者本人の soft delete(返信があってもスレッド構造を残すため行は消さない)。
- ``parent_id`` は同一 publication の 1 階層のみ(深いネストは API 層で拒否)。
- ``block_id`` は公開スナップショットに存在するブロックのみ(API 層で検証)。
- 本文は plain text のみ・1〜4000 文字(API 層でサニタイズ・検証)。

Integration note: 統合後の実 alembic head は ``0015_article_publications``(Task 24)。
本タスクはその head へ一意な revision id ``0016_publication_comments`` で連結する。並行タスク
(T17 jats / T19 semantic_embeddings)が同時に別 head を作り得るため複数 head が生じるが、
統合コントローラが merge 時に線形化する(必要なら down_revision を付け替える)。DB が届かない
環境では適用を Task 32(統合後)へ委譲する。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017_publication_comments"
down_revision: str | None = "0016_semantic_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATE_TABLE = """
CREATE TABLE publication_comments (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    publication_id  UUID        NOT NULL REFERENCES article_publications(id) ON DELETE CASCADE,
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id       UUID        REFERENCES publication_comments(id) ON DELETE CASCADE,
    block_id        TEXT        NOT NULL,
    body            TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'visible',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_publication_comments_status
        CHECK (status IN ('visible', 'hidden', 'deleted'))
)
"""

# publication 単位のスレッド読み取り(created_at 昇順)を速くする。
_PUBLICATION_INDEX = (
    "CREATE INDEX ix_publication_comments_publication "
    "ON publication_comments (publication_id, created_at)"
)
_PARENT_INDEX = "CREATE INDEX ix_publication_comments_parent ON publication_comments (parent_id)"
_USER_INDEX = "CREATE INDEX ix_publication_comments_user ON publication_comments (user_id)"
_TRIGGER = (
    "CREATE TRIGGER trg_publication_comments_updated_at BEFORE UPDATE ON publication_comments "
    "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
)


def upgrade() -> None:
    op.execute(_CREATE_TABLE)
    op.execute(_PUBLICATION_INDEX)
    op.execute(_PARENT_INDEX)
    op.execute(_USER_INDEX)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS publication_comments CASCADE")
