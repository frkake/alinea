"""JobStore: PostgreSQL `jobs` テーブルを唯一の真実として扱うジョブ実行モデル(plans/01 §4)。

責務:
- `enqueue`: 冪等性キーで重複作成を防ぐ(同一キー再投入は既存 job を返す)。
- `claim`: `status='queued'` の行のみを排他的に `running` へ遷移(二重起床・二重 enqueue 対策)。
- `checkpoint`: 完了 stage の出力参照を記録し `stage` を進める。リトライ時に途中再開する。
- `fail_with_retry`: 指数バックオフ 30s→2min→8min(自動 3 回)。以後 `failed`。
- `record_partial_failure`: 部分失敗を記録し、ジョブ全体は失敗させない(docs/09 §2)。

**逸脱メモ**: plans/02 の `jobs` DDL には `checkpoint` 列が無い(plans/01 §4.2 の追補予定列)。
alembic は変更禁止のため、checkpoint は `payload["_checkpoint"]`(JSONB)に格納する。`result` は
SSE `done` で外部露出する(plans/03 §21.2)ため使わない。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakudoku_core.db.models import Job

# 優先度(DDL は INT。ix_jobs_pick は priority DESC でピックする)。
_PRIORITY_MAP = {"interactive": 10, "bulk": 0}

# 指数バックオフ(秒)。attempt 回目失敗後の待機。plans/01 §4.5 / docs/09 §2。
_BACKOFF_SECONDS: dict[int, int] = {1: 30, 2: 120, 3: 480}
_DEFAULT_MAX_ATTEMPTS = 4  # 初回 + 自動リトライ 3 回

_CHECKPOINT_KEY = "_checkpoint"


class JobStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        priority: str | int = "bulk",
        user_id: str | None = None,
        paper_id: str | None = None,
        library_item_id: str | None = None,
        article_id: str | None = None,
        stage: str = "queued",
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> str:
        """ジョブを作成し job_id を返す。冪等性キーが既存なら既存 job_id を返す。"""
        if idempotency_key is not None:
            existing = await self._find_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        priority_value = (
            _PRIORITY_MAP.get(priority, 0) if isinstance(priority, str) else int(priority)
        )
        job = Job(
            kind=kind,
            stage=stage,
            status="queued",
            progress=0,
            priority=priority_value,
            payload=payload or {},
            idempotency_key=idempotency_key,
            user_id=user_id,
            paper_id=paper_id,
            library_item_id=library_item_id,
            article_id=article_id,
            max_attempts=max_attempts,
        )
        self.session.add(job)
        try:
            await self.session.commit()
        except IntegrityError:
            # 競合で一意制約(idempotency_key / active ingest)に当たった → 既存を返す。
            await self.session.rollback()
            if idempotency_key is not None:
                existing = await self._find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise
        return str(job.id)

    async def _find_by_idempotency_key(self, idempotency_key: str) -> str | None:
        result = await self.session.execute(
            select(Job.id).where(Job.idempotency_key == idempotency_key)
        )
        row = result.scalar_one_or_none()
        return str(row) if row is not None else None

    async def get(self, job_id: str) -> Job | None:
        self.session.expire_all()
        return await self.session.get(Job, job_id)

    async def claim(self, job_id: str) -> Job | None:
        """`status='queued'` のときのみ `running` へ遷移し attempt を +1。0 行なら None。"""
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="running", started_at=func.now(), attempt=Job.attempt + 1)
            .returning(Job.id)
        )
        result = await self.session.execute(stmt)
        claimed = result.scalar_one_or_none()
        await self.session.commit()
        if claimed is None:
            return None
        self.session.expire_all()
        return await self.session.get(Job, job_id)

    async def checkpoint(
        self,
        job_id: str,
        stage: str,
        data: dict[str, Any] | None = None,
        *,
        progress: int | None = None,
    ) -> None:
        """stage 完了を記録して stage を進める(段階再開の基点)。"""
        job = await self._require(job_id)
        checkpoint = dict(job.payload.get(_CHECKPOINT_KEY, {}))
        checkpoint[stage] = data if data is not None else {}
        job.payload = {**job.payload, _CHECKPOINT_KEY: checkpoint}
        job.stage = stage
        if progress is not None:
            job.progress = max(0, min(100, progress))
        await self.session.commit()

    @staticmethod
    def get_checkpoint(job: Job) -> dict[str, Any]:
        value = job.payload.get(_CHECKPOINT_KEY, {})
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def has_stage_completed(cls, job: Job, stage: str) -> bool:
        return stage in cls.get_checkpoint(job)

    async def set_progress(self, job_id: str, progress: int, *, stage: str | None = None) -> None:
        job = await self._require(job_id)
        job.progress = max(0, min(100, progress))
        if stage is not None:
            job.stage = stage
        await self.session.commit()

    async def mark_waiting_quota(self, job_id: str) -> None:
        job = await self._require(job_id)
        job.status = "waiting_quota"
        await self.session.commit()

    async def succeed(self, job_id: str, result: dict[str, Any] | None = None) -> None:
        job = await self._require(job_id)
        job.status = "succeeded"
        job.progress = 100
        job.result = result or {}
        job.finished_at = dt.datetime.now(dt.UTC)
        await self.session.commit()

    async def record_partial_failure(self, job_id: str, stage: str, error: dict[str, Any]) -> None:
        """部分失敗(例: 図抽出失敗)を log に残すが status は変えない(部分成功は正の状態)。"""
        job = await self._require(job_id)
        entry = {"stage": stage, "level": "partial_failure", "error": error}
        job.log = [*job.log, entry]
        await self.session.commit()

    async def fail_with_retry(self, job_id: str, error: dict[str, Any]) -> bool:
        """失敗を記録。リトライ余地があれば queued + next_retry_at を設定し True。

        自動リトライを尽くしたら failed にして False を返す。
        """
        job = await self._require(job_id)
        job.error = json.dumps(error, ensure_ascii=False)
        job.log = [*job.log, {"level": "error", "error": error}]
        if job.attempt >= job.max_attempts:
            job.status = "failed"
            job.finished_at = dt.datetime.now(dt.UTC)
            await self.session.commit()
            return False
        backoff = _BACKOFF_SECONDS.get(job.attempt, _BACKOFF_SECONDS[max(_BACKOFF_SECONDS)])
        job.status = "queued"
        job.next_retry_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=backoff)
        await self.session.commit()
        return True

    async def _require(self, job_id: str) -> Job:
        self.session.expire_all()
        job = await self.session.get(Job, job_id)
        if job is None:
            raise LookupError(f"job not found: {job_id}")
        return job
