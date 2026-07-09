"""structlog による構造化ログ設定(plans/01 §9.3)。

stdout へ 1 行 JSON を出す。request_id・user_id・path はミドルウェアが contextvars で束ねる。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(*, json_logs: bool = True, level: int = logging.INFO) -> None:
    """プロセス起動時に 1 回だけ呼ぶ。多重呼び出しは冪等。"""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "alinea.api") -> Any:
    return structlog.get_logger(name)
