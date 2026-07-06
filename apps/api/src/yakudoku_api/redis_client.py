"""アプリ全体で共有する非同期 Redis 接続。

セッション/レート制限/SSE(Pub/Sub + Stream)で使う。`decode_responses=True` で
文字列を扱う。テストは実 Redis(docker-compose)に対して実行する。
"""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as redis

from yakudoku_api.settings import get_api_settings


@lru_cache
def get_redis() -> redis.Redis:
    settings = get_api_settings()
    client: redis.Redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return client
