"""stale(古い結果)判定(§7・§9・brief Step 9)。

論文 revision または GitHub default branch commit が変わったら、以前の成功 run を **削除せず
stale=true にする**(再解析は設定モードに従う)。ここは純粋な UPDATE ヘルパで、api(estimate/
start 時の新 commit)と worker(ingest の新 revision)の両方から呼ぶ。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def mark_runs_stale_for_new_revision(
    session: AsyncSession, *, user_id: str, library_item_id: str, current_revision_id: str
) -> int:
    """新 revision に伴い、当該 (user, library_item) の別 revision の成功 run を stale にする。

    resource は問わない(論文本文が変われば全対応が古くなる)。current_revision_id 自体の run は
    残す(まだ最新基盤)。stale/canceled/failed/waiting_budget は対象外(succeeded のみ)。
    戻り値は stale 化した行数。
    """
    result = await session.execute(
        text(
            "UPDATE code_analysis_runs SET stale = true, updated_at = now() "
            "WHERE user_id = CAST(:u AS uuid) "
            "AND library_item_id = CAST(:li AS uuid) "
            "AND revision_id <> CAST(:rev AS uuid) "
            "AND status = 'succeeded' AND stale = false"
        ),
        {"u": user_id, "li": library_item_id, "rev": current_revision_id},
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def mark_runs_stale_for_new_commit(
    session: AsyncSession, *, user_id: str, resource_id: str, current_commit_sha: str
) -> int:
    """新 default branch commit に伴い、当該 (user, resource) の別 commit の成功 run を stale 化。

    リポジトリが更新された場合、以前の commit に固定された対応は古い。current_commit_sha の run は
    残す。戻り値は stale 化した行数。
    """
    result = await session.execute(
        text(
            "UPDATE code_analysis_runs SET stale = true, updated_at = now() "
            "WHERE user_id = CAST(:u AS uuid) "
            "AND resource_id = CAST(:res AS uuid) "
            "AND commit_sha <> :sha "
            "AND status = 'succeeded' AND stale = false"
        ),
        {"u": user_id, "res": resource_id, "sha": current_commit_sha},
    )
    return int(getattr(result, "rowcount", 0) or 0)
