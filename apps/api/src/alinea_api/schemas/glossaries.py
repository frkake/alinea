"""glossaries エンドポイントの DTO(plans/03 §7.9)。

用語集(訳語統一の内部機構)の 3 層(global/user/paper)CRUD・訳語変更・promote。
語彙帳(英語学習・plans/03 §11)とは別物。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Scope = Literal["global", "user", "paper"]
Policy = Literal["translate", "keep_original", "both"]


class GlossaryTermItem(BaseModel):
    """plans/03 §7.9 GlossaryTerm。"""

    id: str
    scope: Scope
    library_item_id: str | None  # scope=paper のみ
    source_term: str
    target_term: str
    pos_label: str | None
    policy: Policy
    auto_extracted: bool


class GlossaryTermsListResponse(BaseModel):
    items: list[GlossaryTermItem]


class GlossaryTermCreateRequest(BaseModel):
    scope: Scope  # global 指定時はルータが 403(plans/03 §7.9「scope=global への書き込みは403」)
    library_item_id: str | None = None
    source_term: str
    target_term: str
    policy: Policy


class GlossaryTermPatchRequest(BaseModel):
    target_term: str | None = None
    policy: Policy | None = None


class GlossaryDryRunResponse(BaseModel):
    """PATCH dry_run=true(plans/03 §7.9)。"""

    affected_block_count: int


class GlossaryPatchResponse(BaseModel):
    """PATCH dry_run=false(plans/03 §7.9)。"""

    term: GlossaryTermItem
    affected_block_count: int
    job_id: str | None


class GlossaryPromoteResponse(BaseModel):
    term: GlossaryTermItem
