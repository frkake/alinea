"""翻訳パイプライン駆動(plans/06 §2-§7・§9・§12・§13)。

責務:
- 翻訳対象スコープ判定(§2.1)と進捗の分母(§13.1)。
- 設定 4 項目(4f)の反映(§2.2)。
- バッチ化(§3.3)・文脈パッキング(§6)・structured output(§7)。
- プレースホルダ検証(§4.4)→ 失敗時のプロンプト再構成再試行(§4.6)→ なお失敗なら
  原文フォールバック(P3。docs/03 §4「壊れた訳を見せない」)。
- 自動品質検査 5 種(§12)。
- 共有キャッシュ解決(shared / personal 2 層。§9・plans/02 §5.2)と進捗計算(§13.1)。

LLM 実行は :mod:`alinea_llm`(``LLMRouter.complete(mode="structured")``)へ委譲する。
本層の再試行はプレースホルダ検証失敗によるプロンプト再構成であり、LLM 層のエラーリトライ
(§15)と直交する。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from alinea_llm.errors import ErrorKind, ProviderChainExhausted
from alinea_llm.router import LLMRouter
from alinea_llm.types import ContentPart, JsonSchemaSpec, LLMRequest, Message
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_core.db.models import (
    DocumentRevision,
    Paper,
    TranslationSet,
    TranslationUnit,
)
from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.translation.glossary import format_glossary_lines, glossary_hash
from alinea_core.translation.placeholder import (
    TOKEN_RE,
    EncodedBlock,
    decode_translation,
    encode_block,
    verify_tokens,
)
from alinea_core.translation.prompts import (
    TargetBlock,
    TranslationBatchOut,
    build_paper_context,
    build_system_preamble,
    build_user_message,
    field_profile,
)

# --- 確定値(plans/06 §3.3・§6・§12) --------------------------------------------

BATCH_MAX_BLOCKS = 8
BATCH_MAX_SOURCE_TOKENS = 2800
MAX_OUTPUT_TOKENS = 4096
MAX_RETRIES = 2  # 初回 + 再試行 2 回 = 計 3 回(docs/03 §4)
CONTEXT_PREV_BLOCKS = 2
CONTEXT_TRUNCATE_CHARS = 600

# 自動翻訳対象のブロック型(docs/03 §2・plans/06 §2.1)。
TRANSLATABLE_BLOCK_TYPES: frozenset[str] = frozenset(
    {"paragraph", "heading", "figure", "table", "list", "quote", "theorem", "footnote"}
)

# 訳の配信を止める(API が text_ja: null で返す)3 フラグ(plans/06 §12・§16-4)。
BLOCKING_FLAGS: frozenset[str] = frozenset(
    {"placeholder_mismatch", "provider_refusal", "context_overflow"}
)

_STRUCTURED_SCHEMA_NAME = "translation_batch_v1"
_APPENDIX_TITLE_RE = re.compile(r"^\s*Appendi(x|ces)\b", re.IGNORECASE)
_REFERENCE_TITLE_RE = re.compile(
    r"\b(references|bibliography|works cited|literature cited)\b", re.IGNORECASE
)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
# 全角数字・全角ピリオド/カンマ を半角へ(§12 の「全角→半角正規化」)。全角文字は意図的。
_FULLWIDTH: dict[int, int] = {ord("０") + i: ord("0") + i for i in range(10)}  # noqa: RUF001
_FULLWIDTH[ord("．")] = ord(".")  # noqa: RUF001
_FULLWIDTH[ord("，")] = ord(",")  # noqa: RUF001


def strip_tokens(text: str) -> str:
    """``⟦KIND:id⟧`` トークンを取り除く(数値・長さ・言語検査の前処理。§12)。"""
    return TOKEN_RE.sub("", text)


# --- 翻訳対象スコープ(plans/06 §2.1) --------------------------------------------


class ScopeResult(BaseModel):
    """自動翻訳スコープ判定の結果(plans/06 §2.1)。"""

    in_scope_block_ids: list[str]  # 文書順
    sections: list[dict[str, Any]]  # [{"section_id", "block_ids"}]
    appendix_section_ids: list[str]
    reference_section_ids: list[str]


def _is_appendix_heading(number: str | None, title: str | None) -> bool:
    num = (number or "").strip()
    if num and num[0].isascii() and num[0].isalpha():  # 'A' / 'B.1'(§2.1-3)
        return True
    return bool(_APPENDIX_TITLE_RE.match(title or ""))


def _is_reference_section(section: Section) -> bool:
    """reference_entry のみを含むセクション(§2.1-2)。"""
    title = (section.heading.title or "").strip()
    if section.id == "sec-refs" or _REFERENCE_TITLE_RE.search(title):
        return True
    blocks = [b for b in section.blocks if b.type != "heading"]
    return bool(blocks) and all(b.type == "reference_entry" for b in blocks)


def _as_content(content: DocumentContent | dict[str, Any]) -> DocumentContent:
    if isinstance(content, DocumentContent):
        return content
    return DocumentContent.model_validate(content)


def compute_translation_scope(content: DocumentContent | dict[str, Any]) -> ScopeResult:
    """自動翻訳対象ブロックを決定する(plans/06 §2.1・docs/03 §2)。

    対象条件: (1) ブロック型が翻訳対象、(2) 参考文献セクションでない、(3) 付録でない。
    equation / code / algorithm / reference_entry は常に対象外。判定は決定的で
    ``block_search_index`` 再生成時にも同値になる。
    """
    doc = _as_content(content)
    in_scope: list[str] = []
    sections: list[dict[str, Any]] = []
    appendix_ids: list[str] = []
    reference_ids: list[str] = []

    def walk(section: Section, under_appendix: bool) -> None:
        is_appendix = under_appendix or _is_appendix_heading(
            section.heading.number, section.heading.title
        )
        is_reference = _is_reference_section(section)
        if is_appendix:
            appendix_ids.append(section.id)
        if is_reference:
            reference_ids.append(section.id)
        own: list[str] = []
        if not is_appendix and not is_reference:
            for blk in section.blocks:
                if blk.type in TRANSLATABLE_BLOCK_TYPES:
                    in_scope.append(blk.id)
                    own.append(blk.id)
        if own:
            sections.append({"section_id": section.id, "block_ids": own})
        for sub in section.sections:
            walk(sub, is_appendix)

    for top in doc.sections:
        walk(top, False)
    return ScopeResult(
        in_scope_block_ids=in_scope,
        sections=sections,
        appendix_section_ids=appendix_ids,
        reference_section_ids=reference_ids,
    )


# --- 設定(4f)反映(plans/06 §2.2・docs/03 §2) -----------------------------------


@dataclass
class TranslationSettings:
    """``users.settings.translation.*``(plans/03 §17.1)。4f の 4 項目。

    ``auto_translate_appendix`` は 4f トグル「付録を自動翻訳しない」の反転
    (既定 ON = 付録を訳さない ⇔ ``auto_translate_appendix=False``)。
    """

    default_style: str = "natural"
    auto_translate_appendix: bool = False
    translate_table_cells: bool = False
    suggest_section_selection_over_30_pages: bool = True

    @classmethod
    def from_user_settings(cls, settings: Mapping[str, Any] | None) -> TranslationSettings:
        t = (settings or {}).get("translation", {}) if settings else {}
        return cls(
            default_style=str(t.get("default_style", "natural")),
            auto_translate_appendix=bool(t.get("auto_translate_appendix", False)),
            translate_table_cells=bool(t.get("translate_table_cells", False)),
            suggest_section_selection_over_30_pages=bool(
                t.get("suggest_section_selection_over_30_pages", True)
            ),
        )


@dataclass
class InitialJobPlan:
    """初回全文翻訳で積むジョブ計画(plans/06 §2.2・§13.1)。分母(スコープ)は不変。"""

    style: str
    section_ids: list[str]  # translate_section ジョブを積むセクション
    propose_section_selection: bool  # 30 ページ超の選択提案(P6)
    include_appendix: bool
    translate_table_cells: bool


def plan_initial_translation(
    content: DocumentContent | dict[str, Any],
    settings: TranslationSettings,
    *,
    pages: int,
) -> InitialJobPlan:
    """設定 4 項目を初回翻訳のジョブ生成へ反映する(plans/06 §2.2)。

    設定はジョブを積む範囲だけを変える。スコープ判定(§2.1)と進捗の分母は不変。
    """
    scope = compute_translation_scope(content)
    section_ids = [s["section_id"] for s in scope.sections]
    if settings.auto_translate_appendix:
        section_ids = section_ids + list(scope.appendix_section_ids)
    propose = settings.suggest_section_selection_over_30_pages and pages > 30
    return InitialJobPlan(
        style=settings.default_style,
        section_ids=[] if propose else section_ids,
        propose_section_selection=propose,
        include_appendix=settings.auto_translate_appendix,
        translate_table_cells=settings.translate_table_cells,
    )


# --- バッチ化(plans/06 §3.3) ------------------------------------------------------


class BlockToTranslate(BaseModel):
    """バッチ 1 件。復元・検証用の :class:`EncodedBlock` と表示用の block_type を持つ。"""

    encoded: EncodedBlock
    block_type: str


def estimate_source_tokens(text: str) -> int:
    """プレースホルダ化済み原文のトークン見積り。

    plans/06 §3.3 は tiktoken ``o200k_base`` * 1.1 を指定するが、py-core は tiktoken を
    依存に持たない(deviations 参照)ため、決定的な文字数ヒューリスティック
    ``ceil(len/4 * 1.1)`` で近似する。バッチ上限の構造的保証
    という目的は満たす。
    """
    return math.ceil(len(text) / 4 * 1.1)


def make_batches(
    items: list[BlockToTranslate],
    *,
    max_blocks: int = BATCH_MAX_BLOCKS,
    max_tokens: int = BATCH_MAX_SOURCE_TOKENS,
    estimator: Callable[[str], int] = estimate_source_tokens,
) -> list[list[BlockToTranslate]]:
    """文書順のまま貪欲に分割(plans/06 §3.3)。単独超過ブロックは単独バッチ。"""
    batches: list[list[BlockToTranslate]] = []
    current: list[BlockToTranslate] = []
    current_tokens = 0
    for item in items:
        tokens = estimator(item.encoded.text)
        if current and (len(current) >= max_blocks or current_tokens + tokens > max_tokens):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(item)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


# --- インライン正規化・text_ja 導出(plans/06 §4.3・引き継ぎメモ) -------------------


def normalize_inlines(inlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """空 text を除去し隣接 text をマージする(decode のギャップ text 対策)。"""
    out: list[dict[str, Any]] = []
    for il in inlines:
        t = il.get("t")
        if t == "emphasis":
            out.append({**il, "children": normalize_inlines(il.get("children", []))})
        elif t == "text":
            v = il.get("v", "")
            if v == "":
                continue
            if out and out[-1].get("t") == "text":
                out[-1] = {"t": "text", "v": out[-1]["v"] + v}
            else:
                out.append({"t": "text", "v": v})
        else:
            out.append(il)
    return out


def content_to_text_ja(inlines: list[dict[str, Any]]) -> str:
    """content_ja(復元済みインライン列)から検索・長さ検査用の平文を導出(§4.3)。"""
    parts: list[str] = []
    for il in inlines:
        t = il.get("t")
        if t == "text":
            parts.append(il.get("v", ""))
        elif t == "emphasis":
            parts.append(content_to_text_ja(il.get("children", [])))
        elif t == "math_inline":
            parts.append(il.get("v", ""))
        elif t == "citation":
            parts.append(f"[{il.get('ref', '')}]")
        elif t == "ref":
            parts.append(il.get("v") or il.get("ref") or "")
        elif t == "code_inline":
            parts.append(il.get("v", ""))
        elif t == "url":
            parts.append(il.get("v") or il.get("href") or "")
        # footnote_ref は平文に出さない
    return "".join(parts).strip()


# --- 自動品質検査(plans/06 §12) --------------------------------------------------


def _numbers(text: str) -> Counter[str]:
    normalized = text.translate(_FULLWIDTH)
    return Counter(m.group(0).replace(",", "") for m in _NUM_RE.finditer(normalized))


def _no_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _is_japanese(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3040 <= o <= 0x309F  # ひらがな
        or 0x30A0 <= o <= 0x30FF  # カタカナ
        or 0x4E00 <= o <= 0x9FFF  # CJK 統合漢字
    )


def _word_boundary_re(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", re.IGNORECASE)


def run_quality_checks(
    encoded: EncodedBlock,
    source_plain: str,
    text_ja: str,
    snapshot: list[dict[str, Any]],
) -> list[str]:
    """5 種のうちプレースホルダ以外の 4 種を判定(§12)。UPSERT 直前に実行する。

    ``placeholder_mismatch`` / ``provider_refusal`` / ``context_overflow`` は原文
    フォールバック側(§4.6)で付与するため、ここでは扱わない。
    """
    flags: list[str] = []
    src_no_tok = strip_tokens(encoded.text)
    ja_no_tok = strip_tokens(text_ja)

    # 2) 数値・単位の一致
    if _numbers(source_plain) != _numbers(ja_no_tok):
        flags.append("number_mismatch")

    # 3) 長さ逸脱(原文 60 文字未満は検査しない。合格帯 0.30-1.10)
    src_len = len(_no_ws(src_no_tok))
    if src_len >= 60:
        r = len(_no_ws(ja_no_tok)) / src_len
        if not (0.30 <= r <= 1.10):
            flags.append("length_outlier")

    # 4) 用語スナップショットからの逸脱
    for entry in snapshot:
        src_term = entry.get("source_term", "")
        if not src_term or not _word_boundary_re(src_term).search(encoded.text):
            continue
        policy = entry.get("policy", "translate")
        if policy == "keep_original":
            if src_term.lower() not in text_ja.lower():
                flags.append("glossary_violation")
                break
        else:  # translate / both
            if entry.get("target_term", "") not in text_ja:
                flags.append("glossary_violation")
                break

    # 5) 未訳(英文がそのまま残っている)
    non_ws = [c for c in ja_no_tok if not c.isspace()]
    if non_ws:
        jp = sum(1 for c in non_ws if _is_japanese(c))
        if jp / len(non_ws) < 0.05 and len(src_no_tok.split()) >= 4:
            flags.append("untranslated")

    return flags


# --- 翻訳結果(pipeline レベルの in-memory 表現) ----------------------------------


class TranslatedUnit(BaseModel):
    """1 ブロックの翻訳結果(DB 行の元)。

    ``state`` は pipeline レベルの状態で、``source_fallback`` は P3 の原文フォールバックを
    表す(引き継ぎメモ)。DB の ``translation_units.state`` は ``machine/edited/protected``
    のみ(plans/02 §4.4 の CHECK)なので、永続化時は :meth:`db_state` で ``machine`` に写す。
    """

    block_id: str
    source_hash: str
    content_ja: list[dict[str, Any]] | dict[str, Any]
    text_ja: str
    state: str = "machine"
    quality_flags: list[str] = []
    model: str = ""

    def db_state(self) -> str:
        return "machine" if self.state == "source_fallback" else self.state

    @property
    def is_displayable(self) -> bool:
        return not (set(self.quality_flags) & BLOCKING_FLAGS)


def _fallback_unit(item: BlockToTranslate, flag: str, model: str) -> TranslatedUnit:
    """原文フォールバック行(plans/06 §4.6)。content_ja=[] / text_ja='' / state 写像=machine。"""
    return TranslatedUnit(
        block_id=item.encoded.block_id,
        source_hash=item.encoded.source_hash,
        content_ja=[],
        text_ja="",
        state="source_fallback",
        quality_flags=[flag],
        model=model,
    )


def _build_unit(
    item: BlockToTranslate, ja: str, snapshot: list[dict[str, Any]], model: str
) -> TranslatedUnit:
    content = normalize_inlines(decode_translation(item.encoded, ja))
    text_ja = content_to_text_ja(content)
    flags = run_quality_checks(item.encoded, strip_tokens(item.encoded.text), text_ja, snapshot)
    return TranslatedUnit(
        block_id=item.encoded.block_id,
        source_hash=item.encoded.source_hash,
        content_ja=content,
        text_ja=text_ja,
        state="machine",
        quality_flags=flags,
        model=model,
    )


# --- 見出し原題併記(plans/06 §14・docs/03 §6.2) ----------------------------------


def heading_display(number: str | None, title_ja: str | None, title_en: str) -> str:
    """「訳題 — 原題」形式(クライアント合成用の参照実装。§14)。

    例: ``heading_display("1", "はじめに", "Introduction") == "1 はじめに — Introduction"``。
    未訳(``title_ja`` が None/空)は原題のみ。
    """
    if not title_ja:
        return title_en
    num = (number or "").strip()
    head = f"{num} {title_ja}" if num else title_ja
    return f"{head} — {title_en}"


# --- LLM バッチ翻訳(plans/06 §4.4・§4.6・§5-§7) ---------------------------------


@dataclass
class TranslationContext:
    """1 バッチのプロンプト構築に必要な文脈(§5-§6)。"""

    style: str = "natural"
    snapshot: list[dict[str, Any]] = field(default_factory=list)
    revision_id: str = ""
    glossary_hash: str = ""
    system_preamble: str = ""
    paper_context: str = ""
    section_path_display: str = ""
    prev_source_blocks: list[str] = field(default_factory=list)
    prev_translations: list[str] = field(default_factory=list)
    next_source_block: str | None = None
    reason: str = "initial"
    instruction: str = ""
    task: str = "translation"


def _fmt_tokens(tokens: list[str]) -> str:
    return ", ".join(tokens) if tokens else "(なし)"


def _feedback_text(vr: Any, encoded: EncodedBlock, attempt: int) -> str:
    """検証失敗フィードバック(§4.6)。attempt 1 は差分、attempt 2 は全トークン列挙。"""
    if attempt == 1:
        return (
            "前回の出力はトークン検証に失敗した。"
            f"欠落: {_fmt_tokens(vr.missing)} / 重複: {_fmt_tokens(vr.duplicated)} / "
            f"不明: {_fmt_tokens(vr.unknown)}。"
            "原文中の全トークンを、変更せずちょうど1回ずつ含めて翻訳し直すこと。"
        )
    all_tokens = ", ".join(te.token for te in encoded.tokens)
    return (
        f"この訳文には次のトークンを必ず各1回含める: {all_tokens}。"
        "思考の途中経過や説明を出力しない。"
    )


def _build_request(
    group: list[BlockToTranslate], ctx: TranslationContext, feedback: str
) -> LLMRequest:
    targets = [
        TargetBlock(block_id=it.encoded.block_id, block_type=it.block_type, text=it.encoded.text)
        for it in group
    ]
    user_text = build_user_message(
        section_path_display=ctx.section_path_display,
        targets=targets,
        prev_source_blocks=ctx.prev_source_blocks,
        prev_translations=ctx.prev_translations,
        next_source_block=ctx.next_source_block,
        instruction=ctx.instruction if ctx.reason == "instructed" else "",
        retranslate_note=ctx.reason == "retranslate",
        feedback=feedback,
    )
    system_parts = [
        ContentPart(
            type="text",
            text=ctx.system_preamble or build_system_preamble(ctx.style),
            cache_hint=True,
        )
    ]
    if ctx.paper_context:
        system_parts.append(ContentPart(type="text", text=ctx.paper_context, cache_hint=True))
    return LLMRequest(
        model="",  # Router がチェーンの model で上書きする
        system=system_parts,
        messages=[Message(role="user", parts=[ContentPart(type="text", text=user_text)])],
        max_output_tokens=MAX_OUTPUT_TOKENS,
        effort="none",
        json_schema=JsonSchemaSpec(
            name=_STRUCTURED_SCHEMA_NAME, json_schema=TranslationBatchOut.model_json_schema()
        ),
        prompt_cache_key=f"tr:{ctx.revision_id}:{ctx.style}:{ctx.glossary_hash}",
        timeout_s=120.0,
        metadata={"task": ctx.task},
    )


async def _call_llm(
    router: LLMRouter,
    group: list[BlockToTranslate],
    ctx: TranslationContext,
    feedbacks: dict[str, Any],
    attempt: int,
    *,
    user_id: str | None,
    library_item_id: str | None,
    job_id: str | None,
) -> tuple[dict[str, str], str, str]:
    feedback = ""
    if attempt >= 1 and len(group) == 1:
        vr = feedbacks.get(group[0].encoded.block_id)
        if vr is not None:
            feedback = _feedback_text(vr, group[0].encoded, attempt)
    req = _build_request(group, ctx, feedback)
    resp = await router.complete(
        ctx.task,
        request=req,
        mode="structured",
        user_id=user_id,
        library_item_id=library_item_id,
        job_id=job_id,
    )
    parsed: dict[str, str] = {}
    data = resp.parsed or {}
    for entry in data.get("translations", []):
        parsed[str(entry.get("id"))] = str(entry.get("ja", ""))
    return parsed, resp.model, resp.stop_reason


async def _attempt_group(
    router: LLMRouter,
    group: list[BlockToTranslate],
    ctx: TranslationContext,
    attempt: int,
    feedbacks: dict[str, Any],
    results: dict[str, TranslatedUnit],
    overflow: set[str],
    *,
    user_id: str | None,
    library_item_id: str | None,
    job_id: str | None,
) -> tuple[list[BlockToTranslate], str]:
    """1 グループを 1 リクエストで翻訳し、不合格ブロックを返す(再帰で max_tokens 二分割)。"""
    try:
        parsed, model, stop_reason = await _call_llm(
            router,
            group,
            ctx,
            feedbacks,
            attempt,
            user_id=user_id,
            library_item_id=library_item_id,
            job_id=job_id,
        )
    except ProviderChainExhausted as exc:
        if exc.errors and all(e.kind == ErrorKind.CONTENT_FILTER for e in exc.errors):
            for it in group:  # CONTENT_FILTER 全滅 → provider_refusal 縮退(§4.6)
                results[it.encoded.block_id] = _fallback_unit(it, "provider_refusal", "")
            return [], ""
        raise

    if stop_reason == "max_tokens" and len(group) > 1:  # 二分割再送(§3.3)
        mid = len(group) // 2
        left, _ = await _attempt_group(
            router,
            group[:mid],
            ctx,
            attempt,
            feedbacks,
            results,
            overflow,
            user_id=user_id,
            library_item_id=library_item_id,
            job_id=job_id,
        )
        right, _ = await _attempt_group(
            router,
            group[mid:],
            ctx,
            attempt,
            feedbacks,
            results,
            overflow,
            user_id=user_id,
            library_item_id=library_item_id,
            job_id=job_id,
        )
        return left + right, model

    failing: list[BlockToTranslate] = []
    for it in group:
        ja = parsed.get(it.encoded.block_id)
        if ja is None:
            failing.append(it)
            continue
        vr = verify_tokens(it.encoded, ja)
        if vr.ok:
            results[it.encoded.block_id] = _build_unit(it, ja, ctx.snapshot, model)
        else:
            feedbacks[it.encoded.block_id] = vr
            failing.append(it)

    if stop_reason == "max_tokens" and len(group) == 1 and failing:
        overflow.add(group[0].encoded.block_id)  # 単独超過 → context_overflow(§3.3)
    return failing, model


async def translate_batch(
    router: LLMRouter,
    items: list[BlockToTranslate],
    ctx: TranslationContext,
    *,
    user_id: str | None = None,
    library_item_id: str | None = None,
    job_id: str | None = None,
) -> list[TranslatedUnit]:
    """1 バッチ(直列)を翻訳する。検証失敗は最大 2 回再構成再試行→原文フォールバック。"""
    results: dict[str, TranslatedUnit] = {}
    feedbacks: dict[str, Any] = {}
    overflow: set[str] = set()
    last_model = ""
    pending = list(items)
    attempt = 0
    while pending and attempt <= MAX_RETRIES:
        groups = [[it] for it in pending] if attempt > 0 else [pending]
        next_pending: list[BlockToTranslate] = []
        for group in groups:
            failing, model = await _attempt_group(
                router,
                group,
                ctx,
                attempt,
                feedbacks,
                results,
                overflow,
                user_id=user_id,
                library_item_id=library_item_id,
                job_id=job_id,
            )
            if model:
                last_model = model
            next_pending.extend(failing)
        pending = next_pending
        attempt += 1
    for it in pending:  # 全滅 → 原文フォールバック(P3)
        flag = "context_overflow" if it.encoded.block_id in overflow else "placeholder_mismatch"
        results[it.encoded.block_id] = _fallback_unit(it, flag, last_model)
    return [results[it.encoded.block_id] for it in items]


async def _retry_blocking_units(
    router: LLMRouter,
    items: list[BlockToTranslate],
    ctx: TranslationContext,
    units: list[TranslatedUnit],
    *,
    user_id: str | None,
    library_item_id: str | None,
    job_id: str | None,
) -> list[TranslatedUnit]:
    """ブロッキング失敗だけを追加で再送する。retry_failed ジョブでは再帰しない。"""
    if ctx.reason == "retry_failed":
        return units
    failed = {u.block_id for u in units if set(u.quality_flags) & BLOCKING_FLAGS}
    if not failed:
        return units
    retry_items = [item for item in items if item.encoded.block_id in failed]
    retry_ctx = replace(ctx, reason="retry_failed", task="retranslation_escalation")
    retry_units = await translate_batch(
        router,
        retry_items,
        retry_ctx,
        user_id=user_id,
        library_item_id=library_item_id,
        job_id=job_id,
    )
    by_id = {u.block_id: u for u in units}
    for retry_unit in retry_units:
        by_id[retry_unit.block_id] = retry_unit
    return [by_id[item.encoded.block_id] for item in items]


async def translate_block(
    block: dict[str, Any] | Block,
    router: LLMRouter,
    *,
    block_type: str | None = None,
    ctx: TranslationContext | None = None,
    user_id: str | None = None,
    library_item_id: str | None = None,
    job_id: str | None = None,
) -> TranslatedUnit:
    """単一ブロックの翻訳(バッチ 1 件のショートカット)。"""
    block_dict = block.model_dump() if isinstance(block, Block) else block
    encoded = encode_block(block_dict)
    btype = block_type or str(block_dict.get("type", "paragraph"))
    item = BlockToTranslate(encoded=encoded, block_type=btype)
    units = await translate_batch(
        router,
        [item],
        ctx or TranslationContext(),
        user_id=user_id,
        library_item_id=library_item_id,
        job_id=job_id,
    )
    return units[0]


# --- 進捗計算(plans/06 §13.1) ----------------------------------------------------


def compute_progress(units: Iterable[Any], total: int) -> int:
    """「翻訳 96%」への写像(§13.1)。

    分母 = 自動翻訳対象ブロック数。分子 = 表示可能な訳を持つ unit 数(3 ブロッキング
    フラグを持たない)。``progress_pct = floor(100 * 分子 / 分母)``、分母 0 は 100。
    """
    if total <= 0:
        return 100
    displayable = 0
    for u in units:
        if isinstance(u, Mapping):
            flags = u.get("quality_flags", [])
        else:
            flags = getattr(u, "quality_flags", [])
        if not (set(flags or []) & BLOCKING_FLAGS):
            displayable += 1
    return min(100, (100 * displayable) // total)


# --- 共有キャッシュ / personal フォーク解決(plans/06 §9・plans/02 §5.2) ----------


def resolve_translation(
    personal: Mapping[str, Any] | None,
    base: Mapping[str, Any] | None,
    block_id: str,
) -> Any | None:
    """personal → base の順で 1 ブロックの表示用 unit を解決する(plans/02 §5.2)。"""
    if personal is not None and block_id in personal:
        return personal[block_id]
    if base is not None:
        return base.get(block_id)
    return None


async def find_shared_set(
    session: AsyncSession, revision_id: str, style: str
) -> TranslationSet | None:
    """``(revision_id, style)`` の shared セットを引く(§9.1 の解決規則)。"""
    stmt = select(TranslationSet).where(
        TranslationSet.revision_id == revision_id,
        TranslationSet.style == style,
        TranslationSet.scope == "shared",
    )
    return (await session.execute(stmt)).scalars().first()


async def resolve_display_units(
    session: AsyncSession, revision_id: str, style: str, user_id: str
) -> dict[str, TranslationUnit]:
    """表示用 TranslationUnit を解決(plans/02 §5.2。personal 優先→shared)。"""
    sets_subq = (
        select(TranslationSet.id.label("id"), TranslationSet.scope.label("scope"))
        .where(
            TranslationSet.revision_id == revision_id,
            TranslationSet.style == style,
            or_(TranslationSet.scope == "shared", TranslationSet.user_id == user_id),
        )
        .subquery()
    )
    stmt = (
        select(TranslationUnit)
        .join(sets_subq, sets_subq.c.id == TranslationUnit.set_id)
        .distinct(TranslationUnit.block_id)
        .order_by(TranslationUnit.block_id, (sets_subq.c.scope == "personal").desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {u.block_id: u for u in rows}


# --- セクション翻訳(plans/06 §3-§7・§13) ----------------------------------------


class SectionResult(BaseModel):
    section_id: str
    translated: int  # 今回翻訳できたブロック数(フォールバック除く)
    fallback: int  # 原文フォールバックしたブロック数
    skipped: int  # 既訳スキップしたブロック数
    block_ids: list[str]
    set_status: str
    progress_pct: int


def _find_section(content: DocumentContent, section_id: str) -> Section | None:
    def walk(sec: Section) -> Section | None:
        if sec.id == section_id:
            return sec
        for sub in sec.sections:
            found = walk(sub)
            if found is not None:
                return found
        return None

    for top in content.sections:
        found = walk(top)
        if found is not None:
            return found
    return None


def _section_path_display(content: DocumentContent, section_id: str) -> str:
    path: list[str] = []

    def walk(sec: Section, ancestors: list[Section]) -> bool:
        chain = [*ancestors, sec]
        if sec.id == section_id:
            path.extend(f"{s.heading.number} {s.heading.title}".strip() for s in chain)
            return True
        return any(walk(sub, chain) for sub in sec.sections)

    for top in content.sections:
        if walk(top, []):
            break
    return " > ".join(p for p in path if p)


def _toc_outline(content: DocumentContent) -> str:
    lines: list[str] = []
    for top in content.sections:
        lines.append(f"- {top.heading.number} {top.heading.title}".rstrip())
        for sub in top.sections:
            lines.append(f"  - {sub.heading.number} {sub.heading.title}".rstrip())
    return "\n".join(lines)


def _authors_short(authors: list[Any]) -> str:
    names = [str(a.get("name", a)) if isinstance(a, dict) else str(a) for a in authors[:3]]
    suffix = " ほか" if len(authors) > 3 else ""
    return "、".join(names) + suffix if names else "(不明)"


async def _upsert_unit(session: AsyncSession, set_id: str, unit: TranslatedUnit) -> None:
    stmt = pg_insert(TranslationUnit).values(
        set_id=set_id,
        block_id=unit.block_id,
        source_hash=unit.source_hash,
        content_ja=unit.content_ja,
        text_ja=unit.text_ja,
        state=unit.db_state(),
        quality_flags=unit.quality_flags,
        model=unit.model,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_translation_units_set_block",
        set_={
            "source_hash": stmt.excluded.source_hash,
            "content_ja": stmt.excluded.content_ja,
            "text_ja": stmt.excluded.text_ja,
            "state": stmt.excluded.state,
            "quality_flags": stmt.excluded.quality_flags,
            "model": stmt.excluded.model,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def _refresh_set_status(
    session: AsyncSession, tset: TranslationSet, content: DocumentContent
) -> tuple[str, int]:
    """セット状態(pending/partial/complete)と進捗率を再計算する(§13.1)。"""
    scope = compute_translation_scope(content)
    in_scope = set(scope.in_scope_block_ids)
    rows = (
        await session.execute(
            select(TranslationUnit.block_id, TranslationUnit.quality_flags).where(
                TranslationUnit.set_id == tset.id
            )
        )
    ).all()
    total_units = len(rows)
    scoped = [{"block_id": bid, "quality_flags": flags} for (bid, flags) in rows if bid in in_scope]
    covered = {u["block_id"] for u in scoped}
    if in_scope and covered >= in_scope:
        status = "complete"
    elif total_units > 0:
        status = "partial"
    else:
        status = "pending"
    tset.status = status
    return status, compute_progress(scoped, len(in_scope))


def _translatable_block_ids(section: Section) -> list[str]:
    return [b.id for b in section.blocks if b.type in TRANSLATABLE_BLOCK_TYPES]


def _task_for_section_reason(reason: str) -> str:
    """明示的な失敗再試行は通常翻訳より強いルートに送る。"""
    return "retranslation_escalation" if reason == "retry_failed" else "translation"


async def translate_section(
    session: AsyncSession,
    translation_set_id: str,
    section_id: str,
    router: LLMRouter,
    *,
    block_ids: list[str] | None = None,
    reason: str = "initial",
    instruction: str = "",
    user_id: str | None = None,
    library_item_id: str | None = None,
    job_id: str | None = None,
    job_store: Any | None = None,
    publish: Callable[[dict[str, Any]], Any] | None = None,
) -> SectionResult:
    """セクション内の翻訳対象ブロックをバッチ翻訳し translation_units へ UPSERT する。

    冪等: 既訳(``edited``/``protected`` は無条件、``machine`` は ``source_hash`` 一致)を
    スキップする(§3.3)。バッチ確定ごとに進捗を ``jobs`` へ反映し(``job_store``)、
    ``publish`` があれば SSE イベント相当を発行する(§13.2)。
    """
    tset = await session.get(TranslationSet, translation_set_id)
    if tset is None:
        raise LookupError(f"translation_set not found: {translation_set_id}")
    revision = await session.get(DocumentRevision, tset.revision_id)
    if revision is None:
        raise LookupError(f"document_revision not found: {tset.revision_id}")
    content = _as_content(revision.content)
    paper = await session.get(Paper, revision.paper_id)
    snapshot = list(tset.glossary_snapshot or [])
    ghash = glossary_hash(snapshot)

    section = _find_section(content, section_id)
    if section is None:
        raise LookupError(f"section not found: {section_id}")

    if block_ids is None:
        scope = compute_translation_scope(content)
        section_map = {s["section_id"]: s["block_ids"] for s in scope.sections}
        block_ids = section_map.get(section_id) or _translatable_block_ids(section)

    blocks_by_id = {b.id: b for b in section.blocks}
    encoded_by_id = {
        bid: encode_block(blocks_by_id[bid].model_dump())
        for bid in block_ids
        if bid in blocks_by_id
    }

    existing = {
        row.block_id: row
        for row in (
            await session.execute(select(TranslationUnit).where(TranslationUnit.set_id == tset.id))
        ).scalars()
    }

    items: list[BlockToTranslate] = []
    skipped = 0
    for bid in block_ids:
        encoded = encoded_by_id.get(bid)
        if encoded is None:
            continue
        ex = existing.get(bid)
        if ex is not None:
            if ex.state in ("edited", "protected"):
                skipped += 1
                continue
            blocking = bool(set(ex.quality_flags or []) & BLOCKING_FLAGS)
            if ex.state == "machine" and ex.source_hash == encoded.source_hash and not blocking:
                skipped += 1
                continue
        items.append(BlockToTranslate(encoded=encoded, block_type=blocks_by_id[bid].type))

    # 文脈: セクション内翻訳可能ブロックの並び(既訳含む)。
    context_order = [bid for bid in block_ids if bid in encoded_by_id]
    src_text = {bid: encoded_by_id[bid].text[:CONTEXT_TRUNCATE_CHARS] for bid in context_order}
    ja_text = {bid: ex.text_ja for bid, ex in existing.items() if bid in encoded_by_id}

    system_preamble = build_system_preamble(tset.style)
    paper_context = build_paper_context(
        title=paper.title if paper else "",
        authors_short=_authors_short(paper.authors if paper else []),
        profile_text=field_profile(paper.arxiv_categories if paper else []),
        toc_outline=_toc_outline(content),
        glossary_lines=format_glossary_lines(snapshot),
    )
    section_path = _section_path_display(content, section_id)

    translated = 0
    fallback = 0
    progress_pct = 0
    for batch in make_batches(items):
        first_idx = context_order.index(batch[0].encoded.block_id)
        last_idx = context_order.index(batch[-1].encoded.block_id)
        prev_ids = context_order[max(0, first_idx - CONTEXT_PREV_BLOCKS) : first_idx]
        next_id = context_order[last_idx + 1] if last_idx + 1 < len(context_order) else None
        ctx = TranslationContext(
            style=tset.style,
            snapshot=snapshot,
            revision_id=tset.revision_id,
            glossary_hash=ghash,
            system_preamble=system_preamble,
            paper_context=paper_context,
            section_path_display=section_path,
            prev_source_blocks=[src_text[i] for i in prev_ids],
            prev_translations=[ja_text[i] for i in prev_ids if i in ja_text],
            next_source_block=src_text.get(next_id) if next_id else None,
            reason=reason,
            instruction=instruction,
            task=_task_for_section_reason(reason),
        )
        units = await translate_batch(
            router,
            batch,
            ctx,
            user_id=user_id,
            library_item_id=library_item_id,
            job_id=job_id,
        )
        units = await _retry_blocking_units(
            router,
            batch,
            ctx,
            units,
            user_id=user_id,
            library_item_id=library_item_id,
            job_id=job_id,
        )
        for u in units:
            await _upsert_unit(session, tset.id, u)
            ja_text[u.block_id] = u.text_ja
            if u.state == "source_fallback":
                fallback += 1
            else:
                translated += 1
        await session.commit()

        _, progress_pct = await _refresh_set_status(session, tset, content)
        await session.commit()
        if job_store is not None and job_id is not None:
            await job_store.set_progress(job_id, progress_pct)
        if publish is not None:
            await publish(
                {
                    "type": "translation.unit_completed",
                    "library_item_id": library_item_id,
                    "translation_set_id": str(tset.id),
                    "block_ids": [u.block_id for u in units],
                    "total_progress": progress_pct,
                }
            )

    status, progress_pct = await _refresh_set_status(session, tset, content)
    await session.commit()
    if job_store is not None and job_id is not None:
        await job_store.checkpoint(
            job_id, f"section:{section_id}", {"block_ids": block_ids}, progress=progress_pct
        )

    return SectionResult(
        section_id=section_id,
        translated=translated,
        fallback=fallback,
        skipped=skipped,
        block_ids=block_ids,
        set_status=status,
        progress_pct=progress_pct,
    )
