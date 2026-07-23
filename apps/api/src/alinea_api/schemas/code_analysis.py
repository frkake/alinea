"""コード対応解析 API の DTO(Task 21・設計 §10)。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class EstimateRequest(BaseModel):
    resource_id: str
    section_ids: list[str] | None = None


class CodeAnalysisEstimateResponse(BaseModel):
    estimate_id: str
    commit_sha: str
    files: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_embedding_tokens: int
    estimated_cost_usd: Decimal
    budget_remaining_usd: Decimal
    expires_at: str


class StartRequest(BaseModel):
    resource_id: str
    estimate_id: str
    section_ids: list[str] | None = None


class StartResponse(BaseModel):
    job_id: str
    run_id: str
    status: str  # queued | waiting_budget | succeeded(冪等再利用)


class CorrespondenceOut(BaseModel):
    paper_anchor: dict[str, Any] = Field(default_factory=dict)
    claim_text: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    code_excerpt: str
    explanation_ja: str
    confidence: str


class RunOut(BaseModel):
    run_id: str
    resource_id: str
    revision_id: str
    commit_sha: str
    trigger: str
    status: str
    stale: bool
    estimated_cost_usd: Decimal
    actual_cost_usd: Decimal
    error: str | None = None
    created_at: str | None = None
    finished_at: str | None = None


class RunsResponse(BaseModel):
    runs: list[RunOut]
    current_result: RunOut | None = None
    correspondences: list[CorrespondenceOut] = Field(default_factory=list)
    stale: bool = False
