"""コード対応解析の月次費用集計と残予算判定(§7)。

``usage_records.task='code_analysis'`` の ``cost_usd`` を当月(JST)で集計する。BYOK・運営キーの
両方を含める(設計 §7「BYOK か運営キーかに関係なく集計」)。api の見積りと worker の予算再検査が
同じ関数を使う。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 当月(Asia/Tokyo)開始以降。llm/deps.py の quota 集計と同じ JST 月境界。
_JST_MONTH_START = (
    "created_at >= (date_trunc('month', now() AT TIME ZONE 'Asia/Tokyo') "
    "AT TIME ZONE 'Asia/Tokyo')"
)

CODE_ANALYSIS_TASK = "code_analysis"


async def month_to_date_cost_usd(session: AsyncSession, user_id: str) -> Decimal:
    """当月の code_analysis 実費(USD)。BYOK+運営の両方、status='ok' 分のみ。"""
    sql = text(
        "SELECT COALESCE(sum(cost_usd), 0) FROM usage_records "  # noqa: S608 (定数のみ・パラメータ束縛)
        "WHERE user_id = CAST(:user_id AS uuid) AND task = :task "
        f"AND status = 'ok' AND {_JST_MONTH_START}"
    )
    value = (
        await session.execute(sql, {"user_id": user_id, "task": CODE_ANALYSIS_TASK})
    ).scalar_one()
    return Decimal(str(value))


async def budget_remaining_usd(
    session: AsyncSession, user_id: str, monthly_budget: Decimal
) -> Decimal:
    """残予算(0 未満は 0 に丸めない — 超過額が分かるよう負値も返す)。"""
    spent = await month_to_date_cost_usd(session, user_id)
    return monthly_budget - spent


async def within_budget(
    session: AsyncSession,
    user_id: str,
    *,
    monthly_budget: Decimal,
    estimated_cost: Decimal,
) -> bool:
    """見積り費用を足しても当月予算内か(予算チェック。設計 §7)。"""
    remaining = await budget_remaining_usd(session, user_id, monthly_budget)
    return estimated_cost <= remaining
