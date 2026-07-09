"""ジョブ回復コマンド: `python -m alinea_core.jobs.requeue`(plans/01 §4.1)。

Redis が消失しても DB の `status='queued'` 行は残る。本コマンドはそれらを arq(Redis)へ
再 enqueue し、完全復旧させる。arq に渡すのは job_id のみ(ペイロードは DB が保持)。

**逸脱メモ**: 本モジュールは redis/arq を使うが、これらは alinea-core の宣言依存ではない
(共有 venv には api/worker 経由で導入済み)。将来 py-core の deps へ追加するか jobs を別パッケージへ
切り出すのが望ましい。
"""

from __future__ import annotations

import asyncio

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from alinea_core.db.models import Job
from alinea_core.db.session import get_sessionmaker
from alinea_core.settings import get_settings

WORKER_TASK = "run_job"

# kind → 既定キュー(plans/01 §4.3)。
QUEUE_FOR_KIND: dict[str, str] = {
    "ingest": "alinea:bulk",
    "translation": "alinea:bulk",
    "article": "alinea:interactive",
    "figure": "alinea:interactive",
    "vocab": "alinea:interactive",
    "resource_meta": "alinea:interactive",
    "export": "alinea:bulk",
    "account_delete": "alinea:bulk",
}
DEFAULT_QUEUE = "alinea:bulk"


async def requeue_all() -> int:
    """queued な全ジョブを arq へ再投入し、投入件数を返す。"""
    settings = get_settings()
    maker = get_sessionmaker()
    async with maker() as session:
        result = await session.execute(select(Job).where(Job.status == "queued"))
        jobs = list(result.scalars().all())

    if not jobs:
        return 0

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        count = 0
        for job in jobs:
            queue = QUEUE_FOR_KIND.get(job.kind, DEFAULT_QUEUE)
            await pool.enqueue_job(WORKER_TASK, str(job.id), _queue_name=queue)
            count += 1
        return count
    finally:
        await pool.aclose()


def main() -> None:
    count = asyncio.run(requeue_all())
    print(f"requeued {count} job(s)")


if __name__ == "__main__":
    main()
