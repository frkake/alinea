"""dashboard エンドポイントの DTO(plans/03 §5.12・§5.7)。

- ``GET /api/dashboard`` の応答(``DashboardResponse``)と ``PUT /api/library-items/queue-order``
  の入出力を持つ。queue-order は経路上 library-items ルータに実装するが、スキーマは本タスクの
  所有ファイルにまとめる(schemas/library.py は他タスク所有のため編集しない)。
- ``continue_reading`` / ``up_next_queue`` / ``recent.items`` の要素は ``LibraryItemSummary``
  (schemas/common.py・plans/03 §1.7)を再利用する。締切(§5.12 の ``deadlines``)は M2-09 まで
  レスポンス形のみ(常に空配列)。
"""

from __future__ import annotations

from pydantic import BaseModel

from alinea_api.schemas.common import LibraryItemSummary


class DeadlineCollectionEntry(BaseModel):
    """§5.12 ``deadlines.collections`` の要素(M2-09 まで生成されない)。"""

    id: str
    name: str
    deadline: str
    days_left: int
    done_count: int
    total_count: int


class DeadlineItemEntry(BaseModel):
    """§5.12 ``deadlines.items`` の要素(M2-09 まで生成されない)。"""

    library_item_id: str
    title: str
    deadline: str
    assignee_self: bool
    status: str


class DeadlinesSection(BaseModel):
    collections: list[DeadlineCollectionEntry]
    items: list[DeadlineItemEntry]


class RecentSection(BaseModel):
    """§5.12 ``recent``。今週追加、最大 6 件(docs/06 §6.4)。"""

    week_count: int
    items: list[LibraryItemSummary]


class StatsWeek(BaseModel):
    finished_count: int
    reading_hours: float


class StatsSection(BaseModel):
    """§5.12 ``stats``。直近 12 週(古→新)の読書時間棒グラフ+今週の読了本数(docs/06 §6.5)。"""

    week: StatsWeek
    weekly_hours: list[float]


class DashboardResponse(BaseModel):
    """§5.12 ``GET /api/dashboard`` の 200 応答。"""

    continue_reading: list[LibraryItemSummary]
    up_next_queue: list[LibraryItemSummary]
    deadlines: DeadlinesSection
    recent: RecentSection
    stats: StatsSection


class QueueOrderRequest(BaseModel):
    """§5.7 ``PUT /api/library-items/queue-order`` のリクエスト本文。"""

    library_item_ids: list[str]


class QueueOrderResponse(BaseModel):
    """§5.7 の 200 応答。"""

    ok: bool = True
