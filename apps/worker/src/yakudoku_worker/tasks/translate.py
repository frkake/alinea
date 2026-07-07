"""翻訳ジョブ(plans/06 §3.1・§13)。

``jobs.kind = 'translation'`` の全用途は ``payload.reason`` で判別する(plans/06 §3.1)。
M0-17 では ``translate_section`` 系(``initial`` / ``literal`` / ``on_demand`` / ``table``)を
担当する。再翻訳系(``retranslate`` / ``instructed`` / ``glossary_change``)は M1-15。

進捗は :class:`~yakudoku_core.jobs.store.JobStore` 経由で ``jobs`` テーブルに反映し(SSE は
jobs を読む)、``ctx['publish']`` があれば ``translation.unit_completed`` 相当を発行する。
LLMRouter は ``ctx['router']`` から注入する(apps 間 import を避けるための DI)。
"""

from __future__ import annotations

from typing import Any

from yakudoku_core.db.models import Job
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.translation.pipeline import translate_section

# translate_section が担当する reason(plans/06 §3.1)。
_SECTION_REASONS = frozenset({"initial", "literal", "on_demand", "table"})


async def run_translation_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='translation'`` のディスパッチャ。reason で処理を振り分ける。"""
    payload = job.payload or {}
    reason = str(payload.get("reason", "initial"))
    if reason not in _SECTION_REASONS:
        # retranslate / instructed / glossary_change は M1-15。
        raise NotImplementedError(f"translation reason not supported in M0: {reason}")

    router = ctx["router"]
    publish = ctx.get("publish")
    result = await translate_section(
        store.session,
        str(payload["set_id"]),
        str(payload["section_id"]),
        router,
        block_ids=payload.get("block_ids"),
        reason=reason,
        instruction=str(payload.get("instruction", "")),
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
        job_store=store,
        publish=publish,
    )
    await store.succeed(
        str(job.id),
        {
            "section_id": result.section_id,
            "translated": result.translated,
            "fallback": result.fallback,
            "skipped": result.skipped,
            "set_status": result.set_status,
            "progress_pct": result.progress_pct,
        },
    )
