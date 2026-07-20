"""プレゼンテーション(論文→PPTX)API の DTO(Task 28)。

- ``PresentationGenerateRequest``: preset(3 種)+ 任意 audience 上書き + 任意指示(≤500 文字)。
- ``PresentationArtifactOut``: 最新成果物の metadata(平文 storage key は露出しない)。
- ``PresentationStatusResponse``: 最新成果物 + 進行中 job(GET)。
- ``PresentationJobResponse``: POST の 202 レスポンス(job_id)。

実際の PPTX 生成は worker(Task 29)。本ルータは job 作成までを担う。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from alinea_api.schemas.jobs import JobOut

# 3 preset(plans/13 M2 プレゼンテーション。preset ごとに既定 audience を持つ)。
Preset = Literal["reading_group", "research_talk", "implementation"]

# preset → 既定 audience(ユーザーが body.audience で上書き可能)。
PRESET_DEFAULT_AUDIENCE: dict[str, str] = {
    "reading_group": "students",
    "research_talk": "researchers",
    "implementation": "practitioners",
}

INSTRUCTION_MAX_LEN = 500


class PresentationGenerateRequest(BaseModel):
    preset: Preset
    audience: str | None = Field(default=None, max_length=100)
    instruction: str | None = Field(default=None, max_length=INSTRUCTION_MAX_LEN)


class PresentationArtifactOut(BaseModel):
    id: str
    library_item_id: str
    source_revision_id: str
    generation_job_id: str | None = None
    preset: str
    audience: str
    instruction: str
    model_provider: str
    model_id: str
    ppt_master_revision: str
    generated_at: str
    updated_at: str
    # 平文 storage key は返さない(download エンドポイント経由でのみ配布)。


class PresentationStatusResponse(BaseModel):
    artifact: PresentationArtifactOut | None = None
    job: JobOut | None = None


class PresentationJobResponse(BaseModel):
    job_id: str
