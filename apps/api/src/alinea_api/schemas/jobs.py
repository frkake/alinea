"""jobs エンドポイントの DTO(plans/03 §1.7 Job・§21)。"""

from __future__ import annotations

import json
from typing import Any

from alinea_core.db.models import Job
from pydantic import BaseModel


class JobOut(BaseModel):
    id: str
    kind: str
    status: str
    stage: str | None = None
    progress_pct: int
    detail: str | None = None
    error: dict[str, Any] | None = None
    library_item_id: str | None = None
    paper_id: str | None = None
    result: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class JobListResponse(BaseModel):
    items: list[JobOut]


def job_to_out(job: Job) -> JobOut:
    error: dict[str, Any] | None = None
    if job.error:
        try:
            parsed = json.loads(job.error)
            error = parsed if isinstance(parsed, dict) else {"message": job.error}
        except (ValueError, json.JSONDecodeError):
            error = {"message": job.error}
    return JobOut(
        id=str(job.id),
        kind=job.kind,
        status=job.status,
        stage=job.stage,
        progress_pct=job.progress,
        error=error,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        paper_id=str(job.paper_id) if job.paper_id else None,
        result=job.result or None,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )
