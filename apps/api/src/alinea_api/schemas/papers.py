"""papers エンドポイントの DTO(plans/03 §4.2・§4.3)。命名は ``Papers`` 接頭辞。"""

from __future__ import annotations

from pydantic import BaseModel


class PapersReingestResponse(BaseModel):
    """§4.2 POST /api/papers/{paper_id}/reingest の 202 応答。"""

    job_id: str


class PapersIngestLogEntry(BaseModel):
    """§4.3 の処理ログ 1 行(joblog.project_ingest_log の射影と同型)。"""

    at: str | None = None
    stage: str | None = None
    level: str | None = None
    message: str | None = None


class PapersIngestLogResponse(BaseModel):
    """§4.3 GET /api/papers/{paper_id}/ingest-log の 200 応答。"""

    entries: list[PapersIngestLogEntry]
