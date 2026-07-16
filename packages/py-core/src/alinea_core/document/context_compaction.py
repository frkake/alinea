"""文脈コンパクション(docs/05 §3 圧縮モード)。api / worker 共有の純関数。

構造化ドキュメントを「論文コンテキスト」平文へ展開したものが予算トークンを超える場合、
末尾切詰め(後方セクション脱落)ではなく **「全セクションの要約 + 関連セクション全文」** で
構成する(docs/05 §3)。要約は決定的な抽出的要約(各セクションのリード文)で、LLM に依存
しない。関連度はメモリ内で決定的に算出する(選択アンカー優先 + 質問キーワード一致)。

同一入力→同一出力。live-LLM 呼び出しを一切足さない(テストは FakeLLM/script provider 前提)。
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import tiktoken

# 抽出要約: セクションあたり残す本文行数とリード文数。
_SUMMARY_HEAD_LINES = 1
_SUMMARY_LEAD_SENTENCES = 2
# 質問キーワード抽出: ラテン英数字トークンの最小長。
_MIN_QUERY_TERM_LEN = 3
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。!?])\s+")
_WORD = re.compile(rf"[A-Za-z0-9]{{{_MIN_QUERY_TERM_LEN},}}")


@functools.lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("o200k_base")


def estimate_tokens(text: str) -> int:
    """tiktoken o200k_base によるローカル見積り(既存の推定器と同一)。"""
    return len(_encoder().encode(text, disallowed_special=()))


@dataclass(frozen=True)
class RenderedSection:
    """1 セクション(節ノード)の展開済み表現。

    - ``header``: 行頭 ``## [id|位置] タイトル`` の見出し行。
    - ``body_lines``: 行頭 ``[block_id|位置] 本文`` のブロック行列(reference_entry 等は除外済み)。
    - ``search_text``: 関連度スコアリング用の平文(未指定なら body_lines から導出)。
    """

    section_id: str
    header: str
    body_lines: tuple[str, ...] = ()
    search_text: str | None = None

    @property
    def full_text(self) -> str:
        return "\n".join((self.header, *self.body_lines)) if self.body_lines else self.header

    @property
    def score_text(self) -> str:
        if self.search_text is not None:
            return self.search_text
        return "\n".join((self.header, *self.body_lines))

    def summary_text(self) -> str:
        """抽出的要約: 見出し + 先頭ブロックのリード文(決定的)。"""
        if not self.body_lines:
            return self.header
        kept: list[str] = []
        for line in self.body_lines[:_SUMMARY_HEAD_LINES]:
            kept.append(_lead_sentences(line))
        return "\n".join((self.header, *kept))


def _lead_sentences(line: str) -> str:
    """行頭マーカー ``[id|位置]`` を保ったまま、本文の先頭数文だけ残す。"""
    prefix = ""
    rest = line
    if line.startswith("["):
        end = line.find("] ")
        if end != -1:
            prefix = line[: end + 2]
            rest = line[end + 2 :]
    sentences = _SENTENCE_SPLIT.split(rest.strip())
    lead = " ".join(sentences[:_SUMMARY_LEAD_SENTENCES]).strip()
    return f"{prefix}{lead}" if lead else prefix.rstrip()


def render_full(preamble: str, sections: Sequence[RenderedSection]) -> str:
    """全セクション全文の平文(圧縮しない場合の出力。既存レンダラと同型)。

    ``preamble`` が空文字なら先頭行を足さない(前置きを持たない素材と byte 一致させる)。
    """
    lines = [preamble] if preamble else []
    for sec in sections:
        lines.append(sec.header)
        lines.extend(sec.body_lines)
    return "\n".join(lines)


def _query_terms(query: str | None) -> set[str]:
    if not query:
        return set()
    return {m.group(0).lower() for m in _WORD.finditer(query)}


def _relevance_score(sec: RenderedSection, terms: set[str]) -> int:
    if not terms:
        return 0
    text = sec.score_text.lower()
    return sum(1 for t in terms if t in text)


def compact_document_context(
    sections: Sequence[RenderedSection],
    *,
    budget: int,
    preamble: str,
    note: str = "",
    query: str | None = None,
    anchor_section_ids: Iterable[str] = (),
) -> str:
    """docs/05 §3 の圧縮モードで文脈平文を組む。

    予算内なら全文をそのまま返す。超過時は「preamble + 注記 + 全セクション要約」を土台に、
    関連セクション(アンカー→質問一致→文書順)を予算内で全文へ昇格する。全セクションの
    見出し+リード要約は必ず残るため、後方セクションが丸ごと落ちることはない。
    """
    sections = list(sections)
    full = render_full(preamble, sections)
    if estimate_tokens(full) <= budget:
        return full

    anchors = set(anchor_section_ids)
    terms = _query_terms(query)

    # 昇格順: アンカー → 質問一致(スコア降順・文書順) → 残りを文書順。
    order = list(range(len(sections)))
    priority = sorted(
        order,
        key=lambda i: (
            0 if sections[i].section_id in anchors else 1,
            -_relevance_score(sections[i], terms),
            i,
        ),
    )

    promoted: set[int] = set()
    # 土台(全要約)のトークン量を起点に、全文へ昇格するたびに差分を加算する。
    parts = [p for p in (preamble, note) if p]
    header = "\n".join(parts)
    summaries = [sec.summary_text() for sec in sections]
    used = estimate_tokens(header + "\n" + "\n".join(summaries))

    for i in priority:
        summary_cost = estimate_tokens(summaries[i])
        full_cost = estimate_tokens(sections[i].full_text)
        delta = full_cost - summary_cost
        if delta <= 0 or used + delta <= budget:
            promoted.add(i)
            used += max(delta, 0)

    body = [sections[i].full_text if i in promoted else summaries[i] for i in range(len(sections))]
    out = "\n".join(([header] if header else []) + body)

    # 最終ガード: 病的入力(セクション数が極端に多い)で要約合計が予算を超える場合のみ、
    # 末尾を機械切詰めする。現実的な論文では到達しない。
    if estimate_tokens(out) > budget:
        enc = _encoder()
        ids = enc.encode(out, disallowed_special=())
        out = enc.decode(ids[:budget])
    return out


__all__ = [
    "RenderedSection",
    "compact_document_context",
    "estimate_tokens",
    "render_full",
]
