"""記事公開スキーマ(Task 24)。

公開スナップショットは記事のサニタイズ済み部分集合。source quote 本文・訳文・メモ・
チャット・discussion・原論文図を一切含まない(:mod:`alinea_core.article.publication`)。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Visibility = Literal["unlisted", "public"]

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PublicationCreateRequest(BaseModel):
    visibility: Visibility = "unlisted"
    # 明示 slug は任意。省略時はサーバが決定的に採番する。
    slug: str | None = Field(default=None, max_length=120)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if not v:
            return None
        if not _SLUG_RE.match(v):
            raise ValueError("slug は英小文字・数字・ハイフンのみ使用できます")
        return v


class PublicationUpdateRequest(BaseModel):
    visibility: Visibility


class PublicationOut(BaseModel):
    """所有者向けの公開メタ(管理用)。"""

    id: str
    article_id: str
    slug: str
    visibility: Visibility
    snapshot_version: int
    title: str
    published_at: str | None = None
    updated_at: str | None = None


class PublicArticleOut(BaseModel):
    """slug 読み取り(認証不要)で返す公開スナップショット本体。"""

    slug: str
    title: str
    visibility: Visibility
    snapshot_version: int
    # public のみ検索索引を許可(unlisted は noindex=true)。
    noindex: bool
    paper_meta: dict[str, Any]
    blocks: list[dict[str, Any]]
    published_at: str | None = None


__all__ = [
    "PublicArticleOut",
    "PublicationCreateRequest",
    "PublicationOut",
    "PublicationUpdateRequest",
    "Visibility",
]
