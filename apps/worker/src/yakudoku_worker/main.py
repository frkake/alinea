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
from yakudoku_core.ingest import joblog
from yakudoku_core.jobs.store import JobStore

from yakudoku_worker.bootstrap import on_shutdown, on_startup
from yakudoku_worker.settings import BULK_QUEUE, INTERACTIVE_QUEUE, redis_settings

log = structlog.get_logger("yakudoku.worker")

# kind -> ハンドラ。後続タスクが登録する。
JobHandler = Callable[[dict[str, Any], JobStore, Job], Awaitable[None]]
HANDLERS: dict[str, JobHandler] = {}

# LLM ルータを必須とする kind(取り込みはアブストラクト翻訳/要約、翻訳はセクション翻訳で使う)。
# ``ctx['router']`` が未構成(運営キー未設定)なら P3 準拠で可視的に失敗させる(bootstrap 参照)。
_LLM_REQUIRED_KINDS = frozenset({"ingest", "translation"})
_NO_ROUTER_MESSAGE = (
    "LLM プロバイダの API キーが未設定です(運営キー/BYOK いずれも無し)。"
    "設定後に再試行してください。"
)


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
            if job.kind in _LLM_REQUIRED_KINDS and ctx.get("router") is None:
                # 運営キー未設定 → 黙って FakeLLM に落とさず可視的に失敗(P3)。
                await joblog.log(session, job, job.stage, "error", _NO_ROUTER_MESSAGE)
                raise RuntimeError("no LLM provider configured (ctx['router'] is None)")
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
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)


class BulkWorker:
    """一括キュー(取り込み・翻訳・エクスポートなど、長時間ジョブ)。"""

    functions: ClassVar[list[Any]] = [run_job]
    queue_name = BULK_QUEUE
    redis_settings = redis_settings()
    max_jobs = 4
    job_timeout = 1800
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)


# import 時に各 kind のハンドラを HANDLERS へ登録する(ingest / translation)。
# 本モジュール(main)の定義完了後に配線するため末尾で import する(循環 import 回避)。
from yakudoku_worker import tasks as _tasks  # noqa: E402, F401
