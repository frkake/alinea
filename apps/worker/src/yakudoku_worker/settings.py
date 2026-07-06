"""arq ワーカー共通設定(キュー名・Redis 接続)。plans/01 §2.3・§4。"""

from __future__ import annotations

from arq.connections import RedisSettings
from yakudoku_core.settings import get_settings

# キュー名(plans/01 §4.3)。interactive=対話的優先、bulk=一括処理。
INTERACTIVE_QUEUE = "yk:interactive"
BULK_QUEUE = "yk:bulk"


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)
