"""structured output 互換戦略(plans/04 §12)。

ネイティブ対応プロバイダでも _validate は必ず通す(_attach_parsed)。JSON モード互換
戦略(DeepSeek 等)はスキーマをプロンプトへ注入し、検証失敗時に修正指示付きで再試行。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import jsonschema  # type: ignore[import-untyped]

from yakudoku_llm.errors import ErrorKind, ProviderError, SchemaValidationFailed
from yakudoku_llm.types import ContentPart, JsonSchemaSpec, LLMRequest, LLMResponse, Message

if TYPE_CHECKING:
    from yakudoku_llm.protocols import LLMProvider

SCHEMA_PROMPT = (
    "\n\n出力は次の JSON Schema に厳密に従う JSON オブジェクトのみとする。"
    "説明文・コードフェンスを含めない。\nSchema:\n{schema_json}"
)

_FIX_PROMPT = "\n\n前回の出力は検証に失敗した: {err}。修正して JSON のみを再出力せよ。"


def _validate(text: str, spec: JsonSchemaSpec) -> dict[str, Any]:
    """json.loads + jsonschema(draft 2020-12)検証。失敗は SchemaValidationFailed。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 稀にコードフェンスが混じる場合の緩和(json / ``` を剥がす)
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise SchemaValidationFailed(f"not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise SchemaValidationFailed("top-level JSON must be an object")
    try:
        jsonschema.validate(data, spec.json_schema)
    except jsonschema.ValidationError as e:
        raise SchemaValidationFailed(str(e.message)) from e
    return data


def attach_parsed(resp: LLMResponse, spec: JsonSchemaSpec) -> LLMResponse:
    """ネイティブ structured の検証。失敗は SCHEMA_VALIDATION の ProviderError。"""
    try:
        resp.parsed = _validate(resp.text, spec)
    except SchemaValidationFailed as e:
        raise ProviderError(ErrorKind.SCHEMA_VALIDATION, resp.provider, resp.model, str(e)) from e
    return resp


def _inject_schema_prompt(req: LLMRequest, spec: JsonSchemaSpec) -> LLMRequest:
    schema_json = json.dumps(spec.json_schema, ensure_ascii=False)
    suffix = SCHEMA_PROMPT.format(schema_json=schema_json)
    return _append_to_last_user(req, suffix)


def _append_to_last_user(req: LLMRequest, suffix: str) -> LLMRequest:
    messages = [m.model_copy(deep=True) for m in req.messages]
    if messages and messages[-1].role == "user":
        parts = list(messages[-1].parts)
        parts.append(ContentPart(type="text", text=suffix))
        messages[-1] = Message(role="user", parts=parts)
    else:
        messages.append(Message(role="user", parts=[ContentPart(type="text", text=suffix)]))
    return req.model_copy(update={"messages": messages})


async def structured_with_json_mode(provider: LLMProvider, req: LLMRequest) -> LLMResponse:
    """structured_native=False のプロバイダ用。検証失敗は最大2回、エラーを添えて再生成。"""
    assert req.json_schema is not None
    spec = req.json_schema
    work = _inject_schema_prompt(req, spec)
    last_err: str | None = None
    for _ in range(3):  # 初回 + 再試行2回
        if last_err:
            work = _append_to_last_user(work, _FIX_PROMPT.format(err=last_err))
        resp = await provider.generate(work)
        try:
            resp.parsed = _validate(resp.text, spec)
            return resp
        except SchemaValidationFailed as e:
            last_err = str(e)[:500]
    raise ProviderError(
        ErrorKind.SCHEMA_VALIDATION,
        provider.name,
        req.model,
        f"structured output validation failed: {last_err}",
    )
