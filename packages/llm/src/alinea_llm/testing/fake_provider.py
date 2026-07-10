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

from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.structured import attach_parsed
from alinea_llm.testing._assets import png_bytes
from alinea_llm.types import (
    ImageRequest,
    ImageResult,
    LLMRequest,
    LLMResponse,
    StreamEvent,
    Usage,
)

_PLACEHOLDER = re.compile(r"⟦[^⟧]*⟧")

# 既定 structured ルックアップ表(§8.1)。呼び出し側スキーマに対して検証する。
#
# 決定(M2-17 followup): 以下 3 件は元の記述が呼び出し側スキーマ(alinea_core.article.schema /
# alinea_figures.dsl / alinea_worker.tasks.generate_vocab_ai の実装)と整合しておらず
# (キー名不一致・必須フィールド欠落・additionalProperties=false 違反)、ALINEA_FAKE_LLM=1
# 経路(E2E・開発)で record 生成/概要図/語彙 AI 生成が ProviderChainExhausted で必ず失敗する
# バグだった。E2E(PW-13/PW-20)を通すための最小修正として実装側スキーマに合わせて書き直す
# (deviations 参照)。
_DEFAULT_STRUCTURED: dict[str, dict[str, Any]] = {
    "overview_figure_dsl_v1": {
        "layout": "flow-3",
        "cards": [
            {
                "role": "problem",
                "label": "課題",
                "heading": "既存手法は輸送が非直線的で低速",
                "body": "従来のフローは曲がった経路をたどり、生成に多くのステップを要する。",
                "tone": "neutral",
            },
            {
                "role": "proposal",
                "label": "提案 — RECTIFIED FLOW",
                "heading": "確率フローを直線化する学習法",
                "body": "ノイズとデータを結ぶ輸送写像を直線に近づけて学習する。",
                "tone": "accent",
            },
            {
                "role": "result",
                "label": "結果",
                "heading": "少ないステップで高品質な生成",
                "body": "直線化により推論のステップ数を大きく削減できる。",
                "tone": "green",
            },
        ],
        "connectors": [{"from": 0, "to": 1}, {"from": 1, "to": 2}],
    },
    # alinea_worker.tasks.generate_vocab_ai の実スキーマ名・9 フィールドと一致させる
    # (plans/07 §7.2)。旧名 vocab_entry_v1 は実装のどこからも参照されない死んだキーだった。
    "vocab_content_v1": {
        "kind": "word",
        "pos_label": "他動詞",
        "ipa": "/ˌrektɪˈfaɪd fləʊ/",  # noqa: RUF001
        "meaning_short": "直線化されたフロー",
        "meaning_long": (
            "確率フローを**直線的な輸送**へ整える手法。"
            "この文では「rectified flow」がその学習法を指す。"
        ),
        "interpretation": (
            "ノイズとデータを結ぶ経路を直線に近づけることで、少ないステップでの生成を可能にする考え方。"
        ),
        "etymology": "rectify(まっすぐにする)+ flow(流れ)。",
        "mnemonic": "曲がった川を「まっすぐ(rectify)」に付け替えるイメージ。",
        "related_forms": "flow matching, straight-line transport",
    },
    "article_v1": {
        "title": "Rectified Flow を読む",
        "blocks": [
            {"type": "heading", "heading": {"level": 2, "text": "概要"}},
            {"type": "paragraph", "markdown": "本稿は Rectified Flow を解説する。"},
            {"type": "heading", "heading": {"level": 2, "text": "手法"}},
            {
                "type": "paragraph",
                "markdown": "確率フローを直線化する。",
                # E2E(rectified-flow シード)の実セクション ID。根拠チップ→原文ジャンプの
                # 検証対象(PW-13)。§4.5 step2 は未知参照を無害に落とすため、シード以外の
                # コンテキストで使っても記事生成自体は失敗しない。
                "evidence": ["sec-2"],
            },
            {"type": "heading", "heading": {"level": 2, "text": "結果"}},
            {"type": "paragraph", "markdown": "少ステップで高品質を得る。"},
            {
                "type": "discussion",
                "discussion": {
                    "items": [
                        {"text": "少ないステップでの生成品質はどこまで改善するか?", "origin": "ai"},
                        {"text": "他のドメインへの応用可能性は?", "origin": "ai"},
                    ]
                },
            },
            {"type": "heading", "heading": {"level": 2, "text": "まとめ"}},
            {"type": "paragraph", "markdown": "本稿の要点を振り返る。"},
        ],
    },
    "chat_answer_v1": {
        "answer_md": "本論文では直線化された輸送を用いる[[ev:1]]。",
        "evidence": [{"index": 1, "block_id": "blk-0001"}],
        "outside_knowledge": "一般に拡散モデルは多ステップを要する(論文外の知識)。",
    },
    # 数字トークンを含めない(pipeline._summary_numbers_ok は要約中の数字が原稿に
    # 部分一致するか検証するため。ランダムな arXiv 末尾番号に依存して検証が揺れると
    # summary_3line 生成の成否が非決定になり VR-1g 等がフレークする — 原文にある数値
    # だけを使う実運用の制約を Fake でも守り、決定的に検証を通す)。
    "summary_3line_v1": {
        "summary_lines": [
            "課題: 既存手法は輸送が非直線的で生成が低速になりがち。",
            "提案: 確率フローを直線的な輸送へ整える学習法を提案する。",
            "仕組み: データ対を結ぶ経路を反復的に直線化する。",
            "検証: 生成とドメイン転送の設定で既存法と比較する。",
            "結果: 少ないステップで高品質な生成・転送を実現する。",
        ],
        "suggested_tags": ["mock", "e2e"],
    },
}


def _last_user_text(req: LLMRequest) -> str:
    for msg in reversed(req.messages):
        if msg.role == "user":
            return "".join(p.text or "" for p in msg.parts if p.type == "text")
    return ""


# 翻訳バッチの user メッセージ内「[block_id] (type) text」行(継続行は直前に連結)。
_TARGET_LINE = re.compile(r"^\[([^\]\s]+)\] \([a-z_]+\) (.*)$")


def _synth_translation_batch(req: LLMRequest) -> dict[str, Any]:
    """translation_batch_v1 の決定的エコー訳を合成する。

    user メッセージの「翻訳対象ブロック」行からブロック id と保護済みテキストを抽出し、
    プレースホルダトークンをそのまま保って「訳:」接頭辞のエコーを返す(検証 protocol の
    「全トークンちょうど 1 回」を構造的に満たす)。E2E・ALINEA_FAKE_LLM=1 用。
    """
    translations: list[dict[str, str]] = []
    in_targets = False
    for line in _last_user_text(req).splitlines():
        if line.startswith("# 翻訳対象ブロック"):
            in_targets = True
            continue
        if in_targets and line.startswith("# "):
            break
        if not in_targets:
            continue
        m = _TARGET_LINE.match(line)
        if m:
            translations.append({"id": m.group(1), "ja": f"訳: {m.group(2)}"})
        elif translations and line.strip():
            translations[-1]["ja"] += "\n" + line
    return {"translations": translations}


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
        if data is None and spec.name == "translation_batch_v1":
            data = _synth_translation_batch(req)
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
