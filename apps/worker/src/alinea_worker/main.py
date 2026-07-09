"""arq ワーカー本体: `InteractiveWorker` / `BulkWorker`。

`run_job(ctx, job_id)` は「起床通知」を受けて DB からジョブを claim し、kind ごとのハンドラへ
ディスパッチする(plans/01 §4.1)。各 kind の具体ハンドラは後続マイルストーン(取り込み・翻訳・
記事・図・語彙・エクスポート・アカウント削除)で `HANDLERS` に登録する。M0 段階では基盤のみ。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import structlog
from alinea_core.db.models import Job
from alinea_core.db.session import get_sessionmaker
from alinea_core.ingest import joblog
from alinea_core.jobs.store import JobStore
from arq import cron

from alinea_worker.bootstrap import on_shutdown, on_startup
from alinea_worker.cron import check_quality_promotions, send_deadline_reminders
from alinea_worker.settings import BULK_QUEUE, INTERACTIVE_QUEUE, redis_settings

log = structlog.get_logger("alinea.worker")

# kind -> ハンドラ。後続タスクが登録する。
JobHandler = Callable[[dict[str, Any], JobStore, Job], Awaitable[None]]
HANDLERS: dict[str, JobHandler] = {}

# LLM ルータを必須とする kind(取り込みはアブストラクト翻訳/要約、翻訳はセクション翻訳、
# article は記事生成・ブロック書き直しで使う)。
# ``ctx['router']`` が未構成(運営キー未設定)なら P3 準拠で可視的に失敗させる(bootstrap 参照)。
_LLM_REQUIRED_KINDS = frozenset({"ingest", "translation", "article", "figure"})
_NO_ROUTER_MESSAGE = (
    "LLM プロバイダの API キーが未設定です(運営キー/BYOK いずれも無し)。"
    "設定後に再試行してください。"
)
_JOB_QUEUE_BY_KIND = {
    "article": INTERACTIVE_QUEUE,
    "figure": INTERACTIVE_QUEUE,
    "vocab": INTERACTIVE_QUEUE,
    "resource_meta": INTERACTIVE_QUEUE,
    "ingest": BULK_QUEUE,
    "translation": BULK_QUEUE,
    "export": BULK_QUEUE,
    "account_delete": BULK_QUEUE,
}


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
        except asyncio.CancelledError:
            try:
                retrying = await store.fail_with_retry(
                    job_id,
                    {
                        "stage": job.stage,
                        "message": "job cancelled or timed out; queued for retry",
                    },
                )
            except LookupError:
                await log.ainfo("job_gone_after_cancel", job_id=job_id)
            else:
                if retrying:
                    await _schedule_retry(ctx, store, job_id)
                await _notify_job_updated(ctx, job)
                await log.ainfo(
                    "job_cancelled_for_retry",
                    job_id=job_id,
                    stage=job.stage,
                    retrying=retrying,
                )
            raise
        except Exception as exc:
            try:
                retrying = await store.fail_with_retry(
                    job_id, {"stage": job.stage, "message": str(exc)}
                )
            except LookupError:
                # ユーザーが取り込みをキャンセル(=ライブラリ項目削除。§cancel-ingest)した後に
                # running だったジョブが書き込みへ失敗したケース。job 行は既に無いので
                # リトライ記録は不要(P3: エラーではなく想定内の中断)。
                await log.ainfo("job_gone_after_cancel", job_id=job_id)
                return
            if retrying:
                await _schedule_retry(ctx, store, job_id)
            await log.aerror("job_failed", job_id=job_id, retrying=retrying)
        # SSE 起床通知(plans/03 §21.2)。translate.py/pipeline.py は自前で `ctx['publish']` に
        # 詳細な進捗を発行するが、article/figure/vocab/resource_meta/export 等は
        # ``JobStore.checkpoint``/``succeed`` で DB のみ更新するため、ハンドラ完了(成功・失敗
        # いずれも)ごとにここで 1 回起床通知する。`/api/jobs/{job_id}/events` は起床通知の
        # 受信ごとに DB の最新状態を読み直して done/error/progress を配信するため、これが無いと
        # ジョブが SSE 接続確立より先に完了した場合に限り正常応答するという競合状態になり、
        # フロントエンドが進捗 0% のまま無限に待つ(M2-17 で PW-13 実行時に発見。deviations 参照)。
        await _notify_job_updated(ctx, job)


async def _notify_job_updated(ctx: dict[str, Any], job: Job) -> None:
    publish = ctx.get("publish")
    if publish is None or job.user_id is None:
        return
    try:
        await publish(
            {
                "type": "job.updated",
                "job_id": str(job.id),
                "user_id": str(job.user_id),
                "library_item_id": str(job.library_item_id) if job.library_item_id else None,
            }
        )
    except Exception as exc:  # SSE 起床通知は best-effort(ジョブ本体は既に確定済み)。
        await log.awarning("job_updated_notify_failed", job_id=str(job.id), error=str(exc))


async def _schedule_retry(ctx: dict[str, Any], store: JobStore, job_id: str) -> None:
    """DB retry 状態に合わせて arq へ次回 run_job を遅延投入する。"""
    arq_pool = ctx.get("arq_pool")
    if arq_pool is None:
        return
    job = await store.get(job_id)
    if job is None:
        return
    queue_name = _JOB_QUEUE_BY_KIND.get(job.kind, BULK_QUEUE)
    try:
        await arq_pool.enqueue_job(
            "run_job",
            job_id,
            _queue_name=queue_name,
            _defer_until=job.next_retry_at,
        )
    except Exception as exc:
        await log.awarning(
            "job_retry_schedule_failed",
            job_id=job_id,
            queue_name=queue_name,
            error=str(exc),
        )


class InteractiveWorker:
    """対話的優先キュー(記事・図・語彙・再翻訳など、ユーザー待ちの短いジョブ)。"""

    functions: ClassVar[list[Any]] = [run_job]
    queue_name = INTERACTIVE_QUEUE
    redis_settings = redis_settings()
    max_jobs = 20
    job_timeout = 420
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)


class BulkWorker:
    """一括キュー(取り込み・翻訳・エクスポートなど、長時間ジョブ)。"""

    functions: ClassVar[list[Any]] = [run_job]
    # check_quality_promotions: 毎日 07:30 JST(= 22:30 UTC 前日。plans/05 §12.3)。
    # send_deadline_reminders: 毎日 08:00 JST(= 23:00 UTC 前日。plans/01 §4.3・M2-09)。
    cron_jobs: ClassVar[list[Any]] = [
        cron(check_quality_promotions, hour={22}, minute={30}),
        cron(send_deadline_reminders, hour={23}, minute={0}),
    ]
    queue_name = BULK_QUEUE
    redis_settings = redis_settings()
    max_jobs = 4
    job_timeout = 1800
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)


# import 時に各 kind のハンドラを HANDLERS へ登録する(ingest / translation)。
# 本モジュール(main)の定義完了後に配線するため末尾で import する(循環 import 回避)。
from alinea_worker import tasks as _tasks  # noqa: E402, F401
from alinea_worker.tasks.generate_article import run_article_job  # noqa: E402

# kind='article'(generate/regenerate/block_rewrite を payload.op で振り分ける。plans/07 §4)。
HANDLERS["article"] = run_article_job
