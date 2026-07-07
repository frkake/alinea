"""SRS(間隔反復)スケジュール規則(docs/11 §7.1・plans/03 §11.8)。

**決定**(docs/11 §7.1 逐語): SM-2 系を簡略化した固定段階方式。段階 1〜5・間隔
1/3/7/14/30 日。2 択評価(``again``=まだあやしい / ``good``=✓ 覚えた)のみで、可変難易度係数
(EF)は持たない。

- 保存時: 段階 1・次回復習=翌日(DB ``vocab_entries`` の既定値。0001 初期スキーマ)。
- ``good``: 段階を 1 進め、次回=今日+新段階の間隔。既に段階 5(未習得)なら「通過」として
  習得済み(次回=null・復習キューから除外。一覧には残る)。
- ``again``: 段階 1 にリセット、次回=翌日(習得済みも解除。いつでも段階 1 に戻せる)。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

ReviewResult = Literal["again", "good"]

MIN_STAGE = 1
MAX_STAGE = 5

# 段階 → 次回までの間隔(日)。docs/11 §7.1 の表。
INTERVAL_DAYS: dict[int, int] = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}


@dataclass(frozen=True, slots=True)
class SrsState:
    stage: int
    next_review_on: dt.date | None  # None = 習得済み
    mastered: bool
    review_count: int


def apply_review(
    *,
    stage: int,
    mastered: bool,
    review_count: int,
    result: ReviewResult,
    today: dt.date,
) -> SrsState:
    """1 回の自己評価を適用し、更新後の SRS 状態を返す(docs/11 §7.1)。"""
    new_count = review_count + 1
    if result == "again":
        return SrsState(
            stage=MIN_STAGE,
            next_review_on=today + dt.timedelta(days=1),
            mastered=False,
            review_count=new_count,
        )
    if mastered or stage >= MAX_STAGE:
        # 既に習得済み、または段階 5 を「✓ 覚えた」で通過 → 習得済み(docs/11 §7.1)。
        return SrsState(stage=MAX_STAGE, next_review_on=None, mastered=True, review_count=new_count)
    new_stage = stage + 1
    return SrsState(
        stage=new_stage,
        next_review_on=today + dt.timedelta(days=INTERVAL_DAYS[new_stage]),
        mastered=False,
        review_count=new_count,
    )


def next_review_display(state: SrsState, *, today: dt.date) -> str:
    """「次の復習: 明日(2 回目)」形式の表示文字列(docs/11 §6.3・4d 逐語)。"""
    if state.mastered or state.next_review_on is None:
        return "習得済み"
    delta = (state.next_review_on - today).days
    if delta <= 0:
        relative = "今日"
    elif delta == 1:
        relative = "明日"
    else:
        relative = f"{delta}日後"
    ordinal = state.review_count + 1
    return f"次の復習: {relative}({ordinal} 回目)"


__all__ = [
    "INTERVAL_DAYS",
    "MAX_STAGE",
    "MIN_STAGE",
    "ReviewResult",
    "SrsState",
    "apply_review",
    "next_review_display",
]
