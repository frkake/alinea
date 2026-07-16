"""文脈ビルダー(plans/07 §2.2、docs/05 §3)。

`document_revisions.content` を「論文コンテキスト」の平文形式(§2.2.2)へ展開し、
system[0] プリアンブル + system[1] 論文文脈 + (任意)system[2] 注釈・メモ + 会話履歴 +
今回の質問(選択周辺全文つき)を ``LLMRequest`` に組む。**訳文は入れない(原文を正)**。

圧縮モード(docs/05 §3): 論文コンテキストが ``SYSTEM1_FULL_BUDGET`` を超える場合は末尾
切詰めではなく「全セクションの要約 + 質問・選択に関連するセクションの全文」で構成する
(:mod:`alinea_core.document.context_compaction`)。選択範囲の周辺全文は user メッセージ側で
``_render_surroundings`` が別途付与する。要約は決定的な抽出的要約で LLM に依存しない。
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from typing import Any

import tiktoken
from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.document.context_compaction import (
    RenderedSection,
    compact_document_context,
)
from alinea_core.document.plaintext import block_to_plain, inline_to_plain
from alinea_core.search.rebuild import BlockIndexRow, compute_index_rows
from alinea_llm.types import ContentPart, LLMRequest, Message

from alinea_api.chat.prompts import format_system_preamble

# トークン予算(plans/07 §2.2.1 確定値)。
SYSTEM1_FULL_BUDGET = 60_000
HISTORY_BUDGET = 12_000
SURROUNDING_CONTEXT_BLOCKS = 2  # アンカー ±2 ブロック(§2.2.1)
MAX_OUTPUT_TOKENS = 8_192

# 圧縮モードに入ったことをモデルへ伝える注記(docs/05 §3)。
_COMPRESSION_NOTE = (
    "(注記: 本文が長いため、関連の低いセクションは各セクションの要約に圧縮しています。"
    "全文が必要なセクションがあれば、そのセクションについて質問してください。)"
)


@functools.lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("o200k_base")


def estimate_tokens(text: str) -> int:
    """tiktoken o200k_base によるローカル見積り(§2.2.1)。"""
    return len(_encoder().encode(text, disallowed_special=()))


def _block_text(blk: Block) -> str:
    """モデルに渡す 1 ブロックの本文表現(§2.2.2)。"""
    if blk.type == "equation":
        latex = (blk.latex or "").strip()
        return f"$$ {latex} $$" if latex else ""
    if blk.type in ("figure", "table"):
        kind = "figure" if blk.type == "figure" else "table"
        caption = inline_to_plain(blk.caption)
        return f"({kind}) Caption: {caption}" if caption else f"({kind})"
    if blk.type == "code":
        head = (blk.code or "").splitlines()[:20]
        return "\n".join(head)
    return block_to_plain(blk)


def _display_position(row: BlockIndexRow) -> str:
    """行頭 `[block_id|位置]` の位置表記(§2.2.2 / §2.5.2)。"""
    if row.element_label:
        return row.element_label
    if row.paragraph_ordinal is not None:
        return f"{row.section_label} ¶{row.paragraph_ordinal}"
    return row.section_label


def _section_label(sec: Section) -> str:
    num = sec.heading.number
    return f"§{num}" if num else (sec.heading.title or sec.id)


def _context_preamble(revision_id: str) -> str:
    return f"# 論文コンテキスト(revision {revision_id})"


def render_context_sections(content: DocumentContent) -> list[RenderedSection]:
    """`document_revisions.content` を節ノード単位の展開済みセクション列へ変換する(§2.2.2)。

    行頭 `[block_id|位置]` が根拠マーカーの語彙になる。reference_entry は含めない。
    入れ子セクションは文書順にフラットな列へ並べる(圧縮モードのセクション単位に対応)。
    """
    rows = {r.block_id: r for r in compute_index_rows(content)}
    out: list[RenderedSection] = []

    def walk(sec: Section) -> None:
        header = f"## [{sec.id}|{_section_label(sec)}] {sec.heading.title or ''}".rstrip()
        body_lines: list[str] = []
        for blk in sec.blocks:
            if blk.type == "reference_entry":
                continue
            text = _block_text(blk)
            if not text:
                continue
            row = rows.get(blk.id)
            position = _display_position(row) if row is not None else _section_label(sec)
            body_lines.append(f"[{blk.id}|{position}] {text}")
        out.append(RenderedSection(section_id=sec.id, header=header, body_lines=tuple(body_lines)))
        for sub in sec.sections:
            walk(sub)

    for s in content.sections:
        walk(s)
    return out


def render_document_context(content: DocumentContent, revision_id: str) -> str:
    """全セクション全文の「論文コンテキスト」平文(圧縮しない場合の出力。§2.2.2)。"""
    lines: list[str] = [_context_preamble(revision_id)]
    for sec in render_context_sections(content):
        lines.append(sec.header)
        lines.extend(sec.body_lines)
    return "\n".join(lines)


def _anchor_section_ids(
    content: DocumentContent,
    context_anchors: Sequence[Mapping[str, Any]],
) -> set[str]:
    """選択アンカーの block_id が属するセクション ID を求める(圧縮モードで全文昇格する)。"""
    if not context_anchors:
        return set()
    wanted = {str(a.get("block_id", "")) for a in context_anchors}
    section_of: dict[str, str] = {}
    for sec, blk in content.iter_blocks():
        section_of[blk.id] = sec.id
    return {section_of[b] for b in wanted if b in section_of}


def _render_surroundings(
    content: DocumentContent,
    context_anchors: Sequence[Mapping[str, Any]],
) -> str:
    """選択アンカー ±2 ブロックの原文全文(§2.2.1)。"""
    if not context_anchors:
        return ""
    rows = compute_index_rows(content)
    blocks = {blk.id: blk for _sec, blk in content.iter_blocks()}
    index_of = {r.block_id: i for i, r in enumerate(rows)}
    wanted: set[int] = set()
    for anchor in context_anchors:
        idx = index_of.get(str(anchor.get("block_id", "")))
        if idx is None:
            continue
        lo = max(0, idx - SURROUNDING_CONTEXT_BLOCKS)
        hi = min(len(rows) - 1, idx + SURROUNDING_CONTEXT_BLOCKS)
        wanted.update(range(lo, hi + 1))
    if not wanted:
        return ""
    out: list[str] = []
    for i in sorted(wanted):
        row = rows[i]
        blk = blocks.get(row.block_id)
        text = _block_text(blk) if blk is not None else row.source_text
        if text:
            out.append(f"[{row.block_id}|{_display_position(row)}] {text}")
    return "\n".join(out)


def _select_history(history: Sequence[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    """新しい方から予算に収まる分だけ採用し、時系列順に並べ直す(§2.2.6)。"""
    selected: list[tuple[str, str]] = []
    used = 0
    for role, text in reversed(history):
        cost = estimate_tokens(text)
        if selected and used + cost > budget:
            break
        selected.append((role, text))
        used += cost
    selected.reverse()
    return selected


def build_chat_request(
    *,
    content: DocumentContent,
    revision_id: str,
    title: str,
    authors_short: str,
    venue_year: str,
    arxiv_id: str,
    user_content: str,
    history: Sequence[tuple[str, str]] = (),
    context_anchors: Sequence[Mapping[str, Any]] = (),
    include_annotations: bool = True,
    annotations_text: str | None = None,
) -> LLMRequest:
    """チャット 1 ターンの ``LLMRequest`` を組む(§2.2)。model は Router が差し込む。"""
    system0 = format_system_preamble(
        title=title, authors_short=authors_short, venue_year=venue_year, arxiv_id=arxiv_id
    )
    # 予算超過時は末尾切詰めではなく「全セクション要約 + 関連セクション全文」で圧縮する(§3)。
    system1 = compact_document_context(
        render_context_sections(content),
        budget=SYSTEM1_FULL_BUDGET,
        preamble=_context_preamble(revision_id),
        note=_COMPRESSION_NOTE,
        query=user_content,
        anchor_section_ids=_anchor_section_ids(content, context_anchors),
    )
    system_parts = [
        ContentPart.from_text(system0, cache_hint=True),  # キャッシュ第1境界(§2.6)
        ContentPart.from_text(system1, cache_hint=True),  # キャッシュ第2境界(§2.2.1)
    ]
    if include_annotations and annotations_text:
        system_parts.append(ContentPart.from_text(annotations_text))  # 境界なし(§2.2.5)

    messages: list[Message] = []
    for role, text in _select_history(list(history), HISTORY_BUDGET):
        norm_role = "assistant" if role == "assistant" else "user"
        messages.append(Message(role=norm_role, parts=[ContentPart.from_text(text)]))

    user_text = user_content
    surroundings = _render_surroundings(content, context_anchors)
    if surroundings:
        user_text = f"{user_content}\n\n# 選択箇所の周辺\n{surroundings}"
    messages.append(Message(role="user", parts=[ContentPart.from_text(user_text)]))

    return LLMRequest(
        model="",  # Router が解決モデルを差し込む(stream_pipeline.request_for_model)
        system=system_parts,
        messages=messages,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        effort="medium",
        timeout_s=120.0,
        prompt_cache_key=f"chat:{revision_id}",
        metadata={"task": "chat"},
    )


__all__ = [
    "HISTORY_BUDGET",
    "SYSTEM1_FULL_BUDGET",
    "build_chat_request",
    "estimate_tokens",
    "render_context_sections",
    "render_document_context",
]
