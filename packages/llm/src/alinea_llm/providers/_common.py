"""アダプタ共通ヘルパ(メッセージ変換・PNG 正規化・エラー分類・base_url 上書き)。

ベース URL 上書き環境変数 ALINEA_{PROVIDER}_BASE_URL(plans/12 §15 ⚠-2)に対応する。
未設定時は各 SDK 既定 URL を使う。
"""

from __future__ import annotations

import io
import os
from typing import Any

from PIL import Image

from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.types import ContentPart, LLMRequest, Message


def base_url_override(provider: str, default: str | None = None) -> str | None:
    """ALINEA_{PROVIDER}_BASE_URL があれば優先、なければ default(SDK 既定は None)。"""
    return os.environ.get(f"ALINEA_{provider.upper()}_BASE_URL") or default


def to_png(data: bytes) -> bytes:
    """任意の画像バイト列を PNG に正規化する(§6.6)。"""
    with Image.open(io.BytesIO(data)) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()


def system_text(req: LLMRequest) -> str:
    return "".join(p.text or "" for p in req.system)


def _image_data_url(part: ContentPart) -> str:
    media = part.image_media_type or "image/png"
    return f"data:{media};base64,{part.image_b64 or ''}"


def to_responses_input(messages: list[Message]) -> list[dict[str, Any]]:
    """OpenAI Responses API の input 形式(input_text / input_image)。"""
    out: list[dict[str, Any]] = []
    for m in messages:
        content: list[dict[str, Any]] = []
        for p in m.parts:
            if p.type == "image":
                content.append({"type": "input_image", "image_url": _image_data_url(p)})
            else:
                key = "output_text" if m.role == "assistant" else "input_text"
                content.append({"type": key, "text": p.text or ""})
        out.append({"role": m.role, "content": content})
    return out


def to_chat_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """OpenAI 互換 Chat Completions のメッセージ形式(画像は image_url data URL)。"""
    out: list[dict[str, Any]] = []
    for m in messages:
        if any(p.type == "image" for p in m.parts):
            parts: list[dict[str, Any]] = []
            for p in m.parts:
                if p.type == "image":
                    parts.append({"type": "image_url", "image_url": {"url": _image_data_url(p)}})
                else:
                    parts.append({"type": "text", "text": p.text or ""})
            out.append({"role": m.role, "content": parts})
        else:
            out.append({"role": m.role, "content": "".join(p.text or "" for p in m.parts)})
    return out


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Anthropic Messages 形式(画像は base64 source ブロック)。"""
    out: list[dict[str, Any]] = []
    for m in messages:
        content: list[dict[str, Any]] = []
        for p in m.parts:
            if p.type == "image":
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": p.image_media_type or "image/png",
                            "data": p.image_b64 or "",
                        },
                    }
                )
            else:
                content.append({"type": "text", "text": p.text or ""})
        out.append({"role": m.role, "content": content})
    return out


def _retry_after(err: Exception) -> float | None:
    response = getattr(err, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def classify_openai(err: Exception, provider: str, model: str) -> ProviderError:
    """openai SDK(OpenAI / DeepSeek / xAI 共通)例外 → ErrorKind(§4)。"""
    # 遅延 import(循環回避)。openai は必須依存。
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
    )

    status = getattr(err, "status_code", None)
    body_text = str(getattr(err, "message", "") or err).lower()
    kind: ErrorKind
    if isinstance(err, APITimeoutError):
        kind = ErrorKind.TIMEOUT
    elif isinstance(err, APIConnectionError):
        kind = ErrorKind.CONNECTION
    elif isinstance(err, RateLimitError):
        kind = ErrorKind.BILLING if "insufficient_quota" in body_text else ErrorKind.RATE_LIMIT
    elif isinstance(err, AuthenticationError | PermissionDeniedError):
        kind = ErrorKind.AUTH
    elif isinstance(err, NotFoundError):
        kind = ErrorKind.MODEL_NOT_FOUND
    elif isinstance(err, BadRequestError):
        kind = (
            ErrorKind.CONTEXT_LENGTH
            if "context_length" in body_text or "too long" in body_text
            else ErrorKind.INVALID_REQUEST
        )
    elif isinstance(err, InternalServerError):
        kind = ErrorKind.SERVER
    elif status == 503:
        kind = ErrorKind.OVERLOADED
    elif status == 402:
        kind = ErrorKind.BILLING
    else:
        kind = ErrorKind.SERVER  # 分類不能は安全側(再試行される)
    return ProviderError(
        kind,
        provider,
        model,
        str(err),
        status_code=status,
        request_id=getattr(err, "request_id", None),
        retry_after_s=_retry_after(err),
    )
