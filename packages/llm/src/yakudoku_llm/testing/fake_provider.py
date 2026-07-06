"""FakeLLMProvider / FakeImageProvider(plans/04 §2・plans/12 §8.1)。

決定的応答規則:
- generate: responses[task] があればそれを返す。なければ最終 user メッセージの
  プレースホルダ(⟦…⟧)を抽出し「(訳) {先頭40文字} {トークンを出現逆順に連結}」を返す。
- generate_structured: structured[schema.name] または既定ルックアップ表を検証して返す。
  未知スキーマは SCHEMA_VALIDATION(テスト書き漏れの検出)。
- generate_stream: generate 結果を 20 文字ごとの text_delta に分割。
- 故障注入: fail=True(既定 kind=MODEL_NOT_FOUND=即フォールバック)、error_kind 指定可、
  script=[ErrorKind|None, ...] で呼び出し n 回目ごとの故障を指定。
- usage は文字数から決定的に算出(tokens = ceil(chars / 4))。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from yakudoku_llm.errors import ErrorKind, ProviderError
from yakudoku_llm.structured import attach_parsed
from yakudoku_llm.testing._assets import png_bytes
from yakudoku_llm.types import (
    ImageRequest,
    ImageResult,
    LLMRequest,
    LLMResponse,
    StreamEvent,
    Usage,
)

_PLACEHOLDER = re.compile(r"⟦[^⟧]*⟧")

# 既定 structured ルックアップ表(§8.1)。呼び出し側スキーマに対して検証する。
_DEFAULT_STRUCTURED: dict[str, dict[str, Any]] = {
    "overview_figure_dsl_v1": {
        "title": "Rectified Flow",
        "cards": [
            {"id": "c1", "label": "ノイズ"},
            {"id": "c2", "label": "直線輸送"},
            {"id": "c3", "label": "データ"},
        ],
        "edges": [{"from": "c1", "to": "c2"}, {"from": "c2", "to": "c3"}],
    },
    "vocab_entry_v1": {
        "term": "rectified flow",
        "reading": "レクティファイド フロー",
        "definition_ja": "直線的な確率フローで生成を行う手法。",
        "part_of_speech": "noun",
        "examples": ["Rectified flow straightens the trajectory."],
        "related": ["flow matching"],
        "outside_knowledge": False,
        "confidence": 0.9,
    },
    "article_v1": {
        "blocks": [
            {"type": "heading", "text": "概要"},
            {"type": "paragraph", "text": "本稿は Rectified Flow を解説する。"},
            {"type": "heading", "text": "手法"},
            {"type": "paragraph", "text": "確率フローを直線化する。"},
            {"type": "heading", "text": "結果"},
            {"type": "paragraph", "text": "少ステップで高品質を得る。"},
        ]
    },
    "chat_answer_v1": {
        "answer_md": "本論文では直線化された輸送を用いる[[ev:1]]。",
        "evidence": [{"index": 1, "block_id": "blk-0001"}],
        "outside_knowledge": "一般に拡散モデルは多ステップを要する(論文外の知識)。",
    },
}


def _last_user_text(req: LLMRequest) -> str:
    for msg in reversed(req.messages):
        if msg.role == "user":
            return "".join(p.text or "" for p in msg.parts if p.type == "text")
    return ""


def _tokens(chars: int) -> int:
    return max(0, math.ceil(chars / 4))


class FakeLLMProvider:
    """決定的なテキスト生成 Fake(LLMProvider 準拠)。"""

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        *,
        structured: dict[str, dict[str, Any]] | None = None,
        fail: bool = False,
        error_kind: ErrorKind = ErrorKind.MODEL_NOT_FOUND,
        retry_after_s: float | None = None,
        script: list[ErrorKind | None] | None = None,
        name: str = "fake",
    ) -> None:
        self.name = name
        self._responses = responses or {}
        self._structured = structured or {}
        self._fail = fail
        self._error_kind = error_kind
        self._retry_after_s = retry_after_s
        self._script = script
        self._script_idx = 0
        self.calls = 0

    def _maybe_fail(self, model: str) -> None:
        if self._script is not None:
            kind = self._script[self._script_idx] if self._script_idx < len(self._script) else None
            self._script_idx += 1
            if kind is not None:
                raise ProviderError(kind, self.name, model, "scripted failure")
            return
        if self._fail:
            raise ProviderError(
                self._error_kind,
                self.name,
                model,
                "injected failure",
                retry_after_s=self._retry_after_s,
            )

    def _input_chars(self, req: LLMRequest) -> int:
        total = sum(len(p.text or "") for p in req.system)
        for msg in req.messages:
            total += sum(len(p.text or "") for p in msg.parts)
        return total

    def _usage(self, req: LLMRequest, output_text: str) -> Usage:
        return Usage(
            input_tokens=_tokens(self._input_chars(req)),
            output_tokens=_tokens(len(output_text)),
        )

    def _render(self, req: LLMRequest) -> str:
        source = _last_user_text(req)
        tokens = _PLACEHOLDER.findall(source)
        reversed_join = "".join(reversed(tokens))
        return f"「(訳) {source[:40]} {reversed_join}」"

    async def generate(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        self._maybe_fail(req.model)
        task = req.metadata.get("task", "")
        text = self._responses.get(task)
        if text is None:
            text = self._render(req)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=req.model,
            usage=self._usage(req, text),
            request_id=f"fake-{self.calls}",
        )

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        self._maybe_fail(req.model)
        if req.json_schema is None:
            raise ProviderError(
                ErrorKind.INVALID_REQUEST, self.name, req.model, "json_schema required"
            )
        spec = req.json_schema
        data = self._structured.get(spec.name) or _DEFAULT_STRUCTURED.get(spec.name)
        if data is None:
            raise ProviderError(
                ErrorKind.SCHEMA_VALIDATION,
                self.name,
                req.model,
                f"unknown schema: {spec.name}",
            )
        text = json.dumps(data, ensure_ascii=False)
        resp = LLMResponse(
            text=text,
            provider=self.name,
            model=req.model,
            usage=self._usage(req, text),
            request_id=f"fake-{self.calls}",
        )
        return attach_parsed(resp, spec)

    async def generate_stream(self, req: LLMRequest) -> Any:  # AsyncIterator[StreamEvent]
        self.calls += 1
        yield StreamEvent(type="start")
        try:
            self._maybe_fail(req.model)
        except ProviderError as err:
            yield StreamEvent(type="error", error_kind=str(err.kind), error_message=err.message)
            return
        task = req.metadata.get("task", "")
        text = self._responses.get(task) or self._render(req)
        for i in range(0, len(text), 20):
            yield StreamEvent(type="text_delta", delta=text[i : i + 20])
        usage = self._usage(req, text)
        yield StreamEvent(type="usage", usage=usage)
        resp = LLMResponse(text=text, provider=self.name, model=req.model, usage=usage)
        yield StreamEvent(type="end", response=resp)

    async def count_tokens(self, req: LLMRequest) -> int:
        return max(1, _tokens(self._input_chars(req)))


class FakeImageProvider:
    """決定的な画像生成 Fake(ImageProvider 準拠)。単色 PNG を返す。"""

    def __init__(
        self,
        *,
        fail: bool = False,
        error_kind: ErrorKind = ErrorKind.MODEL_NOT_FOUND,
        width: int = 1024,
        height: int = 1024,
        name: str = "fake",
    ) -> None:
        self.name = name
        self._fail = fail
        self._error_kind = error_kind
        self._width = width
        self._height = height
        self.calls = 0

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        self.calls += 1
        if self._fail:
            raise ProviderError(self._error_kind, self.name, req.model, "injected failure")
        return ImageResult(
            image_bytes=png_bytes(self._width, self._height),
            provider=self.name,
            model=req.model,
            revised_prompt=req.prompt,
            request_id=f"fake-img-{self.calls}",
        )
