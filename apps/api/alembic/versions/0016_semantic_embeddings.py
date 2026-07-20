"""pgvector foundation: vector extension + paper/block embedding tables (S12・Task 19).

Revision ID: 0016_semantic_embeddings
Revises: 0013_paper_export_job_kind

セマンティック検索(docs/10 §5 / spec 2026-07-16-semantic-search-design.md §5)の埋め込み
格納基盤。``vector`` 拡張を有効化し、論文粒度 ``paper_embeddings`` とブロック粒度
``block_embeddings`` を新設する(いずれも 1536 次元・HNSW cosine index)。

Integration note(重要): plan の記載は 0017 だが、実際の alembic head は
``0013_paper_export_job_kind``(T15=0014 / T24=0015 が並行で 0013/以前に連なる)。統合時に
controller が全 head を linearize する前提で、本 migration は ``0013_paper_export_job_kind`` に
連結し、revision id は一意で説明的な ``0016_semantic_embeddings`` にする。統合前は複数 head に
なるが問題ない。

pgvector との共存: DB イメージは docker/db/Dockerfile が groonga/pgroonga:4.0.1-debian-16 に
postgresql-16-pgvector を足したもの(PGroonga と pgvector の両拡張を同梱)。既存ボリュームは
初回のみ init.sql が走るため、既存環境ではこの migration の CREATE EXTENSION が実体を投入する。

派生データ: 埋め込みは title/abstract/source_text から再生成できる派生データ。完全バックアップ
(export_user_data)には含めず、インポート後に feature flag が有効なら index_embeddings ジョブで
再生成する(import_user_data 参照)。BYOK 秘密鍵は埋め込みテーブルに一切保存しない。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_semantic_embeddings"
down_revision: str | None = "0013_paper_export_job_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# pgvector を有効化する(PGroonga はそのまま。両拡張が共存する)。
_CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector"

# 論文粒度(D3 第一段)。abstract+title(原文=言語非依存)を 1 ベクトルで埋める。
_CREATE_PAPER_EMBEDDINGS = """
CREATE TABLE paper_embeddings (
    paper_id     UUID        PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    model        TEXT        NOT NULL,
    dim          INT         NOT NULL,
    embedding    vector(1536) NOT NULL,
    source_hash  TEXT        NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# ブロック粒度(D3 第二段)。block_search_index と revision 単位で 1:1。原文 source_text を埋める。
_CREATE_BLOCK_EMBEDDINGS = """
CREATE TABLE block_embeddings (
    revision_id  UUID        NOT NULL REFERENCES document_revisions(id) ON DELETE CASCADE,
    block_id     TEXT        NOT NULL,
    model        TEXT        NOT NULL,
    dim          INT         NOT NULL,
    embedding    vector(1536) NOT NULL,
    source_hash  TEXT        NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (revision_id, block_id)
)
"""

# ck_jobs_kind に 'index_embeddings' を union する(既存値域を保ったまま超集合にする)。
# 0013 適用後の値域(base + import + vocab_extract + paper_export)に index_embeddings を足す。
# 統合時に controller が全 head を linearize するため、ここでは 0013 の値域を基準にする。
_JOBS_KIND_WITH_INDEX = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'paper_export', 'import', 'account_delete', "
    "'index_embeddings'))"
)
_JOBS_KIND_ORIGINAL = (
    "ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind CHECK (kind IN "
    "('ingest', 'translation', 'article', 'figure', 'vocab', 'vocab_extract', "
    "'resource_meta', 'export', 'paper_export', 'import', 'account_delete'))"
)

# HNSW(cosine)index。クエリ時の ANN 近傍探索に使う(embedding <=> :qvec)。
_CREATE_PAPER_HNSW = (
    "CREATE INDEX idx_paper_embeddings_hnsw ON paper_embeddings "
    "USING hnsw (embedding vector_cosine_ops)"
)
_CREATE_BLOCK_HNSW = (
    "CREATE INDEX idx_block_embeddings_hnsw ON block_embeddings "
    "USING hnsw (embedding vector_cosine_ops)"
)
# model 不一致行の除外(混在防止)を効率化する補助 index。
_CREATE_PAPER_MODEL_IDX = "CREATE INDEX ix_paper_embeddings_model ON paper_embeddings (model)"
_CREATE_BLOCK_MODEL_IDX = (
    "CREATE INDEX ix_block_embeddings_model ON block_embeddings (revision_id, model)"
)

# updated_at 自動更新(0001 の set_updated_at() トリガ関数を再利用)。
_PAPER_TRIGGER = (
    "CREATE TRIGGER trg_paper_embeddings_updated_at BEFORE UPDATE ON paper_embeddings "
    "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
)
_BLOCK_TRIGGER = (
    "CREATE TRIGGER trg_block_embeddings_updated_at BEFORE UPDATE ON block_embeddings "
    "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
)


def upgrade() -> None:
    op.execute(_CREATE_EXTENSION)
    op.execute(_CREATE_PAPER_EMBEDDINGS)
    op.execute(_CREATE_BLOCK_EMBEDDINGS)
    op.execute(_CREATE_PAPER_HNSW)
    op.execute(_CREATE_BLOCK_HNSW)
    op.execute(_CREATE_PAPER_MODEL_IDX)
    op.execute(_CREATE_BLOCK_MODEL_IDX)
    op.execute(_PAPER_TRIGGER)
    op.execute(_BLOCK_TRIGGER)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_WITH_INDEX)


def downgrade() -> None:
    # 制約を狭める前に 'index_embeddings' の job 行を除去する(0011/0013 の同方針・可逆性の担保)。
    op.execute("DELETE FROM jobs WHERE kind = 'index_embeddings'")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_kind")
    op.execute(_JOBS_KIND_ORIGINAL)
    # 埋め込みテーブルのみ落とす。vector 拡張は他機能が使う可能性があるため残す
    # (DROP EXTENSION は非可逆的な副作用が大きく、init.sql 側の CREATE と非対称になる)。
    op.execute("DROP TABLE IF EXISTS block_embeddings CASCADE")
    op.execute("DROP TABLE IF EXISTS paper_embeddings CASCADE")
