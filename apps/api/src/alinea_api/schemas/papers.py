"""papers エンドポイントの DTO(plans/03 §4.2・§4.3)。命名は ``Papers`` 接頭辞。"""

from __future__ import annotations

from pydantic import BaseModel


class PapersReingestResponse(BaseModel):
    """§4.2 POST /api/papers/{paper_id}/reingest の 202 応答。"""

    job_id: str


class FigureMaterializeResponse(BaseModel):
    """未読込(deferred)図のオンデマンド素材化の応答。

    ``job_id`` が None のときは既に素材化済み(何もしない)。素材化を要するときは
    図数上限を引き上げた再取り込みジョブを起こし ``job_id`` を返す。
    """

    job_id: str | None = None
    already_materialized: bool = False
    figure_limit: int | None = None


class FigureMaterializeBatchRequest(BaseModel):
    """未読込図をまとめて素材化する要求(先頭から ``count`` 件を対象に上限拡張)。"""

    count: int = 1


class PapersIngestLogEntry(BaseModel):
    """§4.3 の処理ログ 1 行(joblog.project_ingest_log の射影と同型)。"""

    at: str | None = None
    stage: str | None = None
    level: str | None = None
    message: str | None = None


class PapersIngestLogResponse(BaseModel):
    """§4.3 GET /api/papers/{paper_id}/ingest-log の 200 応答。"""

    entries: list[PapersIngestLogEntry]
