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


# ---------------------------------------------------------------------------
# 公開記事コメント(Task 25)
# ---------------------------------------------------------------------------
CommentStatus = Literal["visible", "hidden", "deleted"]

# 本文の長さ制約(plain text)。空文字は不可・最大 4000 文字。
COMMENT_BODY_MIN = 1
COMMENT_BODY_MAX = 4000

# HTML タグ(`<...>`)を丸ごと除去する。plain text のみ保存するため属性・スクリプトも消える。
_HTML_TAG_RE = re.compile(r"<[^>]*>")


def sanitize_comment_body(raw: str) -> str:
    """コメント本文を plain text に落とす。

    - HTML タグ(``<...>``)を除去する(``<script>`` / ``<b>`` などを保存しない)。
    - 前後の空白を落とす。タグ除去で残ったテキストノードはそのまま残す。
    """
    return _HTML_TAG_RE.sub("", raw).strip()


class CommentCreateRequest(BaseModel):
    block_id: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=COMMENT_BODY_MIN, max_length=COMMENT_BODY_MAX)
    # 返信先(同一 publication の 1 階層のみ。深いネストは API 層で拒否)。
    parent_id: str | None = None

    @field_validator("body")
    @classmethod
    def _validate_body(cls, v: str) -> str:
        # HTML を除去した後にも 1〜4000 文字であることを保証する(タグだけの投稿を弾く)。
        cleaned = sanitize_comment_body(v)
        if len(cleaned) < COMMENT_BODY_MIN:
            raise ValueError("本文を入力してください")
        if len(cleaned) > COMMENT_BODY_MAX:
            raise ValueError(f"本文は{COMMENT_BODY_MAX}文字以内で入力してください")
        return cleaned


class CommentUpdateRequest(BaseModel):
    body: str = Field(min_length=COMMENT_BODY_MIN, max_length=COMMENT_BODY_MAX)

    @field_validator("body")
    @classmethod
    def _validate_body(cls, v: str) -> str:
        cleaned = sanitize_comment_body(v)
        if len(cleaned) < COMMENT_BODY_MIN:
            raise ValueError("本文を入力してください")
        if len(cleaned) > COMMENT_BODY_MAX:
            raise ValueError(f"本文は{COMMENT_BODY_MAX}文字以内で入力してください")
        return cleaned


class CommentOut(BaseModel):
    """コメント 1 件。hidden / deleted は本文を伏せて返す(status で状態を伝える)。"""

    id: str
    block_id: str
    parent_id: str | None = None
    # 本文は visible のときのみ返す(hidden / deleted は空文字)。
    body: str
    status: CommentStatus
    created_at: str | None = None
    updated_at: str | None = None


__all__ = [
    "COMMENT_BODY_MAX",
    "COMMENT_BODY_MIN",
    "CommentCreateRequest",
    "CommentOut",
    "CommentStatus",
    "CommentUpdateRequest",
    "PublicArticleOut",
    "PublicationCreateRequest",
    "PublicationOut",
    "PublicationUpdateRequest",
    "Visibility",
    "sanitize_comment_body",
]
