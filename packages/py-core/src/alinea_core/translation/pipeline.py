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

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Annotated, Any, Literal

from alinea_llm.errors import ErrorKind, ProviderChainExhausted
from alinea_llm.router import LLMRouter
from alinea_llm.types import ContentPart, JsonSchemaSpec, LLMRequest, Message
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
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
from alinea_core.text_safety import sanitize_json_text, sanitize_untrusted_text
from alinea_core.translation.glossary import format_glossary_lines, glossary_hash
from alinea_core.translation.placeholder import (
    TOKEN_RE,
    EncodedBlock,
    compute_source_hash,
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
from alinea_core.translation.table_cells import (
    CanonicalTableCell,
    CanonicalTableGrid,
    TableTranslationContent,
    parse_table_grid,
    table_cells_complete,
    validate_table_translation_content,
)

# --- 確定値(plans/06 §3.3・§6・§12) --------------------------------------------

BATCH_MAX_BLOCKS = 8
BATCH_MAX_SOURCE_TOKENS = 2800
MAX_OUTPUT_TOKENS = 4096
MAX_RETRIES = 2  # 初回 + 再試行 2 回 = 計 3 回(docs/03 §4)
CONTEXT_PREV_BLOCKS = 2
CONTEXT_TRUNCATE_CHARS = 600
MAX_TRANSLATION_PLAN_SECTION_IDS = 20_000
MAX_TRANSLATION_PLAN_BLOCK_IDS = 200_000
MAX_TRANSLATION_PLAN_ID_LENGTH = 1_024
MAX_TRANSLATION_PLAN_PAGES = 2_000

# 自動翻訳対象のブロック型(docs/03 §2・plans/06 §2.1)。
TRANSLATABLE_BLOCK_TYPES: frozenset[str] = frozenset(
    {"paragraph", "heading", "figure", "table", "list", "quote", "theorem", "footnote"}
)

# 訳の配信を止める(API が text_ja: null で返す)3 フラグ(plans/06 §12・§16-4)。
BLOCKING_FLAGS: frozenset[str] = frozenset(
    {"placeholder_mismatch", "provider_refusal", "context_overflow"}
)

_STRUCTURED_SCHEMA_NAME = "translation_batch_v1"
_APPENDIX_HEADING_BOUNDARY = r"(?=$|[\s.,、:\u2013\u2014-])"
_APPENDIX_TITLE_RE = re.compile(
    rf"^\s*(?:"
    rf"Appendices{_APPENDIX_HEADING_BOUNDARY}|"
    rf"Appendix(?:(?:\s+[A-Za-z0-9]+)|[A-Z0-9])?{_APPENDIX_HEADING_BOUNDARY}|"
    rf"(?:付録|附録)(?:\s*[A-Za-z0-9一二三四五六七八九十])?"
    rf"{_APPENDIX_HEADING_BOUNDARY}"
    rf")",
    re.IGNORECASE,
)
_ROMAN_ROOT_RE = re.compile(
    r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})",
    re.IGNORECASE,
)
_REFERENCE_TITLE_RE = re.compile(
    r"(?:references|bibliography|works cited|literature cited|references and notes|"
    r"参考文献|引用文献)[.:。]?"
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
    num = unicodedata.normalize("NFKC", number or "").strip()
    normalized_title = unicodedata.normalize("NFKC", title or "")
    if _APPENDIX_TITLE_RE.match(normalized_title):
        return True
    root = num.split(".", 1)[0].upper()
    # タイトル根拠がない 1..3999 の Roman numeral は本文。その他 A/B.1 等は付録。
    if root and _ROMAN_ROOT_RE.fullmatch(root):
        return False
    if re.fullmatch(r"[A-Z]", root):
        return True
    return False


def is_reference_section(section: Section) -> bool:
    """reference_entry のみを含むセクション(§2.1-2)。"""
    title = " ".join(unicodedata.normalize("NFKC", section.heading.title or "").split()).casefold()
    if _REFERENCE_TITLE_RE.fullmatch(title):
        return True
    blocks = [b for b in section.blocks if b.type != "heading"]
    return bool(blocks) and all(b.type == "reference_entry" for b in blocks)


def _as_content(content: DocumentContent | dict[str, Any]) -> DocumentContent:
    if isinstance(content, DocumentContent):
        return content
    return DocumentContent.model_validate(content)


def _validate_unique_document_ids(content: DocumentContent) -> None:
    """Fail closed when revision-global section/block identifiers are ambiguous."""
    section_ids: set[str] = set()
    block_ids: set[str] = set()

    def walk(section: Section) -> None:
        if len(section.id) > MAX_TRANSLATION_PLAN_ID_LENGTH:
            raise ValueError(f"section id exceeds {MAX_TRANSLATION_PLAN_ID_LENGTH} characters")
        if len(section_ids) >= MAX_TRANSLATION_PLAN_SECTION_IDS:
            raise ValueError(
                f"document exceeds {MAX_TRANSLATION_PLAN_SECTION_IDS} section identifiers"
            )
        if section.id in section_ids:
            raise ValueError(f"duplicate section id: {section.id}")
        section_ids.add(section.id)
        for block in section.blocks:
            if len(block.id) > MAX_TRANSLATION_PLAN_ID_LENGTH:
                raise ValueError(f"block id exceeds {MAX_TRANSLATION_PLAN_ID_LENGTH} characters")
            if len(block_ids) >= MAX_TRANSLATION_PLAN_BLOCK_IDS:
                raise ValueError(
                    f"document exceeds {MAX_TRANSLATION_PLAN_BLOCK_IDS} block identifiers"
                )
            if block.id in block_ids:
                raise ValueError(f"duplicate block id: {block.id}")
            block_ids.add(block.id)
        for child in section.sections:
            walk(child)

    for section in content.sections:
        walk(section)


def compute_translation_scope(
    content: DocumentContent | dict[str, Any],
    *,
    include_appendix: bool = True,
) -> ScopeResult:
    """自動翻訳対象ブロックを決定する(plans/06 §2.1・docs/03 §2)。

    対象条件: (1) ブロック型が翻訳対象、(2) 参考文献セクションでない、
    (3) 付録は既定で対象、明示 opt-out 時だけ除外。equation / code / algorithm /
    reference_entry は常に対象外。判定は決定的で ``block_search_index`` 再生成時にも
    同値になる。
    """
    doc = _as_content(content)
    _validate_unique_document_ids(doc)
    in_scope: list[str] = []
    sections: list[dict[str, Any]] = []
    appendix_ids: list[str] = []
    reference_ids: list[str] = []

    def walk(section: Section, under_appendix: bool, under_reference: bool) -> None:
        is_appendix = under_appendix or _is_appendix_heading(
            section.heading.number, section.heading.title
        )
        own_reference = is_reference_section(section)
        is_reference = under_reference or own_reference
        if is_reference:
            reference_ids.append(section.id)
        elif is_appendix:
            appendix_ids.append(section.id)
        own: list[str] = []
        if not is_reference and (include_appendix or not is_appendix):
            for blk in section.blocks:
                if blk.type in TRANSLATABLE_BLOCK_TYPES:
                    in_scope.append(blk.id)
                    own.append(blk.id)
        if own:
            sections.append({"section_id": section.id, "block_ids": own})
        for sub in section.sections:
            walk(sub, is_appendix, is_reference)

    for top in doc.sections:
        walk(top, False, False)
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

    ``auto_translate_appendix`` は 4f トグル「付録を自動翻訳しない」の反転。
    全文翻訳が既定なので未指定時は ``True``、明示 ``False`` のみ opt-out とする。
    """

    default_style: str = "natural"
    auto_translate_appendix: bool = True
    translate_table_cells: bool = True
    suggest_section_selection_over_30_pages: bool = False

    @classmethod
    def from_user_settings(cls, settings: Mapping[str, Any] | None) -> TranslationSettings:
        t = (settings or {}).get("translation", {}) if settings else {}
        return cls(
            default_style=str(t.get("default_style", "natural")),
            auto_translate_appendix=bool(t.get("auto_translate_appendix", True)),
            translate_table_cells=bool(t.get("translate_table_cells", True)),
            suggest_section_selection_over_30_pages=bool(
                t.get("suggest_section_selection_over_30_pages", False)
            ),
        )


_TranslationPlanId = Annotated[
    str,
    Field(min_length=1, max_length=MAX_TRANSLATION_PLAN_ID_LENGTH),
]


class TranslationPlan(BaseModel):
    """A persisted, revision-local translation target contract."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    include_appendix: bool
    translate_table_cells: bool
    suggest_section_selection_over_30_pages: bool
    target_section_ids: list[_TranslationPlanId] = Field(
        max_length=MAX_TRANSLATION_PLAN_SECTION_IDS
    )
    target_block_ids: list[_TranslationPlanId] = Field(max_length=MAX_TRANSLATION_PLAN_BLOCK_IDS)
    auxiliary_block_ids: list[_TranslationPlanId] = Field(
        default_factory=list,
        max_length=MAX_TRANSLATION_PLAN_BLOCK_IDS,
    )
    pages: int | None = Field(ge=0, le=MAX_TRANSLATION_PLAN_PAGES)

    @model_validator(mode="after")
    def _validate_block_target_partition(self) -> TranslationPlan:
        if len(self.target_block_ids) + len(self.auxiliary_block_ids) > (
            MAX_TRANSLATION_PLAN_BLOCK_IDS
        ):
            raise ValueError(
                f"combined translation targets exceed {MAX_TRANSLATION_PLAN_BLOCK_IDS}"
            )
        if set(self.target_block_ids) & set(self.auxiliary_block_ids):
            raise ValueError("primary and auxiliary translation targets overlap")
        return self


def build_translation_plan(
    content: DocumentContent | dict[str, Any],
    settings: TranslationSettings,
    *,
    pages: int | None,
) -> TranslationPlan:
    """Build the exact target plan used for a new translation set."""
    scope = compute_translation_scope(
        content,
        include_appendix=settings.auto_translate_appendix,
    )
    return TranslationPlan(
        include_appendix=settings.auto_translate_appendix,
        translate_table_cells=settings.translate_table_cells,
        suggest_section_selection_over_30_pages=(settings.suggest_section_selection_over_30_pages),
        target_section_ids=[str(section["section_id"]) for section in scope.sections],
        target_block_ids=list(scope.in_scope_block_ids),
        pages=pages,
    )


def build_ingest_translation_plan(
    content: DocumentContent | dict[str, Any],
    settings: TranslationSettings,
    *,
    pages: int | None,
) -> TranslationPlan:
    """Build an initial plan, deferring body work only for an opted-in long paper."""

    safe_pages = pages if type(pages) is int and 0 <= pages <= MAX_TRANSLATION_PLAN_PAGES else None
    plan = build_translation_plan(content, settings, pages=safe_pages)
    if not (
        settings.suggest_section_selection_over_30_pages
        and safe_pages is not None
        and safe_pages > 30
        and plan.target_section_ids
    ):
        return plan
    return TranslationPlan(
        version=plan.version,
        include_appendix=plan.include_appendix,
        translate_table_cells=plan.translate_table_cells,
        suggest_section_selection_over_30_pages=True,
        target_section_ids=[],
        target_block_ids=[],
        auxiliary_block_ids=[],
        pages=plan.pages,
    )


def selectable_translation_section_ids(
    content: DocumentContent | dict[str, Any],
    plan: TranslationPlan,
) -> list[str]:
    """Return exact selectable section IDs under the pending plan's appendix policy."""

    scope = compute_translation_scope(content, include_appendix=plan.include_appendix)
    return [str(section["section_id"]) for section in scope.sections]


def translation_plan_awaits_section_selection(
    content: DocumentContent | dict[str, Any],
    plan: TranslationPlan,
) -> bool:
    """Whether a strict plan represents a real, still-unanswered long-paper proposal."""

    return bool(
        plan.suggest_section_selection_over_30_pages
        and plan.pages is not None
        and plan.pages > 30
        and not plan.target_section_ids
        and not plan.target_block_ids
        and not plan.auxiliary_block_ids
        and selectable_translation_section_ids(content, plan)
    )


def select_translation_plan_sections(
    content: DocumentContent | dict[str, Any],
    pending_plan: TranslationPlan,
    section_ids: Sequence[str],
) -> TranslationPlan:
    """Resolve a pending proposal into a bounded, canonical, non-empty primary plan."""

    if not translation_plan_awaits_section_selection(content, pending_plan):
        raise ValueError("translation plan is not awaiting section selection")
    if isinstance(section_ids, str | bytes) or not section_ids:
        raise ValueError("section selection must contain at least one section")
    if len(section_ids) > MAX_TRANSLATION_PLAN_SECTION_IDS:
        raise ValueError("section selection has too many section ids")
    requested: set[str] = set()
    for section_id in section_ids:
        if not isinstance(section_id, str) or not (
            0 < len(section_id) <= MAX_TRANSLATION_PLAN_ID_LENGTH
        ):
            raise ValueError("section selection contains an invalid section id")
        if section_id in requested:
            raise ValueError("section selection contains duplicate section ids")
        requested.add(section_id)

    scope = compute_translation_scope(
        content,
        include_appendix=pending_plan.include_appendix,
    )
    selectable = {str(section["section_id"]) for section in scope.sections}
    if not requested <= selectable:
        raise ValueError("section selection contains a section that is not selectable")

    target_section_ids: list[str] = []
    target_block_ids: list[str] = []
    for section in scope.sections:
        section_id = str(section["section_id"])
        if section_id not in requested:
            continue
        target_section_ids.append(section_id)
        target_block_ids.extend(str(block_id) for block_id in section["block_ids"])
    return TranslationPlan(
        version=pending_plan.version,
        include_appendix=pending_plan.include_appendix,
        translate_table_cells=pending_plan.translate_table_cells,
        suggest_section_selection_over_30_pages=(
            pending_plan.suggest_section_selection_over_30_pages
        ),
        target_section_ids=target_section_ids,
        target_block_ids=target_block_ids,
        auxiliary_block_ids=[],
        pages=pending_plan.pages,
    )


def _canonical_plan_targets(
    scope: ScopeResult, target_block_ids: Iterable[str]
) -> tuple[list[str], list[str]]:
    requested = set(target_block_ids)
    section_ids: list[str] = []
    block_ids: list[str] = []
    for section in scope.sections:
        selected = [str(block_id) for block_id in section["block_ids"] if block_id in requested]
        if selected:
            section_ids.append(str(section["section_id"]))
            block_ids.extend(selected)
    return section_ids, block_ids


def _raw_translation_plan_within_limits(payload: Any) -> bool:
    """Cheaply reject untrusted JSON before Pydantic builds errors/copies large lists."""
    if not isinstance(payload, Mapping) or len(payload) > 8:
        return False
    version = payload.get("version")
    if type(version) is not int or version != 1:
        return False
    for field_name in (
        "include_appendix",
        "translate_table_cells",
        "suggest_section_selection_over_30_pages",
    ):
        if type(payload.get(field_name)) is not bool:
            return False
    sections = payload.get("target_section_ids")
    blocks = payload.get("target_block_ids")
    auxiliary = payload.get("auxiliary_block_ids", [])
    if not isinstance(sections, list) or len(sections) > MAX_TRANSLATION_PLAN_SECTION_IDS:
        return False
    if not isinstance(blocks, list) or len(blocks) > MAX_TRANSLATION_PLAN_BLOCK_IDS:
        return False
    if not isinstance(auxiliary, list) or len(auxiliary) > MAX_TRANSLATION_PLAN_BLOCK_IDS:
        return False
    if len(blocks) + len(auxiliary) > MAX_TRANSLATION_PLAN_BLOCK_IDS:
        return False
    for identifier in sections:
        if not isinstance(identifier, str) or not (
            0 < len(identifier) <= MAX_TRANSLATION_PLAN_ID_LENGTH
        ):
            return False
    for identifier in blocks:
        if not isinstance(identifier, str) or not (
            0 < len(identifier) <= MAX_TRANSLATION_PLAN_ID_LENGTH
        ):
            return False
    for identifier in auxiliary:
        if not isinstance(identifier, str) or not (
            0 < len(identifier) <= MAX_TRANSLATION_PLAN_ID_LENGTH
        ):
            return False
    pages = payload.get("pages")
    return pages is None or (type(pages) is int and 0 <= pages <= MAX_TRANSLATION_PLAN_PAGES)


def resolve_translation_plan(
    content: DocumentContent | dict[str, Any],
    raw_plan: TranslationPlan | Mapping[str, Any] | None,
    *,
    pages: int | None,
) -> TranslationPlan:
    """Validate a stored plan, falling back to the safe default full scope."""
    fallback = build_translation_plan(content, TranslationSettings(), pages=pages)
    if raw_plan is None:
        return fallback
    if isinstance(raw_plan, TranslationPlan):
        plan = raw_plan
    else:
        if not _raw_translation_plan_within_limits(raw_plan):
            return fallback
        try:
            plan = TranslationPlan.model_validate(raw_plan)
        except ValidationError:
            return fallback

    full_scope = compute_translation_scope(content)
    if len(plan.target_section_ids) != len(set(plan.target_section_ids)):
        return fallback
    if len(plan.target_block_ids) != len(set(plan.target_block_ids)):
        return fallback
    if len(plan.auxiliary_block_ids) != len(set(plan.auxiliary_block_ids)):
        return fallback
    if set(plan.target_block_ids) & set(plan.auxiliary_block_ids):
        return fallback
    canonical_sections, canonical_blocks = _canonical_plan_targets(
        full_scope, plan.target_block_ids
    )
    if canonical_sections != plan.target_section_ids or canonical_blocks != plan.target_block_ids:
        return fallback
    _auxiliary_sections, canonical_auxiliary = _canonical_plan_targets(
        full_scope,
        plan.auxiliary_block_ids,
    )
    if canonical_auxiliary != plan.auxiliary_block_ids:
        return fallback
    if not plan.include_appendix:
        main_ids = set(
            compute_translation_scope(content, include_appendix=False).in_scope_block_ids
        )
        if not set(plan.target_block_ids) <= main_ids:
            return fallback
    return plan


def translation_scope_from_plan(
    content: DocumentContent | dict[str, Any],
    raw_plan: TranslationPlan | Mapping[str, Any] | None,
    *,
    pages: int | None = None,
) -> ScopeResult:
    """Return a canonical scope filtered to a validated persisted plan."""
    plan = resolve_translation_plan(content, raw_plan, pages=pages)
    full_scope = compute_translation_scope(content)
    return _scope_from_block_ids(full_scope, plan.target_block_ids)


def _scope_from_block_ids(full_scope: ScopeResult, requested_ids: Iterable[str]) -> ScopeResult:
    section_ids, block_ids = _canonical_plan_targets(full_scope, requested_ids)
    block_set = set(block_ids)
    section_set = set(section_ids)
    sections = [
        {
            "section_id": str(section["section_id"]),
            "block_ids": [block_id for block_id in section["block_ids"] if block_id in block_set],
        }
        for section in full_scope.sections
        if section["section_id"] in section_set
    ]
    return ScopeResult(
        in_scope_block_ids=block_ids,
        sections=sections,
        appendix_section_ids=list(full_scope.appendix_section_ids),
        reference_section_ids=list(full_scope.reference_section_ids),
    )


def translation_execution_scope_from_plan(
    content: DocumentContent | dict[str, Any],
    raw_plan: TranslationPlan | Mapping[str, Any] | None,
    *,
    pages: int | None = None,
) -> ScopeResult:
    """Return canonical primary plus auxiliary block-translation work."""
    plan = resolve_translation_plan(content, raw_plan, pages=pages)
    full_scope = compute_translation_scope(content)
    return _scope_from_block_ids(
        full_scope,
        [*plan.target_block_ids, *plan.auxiliary_block_ids],
    )


def merge_translation_plans(
    content: DocumentContent | dict[str, Any],
    existing_raw_plan: TranslationPlan | Mapping[str, Any] | None,
    requested_raw_plan: TranslationPlan | Mapping[str, Any],
    *,
    pages: int | None = None,
) -> TranslationPlan:
    """Monotonically union a reused set's targets in canonical document order."""
    existing = resolve_translation_plan(content, existing_raw_plan, pages=pages)
    requested = resolve_translation_plan(content, requested_raw_plan, pages=pages)
    full_scope = compute_translation_scope(content)
    target_ids = set(existing.target_block_ids) | set(requested.target_block_ids)
    section_ids, block_ids = _canonical_plan_targets(full_scope, target_ids)
    auxiliary_ids = (
        set(existing.auxiliary_block_ids) | set(requested.auxiliary_block_ids)
    ) - target_ids
    _auxiliary_sections, auxiliary_block_ids = _canonical_plan_targets(
        full_scope,
        auxiliary_ids,
    )
    stored_pages = [
        value for value in (existing.pages, requested.pages, pages) if value is not None
    ]
    return TranslationPlan(
        include_appendix=existing.include_appendix or requested.include_appendix,
        translate_table_cells=(existing.translate_table_cells or requested.translate_table_cells),
        suggest_section_selection_over_30_pages=(
            existing.suggest_section_selection_over_30_pages
            and requested.suggest_section_selection_over_30_pages
        ),
        target_section_ids=section_ids,
        target_block_ids=block_ids,
        auxiliary_block_ids=auxiliary_block_ids,
        pages=max(stored_pages) if stored_pages else None,
    )


@dataclass
class InitialJobPlan:
    """初回全文翻訳で積むジョブ計画(plans/06 §2.2・§13.1)。"""

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

    未指定時は付録を含む全文を対象にし、明示 opt-out 時だけ付録を除外する。
    """
    scope = compute_translation_scope(
        content,
        include_appendix=settings.auto_translate_appendix,
    )
    section_ids = [s["section_id"] for s in scope.sections]
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
    # ``None`` means an ordinary block.  A list (including an empty list) means
    # one table block expanded into verified caption/cell pseudo-targets.
    table_targets: list[EncodedBlock] | None = None
    table_grid: CanonicalTableGrid | None = None
    table_cells_requested: bool = False
    table_preserve_caption: bool = False
    table_preserved_caption: list[dict[str, Any]] | None = None
    table_result_state: str = "machine"
    table_preserved_quality_flags: list[str] = Field(default_factory=list)


def _encoded_with_id(encoded: EncodedBlock, block_id: str) -> EncodedBlock:
    return encoded.model_copy(update={"block_id": block_id})


def _table_cell_inlines(cell: CanonicalTableCell) -> list[dict[str, Any]]:
    """Convert a canonical cell to placeholder-safe inlines, preserving math verbatim."""

    inlines: list[dict[str, Any]] = []
    cursor = 0
    for fragment in cell.math:
        position = cell.source.find(fragment, cursor)
        if position < 0:
            raise ValueError(f"canonical math fragment is absent from {cell.id}")
        if position > cursor:
            inlines.append({"t": "text", "v": cell.source[cursor:position]})
        # Keep delimiters in ``v`` so the table projection can reproduce the exact
        # source atom after placeholder verification.
        inlines.append({"t": "math_inline", "v": fragment})
        cursor = position + len(fragment)
    if cursor < len(cell.source):
        inlines.append({"t": "text", "v": cell.source[cursor:]})
    return inlines or [{"t": "text", "v": ""}]


def _bounded_utf8_digest(value: str, *, chunk_chars: int = 65_536) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    for offset in range(0, len(value), chunk_chars):
        chunk = value[offset : offset + chunk_chars].encode("utf-8")
        digest.update(chunk)
        byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def _prepare_table_item(block: Block, *, cells_requested: bool) -> BlockToTranslate:
    grid = parse_table_grid(block.raw)
    caption = [inline.model_dump(mode="json", exclude_none=True) for inline in block.caption]
    targets: list[EncodedBlock] = []
    caption_encoded = encode_block(caption)
    if caption and caption_encoded.text.strip():
        targets.append(_encoded_with_id(caption_encoded, f"{block.id}::caption"))
    if cells_requested and grid.supported:
        for row in grid.rows:
            for cell in row:
                if not cell.translatable:
                    continue
                cell_encoded = encode_block(_table_cell_inlines(cell))
                targets.append(_encoded_with_id(cell_encoded, f"{block.id}::{cell.id}"))

    unsupported_raw: dict[str, str | int] | None = None
    if not grid.supported:
        raw_digest, raw_bytes = _bounded_utf8_digest(block.raw or "")
        unsupported_raw = {
            "sha256": raw_digest,
            "bytes": raw_bytes,
        }
    canonical_source = {
        "kind": "table-source",
        "version": 1,
        "caption": caption,
        "grid": grid.model_dump(mode="json"),
        "cells_requested": cells_requested,
        # Distinguish unsupported sources without embedding/duplicating an unbounded raw value.
        "unsupported_raw": unsupported_raw,
    }
    serialized = json.dumps(
        canonical_source,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    aggregate = EncodedBlock(
        block_id=block.id,
        text="\n".join(target.text for target in targets),
        tokens=[],
        source_hash=compute_source_hash(serialized, []),
    )
    return BlockToTranslate(
        encoded=aggregate,
        block_type="table",
        table_targets=targets,
        table_grid=grid,
        table_cells_requested=cells_requested,
    )


def _preserve_manual_table_caption(
    item: BlockToTranslate,
    existing: Any,
) -> BlockToTranslate:
    raw_content = existing.content_ja
    try:
        if isinstance(raw_content, list):
            typed = TableTranslationContent(
                kind="table",
                version=1,
                caption=raw_content,
                cells=None,
            )
        else:
            typed = TableTranslationContent.model_validate(raw_content)
    except ValidationError as exc:
        raise ValueError("manual table caption has an invalid persisted contract") from exc
    targets = [
        target for target in (item.table_targets or []) if not target.block_id.endswith("::caption")
    ]
    return item.model_copy(
        update={
            "table_targets": targets,
            "table_preserve_caption": True,
            "table_preserved_caption": typed.caption,
            "table_result_state": str(existing.state),
            "table_preserved_quality_flags": [
                flag for flag in (existing.quality_flags or []) if flag not in BLOCKING_FLAGS
            ],
        }
    )


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

    @model_validator(mode="after")
    def _sanitize_untrusted_model_text(self) -> TranslatedUnit:
        """Keep every translation persistence path safe for PostgreSQL and JSON."""

        self.content_ja = sanitize_json_text(self.content_ja)
        self.text_ja = sanitize_untrusted_text(self.text_ja)
        self.model = sanitize_untrusted_text(self.model)
        return self

    def db_state(self) -> str:
        return "machine" if self.state == "source_fallback" else self.state

    @property
    def is_displayable(self) -> bool:
        return not (set(self.quality_flags) & BLOCKING_FLAGS)


def translation_unit_satisfies_block(
    unit: Any,
    block: Block,
    *,
    require_table_cells: bool,
) -> bool:
    """Apply the shared display/reuse completeness rule for one source block."""

    if isinstance(unit, Mapping):
        quality_flags = unit.get("quality_flags", [])
    else:
        quality_flags = getattr(unit, "quality_flags", [])
    if set(quality_flags or []) & BLOCKING_FLAGS:
        return False
    return translation_unit_has_required_table_cells(
        unit,
        block,
        require_table_cells=require_table_cells,
    )


def translation_unit_has_required_table_cells(
    unit: Any,
    block: Block,
    *,
    require_table_cells: bool,
) -> bool:
    """Check only structural table-cell coverage, independent of quality flags."""

    if block.type != "table" or not require_table_cells:
        return True
    content_ja = unit.get("content_ja") if isinstance(unit, Mapping) else unit.content_ja
    return table_cells_complete(content_ja, parse_table_grid(block.raw))


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
    if item.block_type == "table_cell":
        text_ja = _table_inline_projection(content)
        source_content = normalize_inlines(decode_translation(item.encoded, item.encoded.text))
        source_plain = _table_inline_projection(source_content)
    else:
        text_ja = content_to_text_ja(content)
        source_plain = strip_tokens(item.encoded.text)
    flags = run_quality_checks(item.encoded, source_plain, text_ja, snapshot)
    return TranslatedUnit(
        block_id=item.encoded.block_id,
        source_hash=item.encoded.source_hash,
        content_ja=content,
        text_ja=text_ja,
        state="machine",
        quality_flags=flags,
        model=model,
    )


def _sanitize_table_cell_text(value: str) -> str:
    """Table cells/captions are single-line display atoms: collapse whitespace
    controls (``\\t\\n\\r``) to a space and drop any other C0/C1 control char so the
    strict typed-table contract (:func:`TableTranslationContent._validate_cells`)
    never has to fail closed on otherwise well-formed translated prose.

    保存側の ``_has_control`` は ``\\n`` 等の空白制御も Cc として拒否するため、モデルが
    セル内に改行/タブを混ぜると翻訳ジョブが 4 回リトライして terminal 失敗し、取り込み
    全体が止まる(実測: 2307.09288 の表セル)。単一行の表示原子であるセルでは改行/タブは
    ほぼ確実に不要な整形なので、空白へ正規化して翻訳結果を保つ(値の破棄より情報保存)。
    NUL/ESC 等の危険制御は従来通り除去される(セキュリティ意図は維持)。"""
    cleaned = "".join(
        " " if ch in "\t\n\r" else ch
        for ch in value
        if ch in "\t\n\r" or unicodedata.category(ch) != "Cc"
    )
    return " ".join(cleaned.split())


def _sanitize_caption_inlines(inlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize control chars out of caption inline string fields (v/ref/kind/href)
    and their emphasis children, mirroring :func:`_validate_inline`'s inspected keys.

    セル本文と同じく、モデル由来のキャプション文字列に混ざった改行/タブが
    ``TableTranslationContent`` 構築時に ``_validate_caption`` で terminal 失敗を招くのを防ぐ。"""
    result: list[dict[str, Any]] = []
    for inline in inlines:
        if not isinstance(inline, dict):
            result.append(inline)
            continue
        node = dict(inline)
        for key in ("v", "ref", "kind", "href"):
            value = node.get(key)
            if isinstance(value, str):
                node[key] = _sanitize_table_cell_text(value)
        children = node.get("children")
        if isinstance(children, list):
            node["children"] = _sanitize_caption_inlines(children)
        result.append(node)
    return result


def _table_inline_projection(inlines: list[dict[str, Any]]) -> str:
    """Project verified cell inlines without losing protected display atoms."""

    parts: list[str] = []
    for inline in inlines:
        tag = inline.get("t")
        if tag == "text":
            parts.append(str(inline.get("v", "")))
        elif tag == "emphasis":
            children = inline.get("children")
            if isinstance(children, list):
                parts.append(_table_inline_projection(children))
            else:
                parts.append(str(inline.get("v", "")))
        elif tag == "math_inline":
            value = str(inline.get("v", ""))
            if value.startswith(("$", r"\(", r"\[")):
                parts.append(value)
            elif value:
                parts.append(f"${value}$")
        elif tag == "citation":
            parts.append(f"[{inline.get('ref', '')}]")
        elif tag == "ref":
            parts.append(str(inline.get("v") or inline.get("ref") or ""))
        elif tag == "url":
            parts.append(str(inline.get("v") or inline.get("href") or ""))
        elif tag == "code_inline":
            parts.append(str(inline.get("v", "")))
        elif tag == "footnote_ref":
            reference = str(inline.get("ref") or "")
            if reference:
                parts.append(f"[{reference}]")
    return _sanitize_table_cell_text("".join(parts))


def _table_fallback_from_units(
    item: BlockToTranslate,
    target_units: list[TranslatedUnit],
) -> TranslatedUnit:
    failure_flag = "placeholder_mismatch"
    for flag in ("provider_refusal", "context_overflow", "placeholder_mismatch"):
        if any(flag in unit.quality_flags for unit in target_units):
            failure_flag = flag
            break
    model = next((unit.model for unit in target_units if unit.model), "")
    if not item.table_preserve_caption:
        return _fallback_unit(item, failure_flag, model)
    content = TableTranslationContent(
        kind="table",
        version=1,
        caption=item.table_preserved_caption,
        cells=None,
    ).model_dump(mode="json")
    return TranslatedUnit(
        block_id=item.encoded.block_id,
        source_hash=item.encoded.source_hash,
        content_ja=content,
        text_ja=(
            content_to_text_ja(item.table_preserved_caption)
            if item.table_preserved_caption is not None
            else ""
        ),
        state=item.table_result_state,
        quality_flags=[*item.table_preserved_quality_flags, failure_flag],
        model=model,
    )


def _aggregate_table_unit(
    item: BlockToTranslate,
    translated_by_id: Mapping[str, TranslatedUnit],
) -> TranslatedUnit:
    assert item.table_targets is not None
    assert item.table_grid is not None
    target_units = [
        translated_by_id[target.block_id]
        for target in item.table_targets
        if target.block_id in translated_by_id
    ]
    if len(target_units) != len(item.table_targets) or any(
        set(unit.quality_flags) & BLOCKING_FLAGS or unit.state == "source_fallback"
        for unit in target_units
    ):
        return _table_fallback_from_units(item, target_units)

    caption = item.table_preserved_caption if item.table_preserve_caption else None
    caption_id = f"{item.encoded.block_id}::caption"
    caption_unit = translated_by_id.get(caption_id)
    if caption_unit is not None:
        if not isinstance(caption_unit.content_ja, list):
            return _table_fallback_from_units(item, target_units)
        # モデル由来キャプションは制御文字を含み得るので構築前にサニタイズする(セルと同方針)。
        caption = _sanitize_caption_inlines(caption_unit.content_ja)

    cells: list[list[str | None]] | None = None
    if item.table_cells_requested and item.table_grid.supported:
        cells = [[None for _cell in row] for row in item.table_grid.rows]
        for row_index, row in enumerate(item.table_grid.rows):
            for cell_index, cell in enumerate(row):
                if not cell.translatable:
                    continue
                unit = translated_by_id.get(f"{item.encoded.block_id}::{cell.id}")
                if unit is None or not isinstance(unit.content_ja, list):
                    return _table_fallback_from_units(item, target_units)
                cells[row_index][cell_index] = _table_inline_projection(unit.content_ja)

    raw_content = TableTranslationContent(
        kind="table",
        version=1,
        caption=caption,
        cells=cells,
    ).model_dump(mode="json")
    content = validate_table_translation_content(raw_content, item.table_grid)
    if content is None:
        return _table_fallback_from_units(item, target_units)
    text_parts: list[str] = []
    if caption is not None:
        caption_text = content_to_text_ja(caption)
        if caption_text:
            text_parts.append(caption_text)
    if cells is not None:
        text_parts.extend(cell for row in cells for cell in row if cell)
    quality_flags = list(
        dict.fromkeys(
            [
                *item.table_preserved_quality_flags,
                *(flag for unit in target_units for flag in unit.quality_flags),
            ]
        )
    )
    return TranslatedUnit(
        block_id=item.encoded.block_id,
        source_hash=item.encoded.source_hash,
        content_ja=content.model_dump(mode="json"),
        text_ja="\n".join(text_parts),
        state=item.table_result_state,
        quality_flags=quality_flags,
        model=next((unit.model for unit in target_units if unit.model), ""),
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
    data = resp.parsed or {}
    entries = data.get("translations", [])
    expected_ids = [item.encoded.block_id for item in group]
    if not isinstance(entries, list):
        return {}, resp.model, resp.stop_reason
    ids: list[str] = []
    parsed: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            return {}, resp.model, resp.stop_reason
        target_id = entry.get("id")
        translated = entry.get("ja")
        if not isinstance(target_id, str) or not isinstance(translated, str):
            return {}, resp.model, resp.stop_reason
        ids.append(target_id)
        parsed[target_id] = translated
    # Structured output is a closed set.  Dict assignment alone would silently
    # accept duplicate IDs and ignore unknown targets.
    if len(ids) != len(set(ids)) or set(ids) != set(expected_ids) or len(ids) != len(expected_ids):
        return {}, resp.model, resp.stop_reason
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
        if exc.errors and all(e.kind == ErrorKind.SCHEMA_VALIDATION for e in exc.errors):
            # Native structured output can be truncated before the provider returns a
            # usable JSON object.  In that case the provider reports schema validation
            # rather than ``max_tokens``, so apply the same deterministic bisection used
            # for an explicit output-token stop instead of retrying the whole section.
            if len(group) > 1:
                mid = len(group) // 2
                left, left_model = await _attempt_group(
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
                right, right_model = await _attempt_group(
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
                return left + right, right_model or left_model
            # A single invalid structured response is a block-level failure, not a
            # section/job failure.  Feed it into the normal bounded retry loop and
            # ultimately persist the existing explicit source fallback if necessary.
            return list(group), ""
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


async def _translate_flat_batch(
    router: LLMRouter,
    items: list[BlockToTranslate],
    ctx: TranslationContext,
    *,
    user_id: str | None = None,
    library_item_id: str | None = None,
    job_id: str | None = None,
) -> list[TranslatedUnit]:
    """Translate an already-expanded batch with placeholder retries."""
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


async def translate_batch(
    router: LLMRouter,
    items: list[BlockToTranslate],
    ctx: TranslationContext,
    *,
    user_id: str | None = None,
    library_item_id: str | None = None,
    job_id: str | None = None,
) -> list[TranslatedUnit]:
    """Translate one block batch, atomically aggregating table pseudo-targets.

    A table may expand to many caption/cell targets, so the expanded stream is
    bounded again with the normal batch limits.  Only after every pseudo-target
    succeeds is one typed table unit returned.
    """

    expanded: list[BlockToTranslate] = []
    for item in items:
        if item.table_targets is None:
            expanded.append(item)
            continue
        for target in item.table_targets:
            target_type = "table_caption" if target.block_id.endswith("::caption") else "table_cell"
            expanded.append(BlockToTranslate(encoded=target, block_type=target_type))

    translated_by_id: dict[str, TranslatedUnit] = {}
    for sub_batch in make_batches(expanded):
        translated = await _translate_flat_batch(
            router,
            sub_batch,
            ctx,
            user_id=user_id,
            library_item_id=library_item_id,
            job_id=job_id,
        )
        translated_by_id.update((unit.block_id, unit) for unit in translated)

    results: list[TranslatedUnit] = []
    for item in items:
        if item.table_targets is None:
            results.append(translated_by_id[item.encoded.block_id])
        else:
            results.append(_aggregate_table_unit(item, translated_by_id))
    return results


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
    btype = block_type or str(block_dict.get("type", "paragraph"))
    if btype == "table":
        table_block = block if isinstance(block, Block) else Block.model_validate(block_dict)
        item = _prepare_table_item(table_block, cells_requested=True)
    else:
        item = BlockToTranslate(encoded=encode_block(block_dict), block_type=btype)
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


async def find_effective_set(
    session: AsyncSession,
    revision_id: str,
    style: str,
    user_id: str,
) -> TranslationSet | None:
    """Resolve a user's personal set first, then the shared set."""
    rows = (
        (
            await session.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == revision_id,
                    TranslationSet.style == style,
                    or_(
                        TranslationSet.scope == "shared",
                        TranslationSet.user_id == user_id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    personal = next((row for row in rows if row.scope == "personal"), None)
    return personal or next((row for row in rows if row.scope == "shared"), None)


def _is_valid_shared_base(tset: TranslationSet, base: TranslationSet | None) -> bool:
    return bool(
        tset.scope == "personal"
        and base is not None
        and base.scope == "shared"
        and base.user_id is None
        and base.base_set_id is None
        and str(base.revision_id) == str(tset.revision_id)
        and base.style == tset.style
    )


async def resolve_effective_translation_plan(
    session: AsyncSession,
    tset: TranslationSet,
    content: DocumentContent | dict[str, Any],
    *,
    pages: int | None = None,
) -> TranslationPlan:
    """Keep a personal primary denominator while inheriting valid base execution work."""
    personal = resolve_translation_plan(content, tset.plan, pages=pages)
    if tset.scope != "personal" or tset.base_set_id is None:
        return personal
    # A long-paper selection is deliberately user-specific. Importing the shared base plan here
    # would make every globally requested section appear requested for this user and defeat both
    # the chosen denominator and the selected-out section's on-demand action. Base *units* remain
    # inherited independently by ``resolve_translation_set_units``.
    if (
        personal.suggest_section_selection_over_30_pages
        and personal.pages is not None
        and personal.pages > 30
    ):
        return personal
    base = await session.get(TranslationSet, str(tset.base_set_id))
    if not _is_valid_shared_base(tset, base):
        return personal
    assert base is not None
    base_plan = resolve_translation_plan(content, base.plan, pages=pages)
    auxiliary_ids = (
        set(personal.auxiliary_block_ids)
        | set(base_plan.target_block_ids)
        | set(base_plan.auxiliary_block_ids)
    ) - set(personal.target_block_ids)
    full_scope = compute_translation_scope(content)
    _sections, auxiliary_block_ids = _canonical_plan_targets(full_scope, auxiliary_ids)
    return TranslationPlan(
        version=personal.version,
        include_appendix=personal.include_appendix,
        translate_table_cells=personal.translate_table_cells,
        suggest_section_selection_over_30_pages=(personal.suggest_section_selection_over_30_pages),
        target_section_ids=list(personal.target_section_ids),
        target_block_ids=list(personal.target_block_ids),
        auxiliary_block_ids=auxiliary_block_ids,
        pages=personal.pages,
    )


async def resolve_translation_set_units(
    session: AsyncSession,
    tset: TranslationSet,
) -> dict[str, TranslationUnit]:
    """Resolve one set's exact base units with its personal block-level overrides."""

    async def load(set_id: str) -> list[TranslationUnit]:
        return list(
            (
                await session.execute(
                    select(TranslationUnit)
                    .where(TranslationUnit.set_id == set_id)
                    .order_by(TranslationUnit.id)
                )
            ).scalars()
        )

    resolved: dict[str, TranslationUnit] = {}
    if tset.scope == "personal" and tset.base_set_id is not None:
        base = await session.get(TranslationSet, str(tset.base_set_id))
        if _is_valid_shared_base(tset, base):
            assert base is not None
            for unit in await load(str(base.id)):
                resolved[unit.block_id] = unit
    for unit in await load(str(tset.id)):
        resolved[unit.block_id] = unit
    return resolved


async def resolve_display_units(
    session: AsyncSession, revision_id: str, style: str, user_id: str
) -> dict[str, TranslationUnit]:
    """表示用 TranslationUnit を解決(plans/02 §5.2。personal 優先→shared)。"""
    tset = await find_effective_set(session, revision_id, style, user_id)
    return await resolve_translation_set_units(session, tset) if tset is not None else {}


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
    plan = resolve_translation_plan(content, tset.plan, pages=None)
    scope = translation_scope_from_plan(content, plan)
    in_scope = set(scope.in_scope_block_ids)
    units = await resolve_translation_set_units(session, tset)
    blocks = {block.id: block for _section, block in content.iter_blocks()}
    scoped = [
        {"block_id": block_id, "quality_flags": unit.quality_flags}
        for block_id, unit in units.items()
        if block_id in in_scope
        and block_id in blocks
        and translation_unit_has_required_table_cells(
            unit,
            blocks[block_id],
            require_table_cells=plan.translate_table_cells,
        )
    ]
    covered = {u["block_id"] for u in scoped}
    if not in_scope or covered >= in_scope:
        status = "complete"
    elif covered:
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
        if reason == "initial":
            scope = translation_scope_from_plan(content, tset.plan)
            section_map = {s["section_id"]: s["block_ids"] for s in scope.sections}
            block_ids = list(section_map.get(section_id, []))
        else:
            block_ids = []

    blocks_by_id = {b.id: b for b in section.blocks}
    if len(block_ids) != len(set(block_ids)):
        raise ValueError("duplicate block ids requested for section translation")
    translatable_section_ids = {
        block.id for block in section.blocks if block.type in TRANSLATABLE_BLOCK_TYPES
    }
    if not set(block_ids) <= translatable_section_ids:
        raise ValueError("requested block ids are not translatable members of the section")

    raw_pages = (revision.stats or {}).get("pages")
    pages = raw_pages if isinstance(raw_pages, int) and not isinstance(raw_pages, bool) else None
    stored_plan = resolve_translation_plan(content, tset.plan, pages=pages)
    effective_plan = stored_plan
    primary_scope = translation_scope_from_plan(content, stored_plan, pages=pages)
    if reason in {"on_demand", "table", "retry_failed"}:
        effective_plan = await resolve_effective_translation_plan(
            session,
            tset,
            content,
            pages=pages,
        )
        allowed_ids = set(
            translation_execution_scope_from_plan(
                content,
                effective_plan,
                pages=pages,
            ).in_scope_block_ids
        )
    else:
        allowed_ids = set(primary_scope.in_scope_block_ids)
    if not set(block_ids) <= allowed_ids:
        raise ValueError("requested block ids are outside the translation plan scope")

    cells_requested = reason == "table" or effective_plan.translate_table_cells
    prepared_by_id: dict[str, BlockToTranslate] = {}
    for bid in block_ids:
        block = blocks_by_id[bid]
        if block.type == "table":
            prepared_by_id[bid] = _prepare_table_item(
                block,
                cells_requested=cells_requested,
            )
        else:
            prepared_by_id[bid] = BlockToTranslate(
                encoded=encode_block(block.model_dump()),
                block_type=block.type,
            )
    encoded_by_id = {bid: item.encoded for bid, item in prepared_by_id.items()}

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
                prepared = prepared_by_id[bid]
                if (
                    prepared.table_targets is not None
                    and prepared.table_grid is not None
                    and prepared.table_cells_requested
                    and not table_cells_complete(ex.content_ja, prepared.table_grid)
                ):
                    prepared_by_id[bid] = _preserve_manual_table_caption(prepared, ex)
                else:
                    skipped += 1
                    continue
            blocking = bool(set(ex.quality_flags or []) & BLOCKING_FLAGS)
            if ex.state == "machine" and ex.source_hash == encoded.source_hash and not blocking:
                prepared = prepared_by_id[bid]
                if prepared.table_targets is None:
                    skipped += 1
                    continue
                assert prepared.table_grid is not None
                typed = validate_table_translation_content(ex.content_ja, prepared.table_grid)
                cells_ready = not prepared.table_cells_requested or table_cells_complete(
                    ex.content_ja,
                    prepared.table_grid,
                )
                if typed is not None and cells_ready:
                    skipped += 1
                    continue
        items.append(prepared_by_id[bid])

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
            if u.state == "source_fallback" or set(u.quality_flags) & BLOCKING_FLAGS:
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
