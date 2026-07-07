"""arq ワーカー本体: `InteractiveWorker` / `BulkWorker`。

`run_job(ctx, job_id)` は「起床通知」を受けて DB からジョブを claim し、kind ごとのハンドラへ
ディスパッチする(plans/01 §4.1)。各 kind の具体ハンドラは後続マイルストーン(取り込み・翻訳・
記事・図・語彙・エクスポート・アカウント削除)で `HANDLERS` に登録する。M0 段階では基盤のみ。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import structlog
from yakudoku_core.db.models import Job
from yakudoku_core.db.session import get_sessionmaker
from yakudoku_core.jobs.store import JobStore

from yakudoku_worker.settings import BULK_QUEUE, INTERACTIVE_QUEUE, redis_settings

log = structlog.get_logger("yakudoku.worker")

# kind -> ハンドラ。後続タスクが登録する。
JobHandler = Callable[[dict[str, Any], JobStore, Job], Awaitable[None]]
HANDLERS: dict[str, JobHandler] = {}


async def run_job(ctx: dict[str, Any], job_id: str) -> None:
    """起床通知。claim できたジョブのみ、登録済みハンドラへ渡す。"""
    maker = get_sessionmaker()
    async with maker() as session:
        store = JobStore(session)
        job = await store.claim(job_id)
        if job is None:
            # 既に他ワーカーが処理済み or 未 queued(二重起床)。何もしない。
            await log.adebug("job_not_claimable", job_id=job_id)
            return
        handler = HANDLERS.get(job.kind)
        if handler is None:
            await log.awarning("no_handler_for_kind", kind=job.kind, job_id=job_id)
            return
        try:
            await handler(ctx, store, job)
        except Exception as exc:
            retrying = await store.fail_with_retry(
                job_id, {"stage": job.stage, "message": str(exc)}
            )
            await log.aerror("job_failed", job_id=job_id, retrying=retrying)


class InteractiveWorker:
    """対話的優先キュー(記事・図・語彙・再翻訳など、ユーザー待ちの短いジョブ)。"""

    functions: ClassVar[list[Any]] = [run_job]
    queue_name = INTERACTIVE_QUEUE
    redis_settings = redis_settings()
    max_jobs = 20
    job_timeout = 300


class BulkWorker:
    """一括キュー(取り込み・翻訳・エクスポートなど、長時間ジョブ)。"""

    functions: ClassVar[list[Any]] = [run_job]
    queue_name = BULK_QUEUE
    redis_settings = redis_settings()
    max_jobs = 4
    job_timeout = 1800


# import 時に各 kind のハンドラを HANDLERS へ登録する(ingest / translation)。
# 本モジュール(main)の定義完了後に配線するため末尾で import する(循環 import 回避)。
from yakudoku_worker import tasks as _tasks  # noqa: E402, F401
