"""arq ワーカー共通設定(キュー名・Redis 接続)。plans/01 §2.3・§4。"""

from __future__ import annotations

import os

from alinea_core.settings import get_settings
from arq.connections import RedisSettings

# キュー名(plans/01 §4.3)。interactive=対話的優先、bulk=一括処理。
INTERACTIVE_QUEUE = "alinea:interactive"
BULK_QUEUE = "alinea:bulk"


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)
