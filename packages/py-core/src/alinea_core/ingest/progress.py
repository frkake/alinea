"""進捗計算と完了検知(plans/05 §2.2・§11.3)。

- 段階ごとの固定進捗マップ(§2.2)と translating_body の連続進捗(訳済ブロック比)。
- `readable_upto`(「§3 まで読めます」)の導出(保存しない導出値)。
- 完了検知(§11.3): set_id 単位で残ジョブ 0 を検出し、翻訳セット + 親 ingest ジョブを
  complete に遷移させる(advisory lock でセット単位に直列化)。
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_core.db.models import Job, TranslationSet
from alinea_core.document.blocks import DocumentContent, Section
from alinea_core.ingest import joblog
from alinea_core.translation.pipeline import compute_translation_scope

# stage → 固定進捗(§2.2)。translating_body は動的(body_progress)。
STAGE_ORDER: tuple[str, ...] = (
    "queued",
    "fetching",
    "parsing",
    "structuring",
    "translating_abstract",
    "readable",
    "translating_body",
    "complete",
)
FIXED_STAGE_PROGRESS: dict[str, int] = {
    "queued": 0,
    "fetching": 10,
    "parsing": 20,
    "structuring": 35,
    "translating_abstract": 50,
    "readable": 55,
    "complete": 100,
}


def stage_index(stage: str) -> int:
    """STAGE_ORDER 内の位置。未知は -1。"""
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1


def body_progress(translated_blocks: int, translatable_blocks: int) -> int:
    """translating_body 中の進捗(§2.2)。floor(100 * 訳済 / 対象)。分母 0 は 100。"""
    if translatable_blocks <= 0:
        return 100
    return min(100, (100 * translated_blocks) // translatable_blocks)


def _section_number_map(content: DocumentContent) -> dict[str, str]:
    out: dict[str, str] = {}

    def walk(sec: Section) -> None:
        out[sec.id] = sec.heading.number
        for sub in sec.sections:
            walk(sub)

    for top in content.sections:
        walk(top)
    return out


def first_translatable_section(content: DocumentContent) -> str | None:
    """参考文献・付録を除く本文セクションの先頭 1 つ(§2.1)。無ければ None。"""
    sections = compute_translation_scope(content).sections
    return sections[0]["section_id"] if sections else None


def readable_upto(content: DocumentContent, translated_block_ids: set[str]) -> str | None:
    """先頭から連続で全訳済みのセクションの最後の節番号を `§{n}` で返す(§2.2)。

    保存しない導出値。translation_units の block_id 集合を渡す。
    """
    scope = compute_translation_scope(content)
    numbers = _section_number_map(content)
    last: str | None = None
    for entry in scope.sections:
        block_ids = entry["block_ids"]
        if block_ids and all(bid in translated_block_ids for bid in block_ids):
            num = numbers.get(entry["section_id"], "")
            if num:
                last = num
        else:
            break
    return f"§{last}" if last else None


# --- 完了検知(§11.3) --------------------------------------------------------------


async def count_active_body_jobs(session: AsyncSession, set_id: str) -> int:
    """当該セットの初回全文翻訳ジョブのうち未完了(queued/running/waiting_quota)の件数。"""
    result = await session.execute(
        text(
            "SELECT count(*) FROM jobs "
            "WHERE kind = 'translation' "
            "AND payload->>'set_id' = :set_id "
            "AND payload->>'reason' = 'initial' "
            "AND status IN ('queued', 'running', 'waiting_quota')"
        ),
        {"set_id": set_id},
    )
    return int(result.scalar_one())


async def finalize_ingest_if_body_complete(
    session: AsyncSession,
    *,
    set_id: str,
    ingest_job_id: str,
    content: DocumentContent,
    style: str,
    source_version: str,
    appendix_untranslated: bool,
) -> bool:
    """残ジョブ 0 なら翻訳セットと親 ingest ジョブを complete にする(§11.3)。

    set_id 単位で advisory lock を取り、初回全文翻訳ジョブが 0 件のときのみ確定させる。
    確定できたら True(通知発火は M1-07 に委譲するためここでは行わない)。
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:s))"), {"s": set_id})
    remaining = await count_active_body_jobs(session, set_id)
    tset = await session.get(TranslationSet, set_id)
    if remaining > 0:
        if tset is not None:
            tset.status = "partial"
        await session.commit()
        return False

    if tset is not None:
        tset.status = "complete"
    ingest_job = await session.get(Job, ingest_job_id)
    if ingest_job is None:
        raise LookupError(f"ingest job not found: {ingest_job_id}")
    ingest_job.stage = "complete"
    ingest_job.status = "succeeded"
    ingest_job.progress = 100
    ingest_job.finished_at = dt.datetime.now(dt.UTC)
    await session.commit()

    await joblog.log(
        session,
        ingest_job,
        "translating_body",
        "info",
        joblog.translation_timeline_message(
            style, source_version, appendix_untranslated=appendix_untranslated
        ),
        detail={"format": "translation", "style": style},
        timeline=True,
    )
    return True
