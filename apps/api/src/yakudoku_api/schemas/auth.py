"""auth エンドポイントの DTO(plans/03 §2)。"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# email-validator 非導入のため軽量チェックに留める(厳密検証はメール到達で担保)。
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailRequestBody(BaseModel):
    email: str
    next: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        candidate = value.strip().lower()
        if not _EMAIL_RE.match(candidate):
            raise ValueError("メールアドレスの形式が正しくありません")
        return candidate


class EmailRequestResponse(BaseModel):
    sent: bool = True


class MeUser(BaseModel):
    id: str
    email: str
    display_name: str
    avatar_url: str | None = None
    providers: list[str]
    created_at: str


class MeResponse(BaseModel):
    user: MeUser
    unread_notifications: int


class ExtensionTokenResponse(BaseModel):
    token: str
    expires_at: str


class AccountDeleteBody(BaseModel):
    confirm: str = Field(description="誤操作防止の合言葉。'delete' のみ受理する。")


class AccountDeleteResponse(BaseModel):
    job_id: str
