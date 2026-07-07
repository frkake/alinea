"""取り込みジョブ(``jobs.kind='ingest'``。plans/05 §2・M0-18)。

``ingest_paper(ctx, store, job)`` は :data:`yakudoku_worker.main.HANDLERS` に登録される
arq ハンドラ。8 段階ステートマシンの駆動は :mod:`yakudoku_worker.pipeline` に委譲する。

エラー分類(§2.4): 非リトライ分類(`source_not_found` / `no_text_layer` / `parse_error`)は
その場で ``failed`` 確定にする。リトライ分類は例外を再送出し、``run_job`` の
:meth:`JobStore.fail_with_retry`(指数バックオフ)に委ねる。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from yakudoku_core.arxiv.fetch import FetchError
from yakudoku_core.db.models import Job
from yakudoku_core.ingest import joblog
from yakudoku_core.jobs.store import JobStore

from yakudoku_worker.pipeline import run_ingest

# 非リトライのエラー分類(§2.4)。到達で即 failed。
_NON_RETRYABLE = frozenset({"source_not_found", "no_text_layer", "parse_error"})


async def ingest_paper(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='ingest'`` ハンドラ。段階駆動と非リトライ分類の終端化を行う。"""
    try:
        await run_ingest(ctx, store, job)
    except FetchError as exc:
        error = {"stage": job.stage, "code": exc.kind, "message": str(exc)}
        if exc.kind in _NON_RETRYABLE:
            await joblog.log(
                store.session, job, job.stage, "error", str(exc), detail={"code": exc.kind}
            )
            job.status = "failed"
            job.error = json.dumps(error, ensure_ascii=False)
            job.finished_at = dt.datetime.now(dt.UTC)
            await store.session.commit()
            return
        raise
