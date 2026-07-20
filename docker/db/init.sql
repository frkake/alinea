-- docker/db/init.sql(初回起動時に実行)
CREATE EXTENSION IF NOT EXISTS pgroonga;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- セマンティック検索(S12)。pgvector は docker/db/Dockerfile が同梱する
-- postgresql-16-pgvector が提供する。既存ボリュームの環境は Alembic 側の
-- CREATE EXTENSION に依存する(初回のみ init.sql が走るため)。
CREATE EXTENSION IF NOT EXISTS vector;
