"""使用量計測フック(plans/04 §10)。

``LLMRouter`` / ``ImageRouter`` が 1 試行ごとに ``MeterHook.record(UsageDraft)`` を呼ぶ。
``DbMeterHook`` は ``usage_records`` に 1 行 INSERT する(成功=ok+usage+cost、失敗=error+kind)。

``key_source`` の確定(§10.2・§11): M0 の LLMRouter は draft を常に ``key_source='operator'``
で作るため、ここで「その provider にユーザーの有効な BYOK があるか」で ``user`` に補正する。
これによりクォータ集計(operator 行のみ)が正しく BYOK を除外できる(plans/07 §9.2)。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_llm.protocols import UsageDraft

_INSERT_SQL = text(
    "INSERT INTO usage_records ("
    "  user_id, library_item_id, job_id, task, provider, model, key_source,"
    "  input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens,"
    "  image_count, cost_usd, status, attempt, fallback_rank, error_kind,"
    "  latency_ms, request_id"
    ") VALUES ("
    "  CAST(:user_id AS uuid), CAST(:library_item_id AS uuid), CAST(:job_id AS uuid),"
    "  :task, :provider, :model, :key_source,"
    "  :input_tokens, :cached_input_tokens, :cache_write_input_tokens, :output_tokens,"
    "  :image_count, :cost_usd, :status, :attempt, :fallback_rank, :error_kind,"
    "  :latency_ms, :request_id"
    ")"
)


class DbMeterHook:
    """usage_records へ 1 試行 1 行を記録する MeterHook 実装。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        byok_providers: set[str] | None = None,
    ) -> None:
        self._session = session
        self._byok_providers = byok_providers or set()

    def _key_source(self, draft: UsageDraft) -> str:
        # provider にユーザーの有効な BYOK がある場合は 'user'(クォータ非消費)。
        if draft.user_id and draft.provider in self._byok_providers:
            return "user"
        return draft.key_source

    async def record(self, record: UsageDraft) -> None:
        usage = record.usage
        await self._session.execute(
            _INSERT_SQL,
            {
                "user_id": record.user_id,
                "library_item_id": record.library_item_id,
                "job_id": record.job_id,
                "task": record.task,
                "provider": record.provider,
                "model": record.model,
                "key_source": self._key_source(record),
                "input_tokens": usage.input_tokens if usage else 0,
                "cached_input_tokens": usage.cached_input_tokens if usage else 0,
                "cache_write_input_tokens": usage.cache_write_input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "image_count": record.image_count,
                "cost_usd": record.cost_usd,
                "status": record.status,
                "attempt": record.attempt,
                "fallback_rank": record.fallback_rank,
                "error_kind": record.error_kind,
                "latency_ms": record.latency_ms,
                "request_id": record.request_id,
            },
        )


__all__ = ["DbMeterHook"]
