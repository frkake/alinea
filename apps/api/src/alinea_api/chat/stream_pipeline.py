"""ストリーム変換パイプライン(plans/07 §2.4、plans/03 §10.3 SSE 契約が正)。

モデル生テキスト(`[[evidence:ID]]` + `<outside_knowledge>`/`<speculation>` タグ)を
検証済み SSE イベント(`delta` / `evidence`)へ変換する状態機械。同時に DB 保存用の
ChatContentJson(`⟦A:n⟧` プレースホルダ + segments)と evidence_anchors(AnchorJson)を蓄積する。

3 層対応(§2.3): モデル層 `[[evidence:ID]]`+タグ / API 層 `[[ev:n]]`+evidence イベント+aside /
DB 層 `⟦A:n⟧`+segments。`n` は共通(1 起点)。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

from alinea_core.document.plaintext import strip_markdown
from alinea_llm.errors import ProviderChainExhausted
from alinea_llm.protocols import LLMProvider, MeterHook
from alinea_llm.types import LLMRequest

from alinea_api.chat.evidence import MAX_EVIDENCE, EvidenceValidator

# モデル出力の根拠マーカーとタグ(§2.4)。
_MARKER = r"\[\[evidence:((?:blk|sec)-[A-Za-z0-9-]+)\]\]"
_TAGS = r"(</?(?:outside_knowledge|speculation)>)"
TOKEN_RE = re.compile(_MARKER + "|" + _TAGS)
OPEN_TAGS = {"<outside_knowledge>": "outside_knowledge", "<speculation>": "speculation"}
CLOSE_TAGS = {"</outside_knowledge>", "</speculation>"}
ALL_TAGS = (*OPEN_TAGS.keys(), *CLOSE_TAGS)
HOLDBACK = 48  # 文字。[[evidence:…]] の最長 ~40 + 余裕(§2.4)。

_MARKER_PREFIX = "[[evidence:"
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
_PLACEHOLDER_RE = re.compile(r"⟦A:(\d+)⟧")


@dataclass
class SseEvent:
    """1 つの SSE イベント(`event` 名 + JSON data)。"""

    event: str  # "delta" | "evidence"
    data: dict[str, Any]


def _is_incomplete_marker(suf: str) -> bool:
    if _MARKER_PREFIX.startswith(suf):
        return True
    if suf.startswith(_MARKER_PREFIX) and "]]" not in suf:
        return all(c in _ID_CHARS for c in suf[len(_MARKER_PREFIX) :])
    return False


def _is_incomplete_tag(suf: str) -> bool:
    if ">" in suf:
        return False
    return any(t.startswith(suf) for t in ALL_TAGS)


def _dangerous_suffix_len(buf: str) -> int:
    """末尾で分断されうる不完全なマーカー/タグの長さ(0=保留不要)。"""
    n = len(buf)
    for length in range(min(HOLDBACK, n), 0, -1):
        head = buf[n - length]
        if head == "[" and _is_incomplete_marker(buf[n - length :]):
            return length
        if head == "<" and _is_incomplete_tag(buf[n - length :]):
            return length
    return 0


def _anchor_json(anchor: dict[str, Any]) -> dict[str, Any]:
    """AnchorRef(display 付き)→ AnchorJson(display なし。§2.5.2 で display は保存しない)。"""
    return {k: anchor.get(k) for k in ("revision_id", "block_id", "start", "end", "quote", "side")}


class StreamPipeline:
    """モデル生テキスト → SSE イベント + ChatContentJson 蓄積(§2.4)。"""

    def __init__(self, validator: EvidenceValidator) -> None:
        self.validator = validator
        self.block_index = 0
        self.block_type = "markdown"
        self.aside_label: str | None = None
        self.buf = ""
        self.dropped = 0  # 実在せず除去した根拠件数(evidence_dropped ログ用)
        self._evidence: list[dict[str, Any]] = []  # AnchorRef(display 付き)。ref 順
        self._ref_by_block: dict[str, int] = {}
        self._segments: list[dict[str, str]] = []
        self._cur_md = ""
        self._cur_seg_type = "text"
        self._pending = False  # 現ブロックが delta を出したか(空ブロック検出・index 進行)
        self._first_delta = True

    # --- 供給/終端 ---------------------------------------------------------
    def feed(self, delta: str) -> Iterator[SseEvent]:
        self.buf += delta
        length = _dangerous_suffix_len(self.buf)
        emit, self.buf = (self.buf, "") if length == 0 else (self.buf[:-length], self.buf[-length:])
        yield from self._process(emit)

    def finish(self) -> Iterator[SseEvent]:
        emit, self.buf = self.buf, ""
        yield from self._process(emit)
        # 終端で aside が閉じていなければ自動で閉じる(§2.4 規則3)。
        if self.block_type == "aside":
            self._switch("markdown", None)
        else:
            self._finalize_segment()

    # --- トークン処理 ------------------------------------------------------
    def _process(self, emit: str) -> Iterator[SseEvent]:
        pos = 0
        for m in TOKEN_RE.finditer(emit):
            if m.start() > pos:
                yield from self._emit_text(emit[pos : m.start()])
            if m.group(1) is not None:
                yield from self._emit_marker(m.group(1))
            else:
                self._apply_tag(m.group(2))
            pos = m.end()
        if pos < len(emit):
            yield from self._emit_text(emit[pos:])

    def _emit_text(self, txt: str) -> Iterator[SseEvent]:
        if not txt:
            return
        self._cur_md += txt
        yield self._delta(txt)

    def _emit_marker(self, block_id: str) -> Iterator[SseEvent]:
        anchor = self.validator.resolve(block_id)
        if anchor is None:
            self.dropped += 1  # 実在しない参照はトークンごと除去(§2.5.1・P1)
            return
        ref, first = self._ref_for(anchor)
        if ref is None:  # 上限超過(§2.5.1)
            self.dropped += 1
            return
        self._cur_md += f"⟦A:{ref}⟧"
        yield self._delta(f"[[ev:{ref}]]")
        if first:
            yield SseEvent(
                "evidence",
                {"ref": ref, "display": anchor["display"], "anchor": _anchor_json(anchor)},
            )

    def _apply_tag(self, tag: str) -> None:
        # 入れ子・不整合タグは黙って捨てる(§2.4 規則3)。
        if tag in OPEN_TAGS and self.block_type == "markdown":
            self._switch("aside", OPEN_TAGS[tag])
        elif tag in CLOSE_TAGS and self.block_type == "aside":
            self._switch("markdown", None)

    # --- 補助 --------------------------------------------------------------
    def _delta(self, text: str) -> SseEvent:
        data: dict[str, Any] = {
            "block_index": self.block_index,
            "block_type": self.block_type,
            "text": text,
        }
        if self.block_type == "aside" and self._first_delta and self.aside_label:
            data["label"] = self.aside_label  # aside 初回 delta にのみ label(§10.3)
        self._first_delta = False
        self._pending = True
        return SseEvent("delta", data)

    def _ref_for(self, anchor: dict[str, Any]) -> tuple[int | None, bool]:
        block_id = anchor["block_id"]
        existing = self._ref_by_block.get(block_id)
        if existing is not None:
            return existing, False
        if len(self._evidence) >= MAX_EVIDENCE:
            return None, False
        ref = len(self._evidence) + 1  # 1 起点(§2.4)
        self._evidence.append(anchor)
        self._ref_by_block[block_id] = ref
        return ref, True

    def _switch(self, new_type: str, new_label: str | None) -> None:
        self._finalize_segment()
        if self._pending:  # 空ブロックでは index を進めない(§2.4 規則4)
            self.block_index += 1
        self.block_type = new_type
        self.aside_label = new_label
        self._cur_md = ""
        self._cur_seg_type = "text" if new_type == "markdown" else (new_label or "text")
        self._pending = False
        self._first_delta = True

    def _finalize_segment(self) -> None:
        md = self._cur_md.strip()
        if md:
            self._segments.append({"type": self._cur_seg_type, "md": md})

    # --- 蓄積結果 ----------------------------------------------------------
    @property
    def segments(self) -> list[dict[str, str]]:
        return self._segments

    @property
    def evidence(self) -> list[dict[str, Any]]:
        return self._evidence

    def content_json(self) -> dict[str, Any]:
        """chat_messages.content(ChatContentJson §3.5)。"""
        return {"segments": self._segments}

    def evidence_anchors_json(self) -> list[dict[str, Any]]:
        """chat_messages.evidence_anchors(AnchorJson[]。display なし)。"""
        return [_anchor_json(a) for a in self._evidence]

    def text_plain(self) -> str:
        """検索・履歴文脈用の平文。`⟦A:n⟧` を `(表示位置)` 表記へ展開する(§2.4 規則6)。"""

        def repl(m: re.Match[str]) -> str:
            n = int(m.group(1))
            if 1 <= n <= len(self._evidence):
                return f"({self._evidence[n - 1]['display']})"
            return ""

        parts = [_PLACEHOLDER_RE.sub(repl, seg["md"]) for seg in self._segments]
        return strip_markdown(" ".join(parts))


# ---------------------------------------------------------------------------
# ルータからの単一プロバイダ・ストリーミング(plans/07 §2.1、plans/04 §9.1 規則5)
# ---------------------------------------------------------------------------
# LLMRouter は M0 では明示チェーン形のみで公開ストリーム API を持たない。チャットは
# 「開始後フォールバックしない」(§9.1-5)ため、先頭の有効プロバイダで generate_stream し、
# 計測は Router が保持する MeterHook 経由で記録する(§10)。
def resolve_primary(router: Any) -> tuple[str, str, LLMProvider]:
    """チェーン先頭の有効プロバイダ (name, model, provider) を返す。無ければ例外。"""
    active = router._active()
    if not active:
        raise ProviderChainExhausted("chat", [])
    name, model, provider = active[0]
    return cast("tuple[str, str, LLMProvider]", (name, model, provider))


def request_for_model(request: LLMRequest, model: str) -> LLMRequest:
    """build_context が組んだ LLMRequest に、解決済みモデル ID を差し込む。"""
    meta = {**request.metadata}
    meta.setdefault("task", "chat")
    return request.model_copy(update={"model": model, "metadata": meta})


async def record_usage(router: Any, draft: Any) -> None:
    """Router が保持する MeterHook に 1 行記録する(BYOK 帰属は DbMeterHook が補正)。"""
    meter: MeterHook | None = getattr(router, "_meter", None)
    if meter is not None:
        await meter.record(draft)


__all__ = [
    "HOLDBACK",
    "SseEvent",
    "StreamPipeline",
    "record_usage",
    "request_for_model",
    "resolve_primary",
]
