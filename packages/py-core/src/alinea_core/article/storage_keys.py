"""記事版スナップショットの S3 キー・Redis キー(plans/07 §4.6)。

apps/worker(書き込み)と apps/api(``GET .../versions`` の読み出し・restore)の両方が同じ
フォーマットを使うため、定数をここに集約する(apps 間で直接 import できないため py-core に置く)。
"""

from __future__ import annotations

ARTICLE_SNAPSHOT_KEY_FMT = "renders/articles/{article_id}/v{version}.json"
ARTICLE_VERSIONS_CACHE_KEY_FMT = "article:versions:{article_id}"


def article_snapshot_key(article_id: str, version: int) -> str:
    return ARTICLE_SNAPSHOT_KEY_FMT.format(article_id=article_id, version=version)


def article_versions_cache_key(article_id: str) -> str:
    return ARTICLE_VERSIONS_CACHE_KEY_FMT.format(article_id=article_id)


__all__ = [
    "ARTICLE_SNAPSHOT_KEY_FMT",
    "ARTICLE_VERSIONS_CACHE_KEY_FMT",
    "article_snapshot_key",
    "article_versions_cache_key",
]
