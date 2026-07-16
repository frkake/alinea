"""文脈ビルダー(plans/07 §2.2、docs/05 §3)。

`document_revisions.content` を「論文コンテキスト」の平文形式(§2.2.2)へ展開し、
system[0] プリアンブル + system[1] 論文文脈 + (任意)system[2] 注釈・メモ + 会話履歴 +
今回の質問(選択周辺全文つき)を ``LLMRequest`` に組む。**訳文は入れない(原文を正)**。

M0 簡略化: 圧縮モード(全セクション要約 + 関連セクション全文)は未実装で、全文モードの
予算超過時はトークン予算までの切詰めで代替する(§2.2.3〜2.2.4 は後続)。
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from typing import Any

import tiktoken
from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.document.plaintext import block_to_plain, inline_to_plain
from alinea_core.search.rebuild import BlockIndexRow, compute_index_rows
from alinea_llm.types import ContentPart, LLMRequest, Message

from alinea_api.chat.prompts import format_system_preamble

# トークン予算(plans/07 §2.2.1 確定値)。
SYSTEM1_FULL_BUDGET = 60_000
ANNOTATIONS_BUDGET = 4_000  # system[2] 注釈・メモ(§2.2.1)
HISTORY_BUDGET = 12_000
SURROUNDING_CONTEXT_BLOCKS = 2  # アンカー ±2 ブロック(§2.2.1)
MAX_OUTPUT_TOKENS = 8_192

# 注釈の色 → 日本語ラベル(annotations.color の値域・§2.2.5)。
_ANNOTATION_COLOR_LABELS = {
    "important": "重要",
    "question": "疑問",
    "idea": "アイデア",
    "term": "用語",
}
_NOTE_BODY_PREVIEW = 500  # メモ本文の冒頭文字数(§2.2.5)


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


def render_document_context(content: DocumentContent, revision_id: str) -> str:
    """`document_revisions.content` を「論文コンテキスト」平文へ展開する(§2.2.2)。

    行頭 `[block_id|位置]` が根拠マーカーの語彙になる。reference_entry は含めない。
    """
    rows = {r.block_id: r for r in compute_index_rows(content)}
    lines: list[str] = [f"# 論文コンテキスト(revision {revision_id})"]

    def walk(sec: Section) -> None:
        header = f"## [{sec.id}|{_section_label(sec)}] {sec.heading.title or ''}".rstrip()
        lines.append(header)
        for blk in sec.blocks:
            if blk.type == "reference_entry":
                continue
            text = _block_text(blk)
            if not text:
                continue
            row = rows.get(blk.id)
            position = _display_position(row) if row is not None else _section_label(sec)
            lines.append(f"[{blk.id}|{position}] {text}")
        for sub in sec.sections:
            walk(sub)

    for s in content.sections:
        walk(s)
    return "\n".join(lines)


def _truncate_to_budget(text: str, budget: int) -> str:
    """予算トークンを超える文脈を切り詰める(M0 の圧縮モード代替)。"""
    enc = _encoder()
    ids = enc.encode(text, disallowed_special=())
    if len(ids) <= budget:
        return text
    return enc.decode(ids[:budget]) + "\n…(文脈が長いため以降を省略しました)"


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


def render_annotations_context(
    *,
    annotations: Sequence[Mapping[str, Any]],
    notes: Sequence[Mapping[str, Any]],
    validator: Any,
) -> str:
    """注釈(ハイライト/コメント)・メモを system[2] の平文へ整形する(plans/07 §2.2.5)。

    ``validator`` は :class:`alinea_api.chat.evidence.EvidenceValidator`(``display_for``
    で位置表記を導出する)。予算 ANNOTATIONS_BUDGET を超えたら末尾から切り詰める。
    どちらも空なら空文字を返す(呼び出し側は system[2] を付けない)。
    """
    lines: list[str] = []

    for ann in annotations:
        anchor = ann.get("anchor") if isinstance(ann.get("anchor"), Mapping) else {}
        block_id = str(anchor.get("block_id", "")) if isinstance(anchor, Mapping) else ""
        position = validator.display_for(block_id) or ""
        pos_tag = f"[{block_id}|{position}]" if block_id else ""
        kind = ann.get("kind")
        quote = ""
        if isinstance(anchor, Mapping) and anchor.get("quote"):
            quote = f' "{str(anchor["quote"]).strip()}"'
        if kind == "comment":
            body = str(ann.get("body") or "").strip()
            comment = f"(コメント: {body})" if body else ""
            lines.append(f"- コメント {pos_tag}{quote}{comment}".rstrip())
        else:
            color = str(ann.get("color") or "")
            label = _ANNOTATION_COLOR_LABELS.get(color)
            head = f"ハイライト({label})" if label else "ハイライト"
            lines.append(f"- {head} {pos_tag}{quote}".rstrip())

    for note in notes:
        title = str(note.get("title") or "").strip()
        body = str(note.get("body_md") or "").strip().replace("\n", " ")
        preview = body[:_NOTE_BODY_PREVIEW]
        ellipsis = "…" if len(body) > _NOTE_BODY_PREVIEW else ""
        title_part = f"({title}) " if title else ""
        lines.append(f"- メモ: {title_part}{preview}{ellipsis}".rstrip())

    if not lines:
        return ""
    header = "# ユーザーの注釈・メモ(参考。回答の根拠は本文のみ)"
    return _truncate_to_budget("\n".join([header, *lines]), ANNOTATIONS_BUDGET)


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
    system1 = _truncate_to_budget(
        render_document_context(content, revision_id), SYSTEM1_FULL_BUDGET
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
    "ANNOTATIONS_BUDGET",
    "HISTORY_BUDGET",
    "SYSTEM1_FULL_BUDGET",
    "build_chat_request",
    "estimate_tokens",
    "render_annotations_context",
    "render_document_context",
]
