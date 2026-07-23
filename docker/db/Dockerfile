# Alinea 開発 DB イメージ: PGroonga(全文検索)+ pgvector(セマンティック検索)。
#
# 決定: groonga/pgroonga:4.0.1-debian-16 を base にする。理由: PGroonga は英語ステミング用の
# token_filters/stem.so と tokenizers/mecab.so を同梱する debian 版が必要で(docker-compose.yml
# の db サービスのコメント参照)、alpine 版では PGroonga 索引が作れない(実測)。この base に
# Debian PGDG の postgresql-16-pgvector を足すことで PGroonga を壊さず pgvector(vector 拡張)を
# 追加する。既製の「PGroonga + pgvector」単一イメージは存在しないため自前 build する
# (spec 2026-07-16-semantic-search-design.md §D2 推奨案)。
FROM groonga/pgroonga:4.0.1-debian-16

# postgresql-16-pgvector は Debian の PostgreSQL APT リポジトリ(PGDG)に含まれる。
# groonga/pgroonga:*-debian-16 は既に PGDG apt ソースを構成済み(PostgreSQL 16 を導入する
# ため)なので、そのまま apt-get install できる。pgvector を足しても PGroonga の共有ライブラリ
# (pgroonga.so / pgroonga_wal_resource_manager)は変更されない。
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-16-pgvector; \
    rm -rf /var/lib/apt/lists/*

# extension の実体投入は init.sql(初回起動)/ Alembic(既存ボリューム)で
# `CREATE EXTENSION IF NOT EXISTS vector` を行う。ここではバイナリの同梱のみ。
