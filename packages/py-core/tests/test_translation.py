"""翻訳パイプラインのテスト(plans/06 §2-§13・docs/03)。

- 単体(DB 不要): 品質検査 5 種(PY-TR-03)・見出し原題併記(PY-TR-06)・スコープ判定・
  設定 4 項目の反映(PY-TR-07)・進捗写像・原文フォールバック(PY-TR-02 の in-memory)。
- 統合(実 PostgreSQL): translate_section の状態遷移と進捗分母(PY-TR-04)・原文フォールバック
  の永続化(PY-TR-02)・用語スナップショット(PY-TR-05)・共有キャッシュと personal マージ
  (PY-TR-10)。

LLM は決定的なスクリプトプロバイダ(:class:`_ScriptProvider`)を注入する(実通信なし)。
DB はユニーク UUID データで実 PostgreSQL に対して実行する(SQLite 代替禁止)。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import alinea_core.translation as translation_core
import pytest
from alinea_core.db.models import (
    DocumentRevision,
    Glossary,
    GlossaryTerm,
    LibraryItem,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import DocumentContent
from alinea_core.translation import (
    TranslationSettings,
    build_snapshot,
    compute_progress,
    compute_translation_scope,
    encode_block,
    find_shared_set,
    glossary_hash,
    heading_display,
    make_batches,
    plan_initial_translation,
    resolve_display_units,
    resolve_translation,
    run_quality_checks,
    translate_block,
    translate_section,
)
from alinea_core.translation.pipeline import (
    BlockToTranslate,
    TranslatedUnit,
    _refresh_set_status,
    strip_tokens,
)
from alinea_core.translation.placeholder import TOKEN_RE
from alinea_core.translation.prompts.templates import TranslationBatchOut
from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.router import LLMRouter
from alinea_llm.types import LLMRequest, LLMResponse, StreamEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# 決定的スクリプトプロバイダ(構造化出力の translation_batch_v1 を返す)
# ---------------------------------------------------------------------------

_TARGET_RE = re.compile(r"^\[([^\]]+)\] \(([^)]+)\) (.*)$", re.MULTILINE)


def test_translation_batch_schema_is_openai_strict_compatible() -> None:
    schema = TranslationBatchOut.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"translations"}
    translated_block = schema["$defs"]["TranslatedBlock"]
    assert translated_block["additionalProperties"] is False
    assert set(translated_block["required"]) == {"id", "ja"}


def test_translated_unit_removes_unsafe_model_control_characters() -> None:
    unit = TranslatedUnit(
        block_id="block-1",
        source_hash="hash-1",
        content_ja=[
            {"t": "text", "v": "安全\x00な本文\x11"},
            {"t": "emphasis", "children": [{"t": "text", "v": "強調\x02"}]},
        ],
        text_ja="安全\x00な本文\x11\n次の行",
        model="model\x03",
    )

    assert unit.content_ja == [
        {"t": "text", "v": "安全な本文"},
        {"t": "emphasis", "children": [{"t": "text", "v": "強調"}]},
    ]
    assert unit.text_ja == "安全な本文\n次の行"
    assert unit.model == "model"


def _echo_translate(encoded_text: str) -> str:
    """トークンを保ちつつ本文を固定の日本語に置換した有効訳(検証を通過する)。"""
    parts: list[str] = []
    pos = 0
    for m in TOKEN_RE.finditer(encoded_text):
        if encoded_text[pos : m.start()].strip():
            parts.append("これは訳文である。")
        parts.append(m.group(0))
        pos = m.end()
    if encoded_text[pos:].strip() or not parts:
        parts.append("これは訳文である。")
    return "".join(parts)


def _drop_tokens(_encoded_text: str) -> str:
    """トークンを含まない訳(トークンありブロックでは検証に必ず失敗する)。"""
    return "これは訳せない段落である。"


class _ScriptProvider:
    """LLMProvider 準拠の決定的 Fake。user メッセージの対象ブロックを transform で訳す。"""

    name = "fake"

    def __init__(self, transform: Any = _echo_translate) -> None:
        self.transform = transform
        self.calls = 0
        self.tasks: list[str] = []

    def _targets(self, req: LLMRequest) -> list[tuple[str, str, str]]:
        text = "".join(
            p.text or "" for msg in req.messages if msg.role == "user" for p in msg.parts
        )
        return _TARGET_RE.findall(text)

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        self.tasks.append(str(req.metadata.get("task", "")))
        translations = [
            {"id": bid, "ja": self.transform(txt)} for (bid, _t, txt) in self._targets(req)
        ]
        data = {"translations": translations}
        return LLMResponse(
            text=json.dumps(data, ensure_ascii=False),
            parsed=data,
            provider=self.name,
            model=req.model,
            stop_reason="end",
        )

    async def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(  # pragma: no cover
        self, req: LLMRequest
    ) -> AsyncIterator[StreamEvent]:
        raise NotImplementedError
        yield StreamEvent(type="end")  # 到達しない(async generator にするため)

    async def count_tokens(self, req: LLMRequest) -> int:  # pragma: no cover
        return 1


class _SchemaFailureOnMultiTargetProvider(_ScriptProvider):
    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        if len(self._targets(req)) > 1:
            self.calls += 1
            raise ProviderError(
                ErrorKind.SCHEMA_VALIDATION,
                self.name,
                req.model,
                "simulated truncated structured output",
            )
        return await super().generate_structured(req)


class _AlwaysSchemaFailureProvider(_ScriptProvider):
    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        raise ProviderError(
            ErrorKind.SCHEMA_VALIDATION,
            self.name,
            req.model,
            "simulated invalid structured output",
        )


def _router(transform: Any = _echo_translate) -> LLMRouter:
    return LLMRouter([("fake", "deepseek-v4-flash", _ScriptProvider(transform))])


# ---------------------------------------------------------------------------
# IR ヘルパ
# ---------------------------------------------------------------------------


def _id() -> str:
    return str(uuid.uuid4())


def _para(bid: str, text: str) -> dict[str, Any]:
    return {"id": bid, "type": "paragraph", "inlines": [{"t": "text", "v": text}]}


def _para_ref(bid: str, pre: str, ref: str, post: str) -> dict[str, Any]:
    return {
        "id": bid,
        "type": "paragraph",
        "inlines": [
            {"t": "text", "v": pre},
            {"t": "ref", "ref": ref, "kind": "equation", "v": ""},
            {"t": "text", "v": post},
        ],
    }


def _ref_entry(bid: str, raw: str) -> dict[str, Any]:
    return {"id": bid, "type": "reference_entry", "raw": raw}


def _equation(bid: str, latex: str) -> dict[str, Any]:
    return {"id": bid, "type": "equation", "latex": latex}


def _section(
    sid: str,
    number: str,
    title: str,
    blocks: list[dict[str, Any]],
    subsections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": sid,
        "heading": {"number": number, "title": title},
        "blocks": blocks,
        "sections": subsections or [],
    }


def _content(sections: list[dict[str, Any]]) -> dict[str, Any]:
    return {"quality_level": "A", "sections": sections}


def _content_with_main_appendices_and_references() -> dict[str, Any]:
    return _content(
        [
            _section("sec-main", "1", "Introduction", [_para("blk-main", "Main prose.")]),
            _section(
                "sec-appendix-en",
                "",
                "Appendix A: Details",
                [_para("blk-appendix-en", "English appendix prose.")],
                [
                    _section(
                        "sec-appendix-nested",
                        "A.1",
                        "Nested Details",
                        [_para("blk-appendix-nested", "Nested appendix prose.")],
                    ),
                    _section(
                        "sec-appendix-references",
                        "",
                        "参考文献",
                        [_para("blk-reference-nested", "Nested reference prose.")],
                        [
                            _section(
                                "sec-reference-deep",
                                "A.2",
                                "Sources",
                                [_para("blk-reference-deep", "Deep reference prose.")],
                            )
                        ],
                    ),
                ],
            ),
            _section(
                "sec-appendix-ja",
                "",
                "付録",
                [_para("blk-appendix-ja", "Japanese appendix prose.")],
            ),
            _section(
                "sec-references",
                "",
                "References",
                [_para("blk-reference-en", "Reference prose.")],
            ),
        ]
    )


async def _make_set(
    db: AsyncSession,
    *,
    content: dict[str, Any],
    style: str = "natural",
    snapshot: list[dict[str, Any]] | None = None,
    scope: str = "shared",
    user_id: str | None = None,
    base_set_id: str | None = None,
    categories: list[str] | None = None,
    plan: dict[str, Any] | None = None,
) -> tuple[Paper, DocumentRevision, TranslationSet]:
    paper = Paper(
        id=_id(),
        title="Rectified Flow",
        authors=[{"name": "X. Liu"}, {"name": "C. Gong"}],
        abstract="abstract",
        arxiv_categories=categories or ["cs.LG"],
        visibility="public",
    )
    db.add(paper)
    await db.flush()
    rev = DocumentRevision(
        id=_id(),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="arxiv_html",
        content=content,
        stats={},
    )
    db.add(rev)
    await db.flush()
    tset = TranslationSet(
        id=_id(),
        revision_id=rev.id,
        style=style,
        scope=scope,
        user_id=user_id,
        base_set_id=base_set_id,
        glossary_snapshot=snapshot or [],
        plan=plan,
        status="pending",
    )
    db.add(tset)
    await db.commit()
    return paper, rev, tset


# ===========================================================================
# PY-TR-06: 見出し原題併記(訳題 — 原題)
# ===========================================================================


def test_heading_display_formats() -> None:
    assert heading_display("1", "はじめに", "Introduction") == "1 はじめに — Introduction"
    assert (
        heading_display("2.1", "整流フロー", "Rectified Flow") == "2.1 整流フロー — Rectified Flow"
    )
    assert heading_display("", "アブストラクト", "Abstract") == "アブストラクト — Abstract"
    # 未訳(title_ja=None/空)は原題のみ
    assert heading_display("A", None, "Proof") == "Proof"
    assert heading_display("3", "", "Method") == "Method"


# ===========================================================================
# PY-TR-03: 自動品質検査(number/length/glossary/untranslated)+ 進捗写像
# ===========================================================================


def _encoded(block: dict[str, Any]):  # type: ignore[no-untyped-def]
    return encode_block(block)


def test_quality_number_mismatch() -> None:
    enc = _encoded(_para("b", "We report 3 results across 2 trials in total."))
    # 訳文に 3 のみ(2 が欠落)→ number_mismatch
    flags = run_quality_checks(enc, strip_tokens(enc.text), "全体で3件の結果を報告する。", [])
    assert "number_mismatch" in flags
    # 全数値一致(全角・桁区切りも正規化)→ フラグなし
    ok = run_quality_checks(
        enc, strip_tokens(enc.text), "全体で３件の結果を２回の試行で報告する。", []
    )
    assert "number_mismatch" not in ok


def test_quality_length_outlier() -> None:
    long_src = "This paragraph is intentionally written to exceed sixty characters in length."
    enc = _encoded(_para("b", long_src))
    assert len(strip_tokens(enc.text).replace(" ", "")) >= 60
    flags = run_quality_checks(enc, strip_tokens(enc.text), "短い。", [])
    assert "length_outlier" in flags
    # 60 文字未満は検査しない
    short = _encoded(_para("b", "Short input."))
    assert "length_outlier" not in run_quality_checks(short, strip_tokens(short.text), "え。", [])


def test_quality_glossary_violation() -> None:
    snap_translate = [
        {"source_term": "rectified flow", "target_term": "整流フロー", "policy": "translate"}
    ]
    enc = _encoded(_para("b", "We study rectified flow in this work."))
    # 訳語が無い → violation
    assert "glossary_violation" in run_quality_checks(
        enc, strip_tokens(enc.text), "本研究では整流を扱う。", snap_translate
    )
    # 訳語がある → violation なし
    assert "glossary_violation" not in run_quality_checks(
        enc, strip_tokens(enc.text), "本研究では整流フローを扱う。", snap_translate
    )
    # keep_original: 原語が残っていなければ violation
    snap_keep = [
        {"source_term": "rectified flow", "target_term": "整流フロー", "policy": "keep_original"}
    ]
    assert "glossary_violation" in run_quality_checks(
        enc, strip_tokens(enc.text), "本研究ではこれを扱う。", snap_keep
    )
    assert "glossary_violation" not in run_quality_checks(
        enc, strip_tokens(enc.text), "本研究では rectified flow を扱う。", snap_keep
    )


def test_quality_untranslated() -> None:
    enc = _encoded(_para("b", "This sentence has many english words remaining here."))
    # 英文そのまま(日本語比率 < 5%、語数 >= 4)→ untranslated
    assert "untranslated" in run_quality_checks(
        enc, strip_tokens(enc.text), "This sentence has many english words remaining here.", []
    )
    # 日本語訳 → フラグなし
    assert "untranslated" not in run_quality_checks(
        enc, strip_tokens(enc.text), "この文には多くの英単語が残っている。", []
    )


def test_compute_progress_maps_to_percent() -> None:
    units = [
        {"quality_flags": []},
        {"quality_flags": ["number_mismatch"]},  # 非ブロッキング → 分子に入る
        {"quality_flags": ["placeholder_mismatch"]},  # ブロッキング → 除外
        {"quality_flags": []},
    ]
    assert compute_progress(units, 4) == 75  # 3 表示可能 / 4
    assert compute_progress([], 0) == 100  # 分母 0 は 100
    assert compute_progress(units, 8) == 37  # floor(100*3/8)


# ===========================================================================
# スコープ判定(PY-TR-04 の分母・plans/06 §2.1)
# ===========================================================================


def test_default_scope_includes_appendix_but_excludes_references_and_nontext() -> None:
    content = _content(
        [
            _section(
                "sec-1",
                "1",
                "Introduction",
                [
                    _para("blk-a", "Body one."),
                    _equation("blk-eq", "x=y"),
                    _para("blk-b", "Body two."),
                ],
            ),
            _section(
                "sec-A", "A", "Proof of the Main Theorem", [_para("blk-app", "Appendix body.")]
            ),
            _section(
                "sec-ref",
                "",
                "References",
                [_ref_entry("blk-r1", "[1] ..."), _ref_entry("blk-r2", "[2] ...")],
            ),
            _section(
                "sec-ref-raw",
                "",
                "Bibliography",
                [_para("blk-r-raw", "[1] Wang, A. A reference. 2024.")],
            ),
        ]
    )
    scope = compute_translation_scope(content)
    assert scope.in_scope_block_ids == ["blk-a", "blk-b", "blk-app"]
    assert scope.appendix_section_ids == ["sec-A"]
    assert scope.reference_section_ids == ["sec-ref", "sec-ref-raw"]
    assert scope.sections == [
        {"section_id": "sec-1", "block_ids": ["blk-a", "blk-b"]},
        {"section_id": "sec-A", "block_ids": ["blk-app"]},
    ]


def test_default_scope_handles_nested_japanese_appendix_and_reference_headings() -> None:
    scope = compute_translation_scope(_content_with_main_appendices_and_references())

    assert scope.in_scope_block_ids == [
        "blk-main",
        "blk-appendix-en",
        "blk-appendix-nested",
        "blk-appendix-ja",
    ]
    assert scope.reference_section_ids == [
        "sec-appendix-references",
        "sec-reference-deep",
        "sec-references",
    ]
    assert "blk-reference-nested" not in scope.in_scope_block_ids
    assert "blk-reference-deep" not in scope.in_scope_block_ids
    assert "blk-reference-en" not in scope.in_scope_block_ids


def _fullwidth_ascii(value: str) -> str:
    return "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char for char in value)


@pytest.mark.parametrize(
    "title",
    [
        "References",
        "Bibliography",
        "Works Cited",
        "Literature Cited",
        "References and Notes",
        "参考文献",
        "引用文献",
        "  Works \t Cited  ",
        "References.",
        "参考文献\uff1a",
        _fullwidth_ascii("References"),
    ],
)
def test_reference_title_requires_normalized_exact_match(title: str) -> None:
    content = _content(
        [_section("sec-candidate", "", title, [_para("blk-candidate", "Candidate prose.")])]
    )
    parsed = DocumentContent.model_validate(content)

    scope = compute_translation_scope(content)

    assert translation_core.is_reference_section(parsed.sections[0]) is True
    assert scope.reference_section_ids == ["sec-candidate"]
    assert scope.in_scope_block_ids == []


@pytest.mark.parametrize(
    "title",
    [
        "References to Prior Work",
        "A Note on References",
        "参考文献レビュー",
        "引用文献との比較",
    ],
)
def test_reference_words_inside_comparison_titles_remain_main(title: str) -> None:
    content = _content(
        [_section("sec-candidate", "1", title, [_para("blk-candidate", "Main prose.")])]
    )
    parsed = DocumentContent.model_validate(content)

    scope = compute_translation_scope(content)

    assert translation_core.is_reference_section(parsed.sections[0]) is False
    assert scope.reference_section_ids == []
    assert scope.in_scope_block_ids == ["blk-candidate"]


def test_reference_entry_only_section_is_structurally_reference() -> None:
    content = DocumentContent.model_validate(
        _content(
            [
                _section(
                    "sec-structural",
                    "",
                    "Sources for evaluation",
                    [_ref_entry("ref-structural", "[1] A. Author. 2024.")],
                )
            ]
        )
    )

    assert translation_core.is_reference_section(content.sections[0]) is True
    scope = compute_translation_scope(content)
    assert scope.reference_section_ids == ["sec-structural"]
    assert scope.in_scope_block_ids == []


def test_scope_classification_is_generic_exclusive_complete_and_dfs_ordered() -> None:
    content = _content(
        [
            _section("sec-roman-i", "I", "Introduction", [_para("blk-i", "Main I.")]),
            _section("sec-roman-ii", "II", "Background", [_para("blk-ii", "Main II.")]),
            _section("sec-roman-iv", "IV", "Evaluation", [_para("blk-iv", "Main IV.")]),
            _section(
                "sec-app-a",
                "A",
                "Proofs",
                [_para("blk-app-a", "Appendix A.")],
                [
                    _section(
                        "sec-app-a-child",
                        "A.1",
                        "Details",
                        [_para("blk-app-a-child", "Nested appendix.")],
                    ),
                    _section(
                        "sec-app-refs",
                        "",
                        "References",
                        [_para("blk-app-ref", "Reference material.")],
                        [
                            _section(
                                "sec-app-ref-child",
                                "A.2",
                                "Sources",
                                [_para("blk-app-ref-child", "Reference child.")],
                            )
                        ],
                    ),
                ],
            ),
            _section(
                "sec-app-en-attached",
                "",
                "Appendix A",
                [_para("blk-app-en", "English appendix.")],
            ),
            _section(
                "sec-app-ja-attached",
                "",
                "付録A",
                [_para("blk-app-ja", "Japanese appendix A.")],
            ),
            _section(
                "sec-app-ja-proof",
                "",
                "附録B\uff1a証明",
                [_para("blk-app-ja-proof", "Japanese appendix B.")],
            ),
            _section(
                "sec-refs",
                "",
                "参考文献",
                [_para("blk-ref", "References.")],
                [
                    _section(
                        "sec-ref-child",
                        "1",
                        "Primary sources",
                        [_para("blk-ref-child", "Reference child.")],
                    )
                ],
            ),
        ]
    )

    scope = compute_translation_scope(content)

    assert scope.in_scope_block_ids[:3] == ["blk-i", "blk-ii", "blk-iv"]
    assert scope.appendix_section_ids == [
        "sec-app-a",
        "sec-app-a-child",
        "sec-app-en-attached",
        "sec-app-ja-attached",
        "sec-app-ja-proof",
    ]
    assert scope.reference_section_ids == [
        "sec-app-refs",
        "sec-app-ref-child",
        "sec-refs",
        "sec-ref-child",
    ]
    assert set(scope.appendix_section_ids).isdisjoint(scope.reference_section_ids)
    assert len(scope.appendix_section_ids) == len(set(scope.appendix_section_ids))
    assert len(scope.reference_section_ids) == len(set(scope.reference_section_ids))
    assert "blk-app-ref-child" not in scope.in_scope_block_ids
    assert "blk-ref-child" not in scope.in_scope_block_ids


@pytest.mark.parametrize(
    "title",
    [
        "AppendixA",
        "Appendix A",
        "AppendixA. Proof",
        "AppendixA\uff0eProof",
        "AppendixA, Proof",
        "AppendixA\uff0cProof",
        "AppendixA: Proof",
        "AppendixA\uff1aProof",
        "AppendixA-Proof",
        "AppendixA\u2013Proof",
        "AppendixA—Proof",
        "付録Ａ",
        "付録Ａ\uff0e証明",
        "付録一",
        "付録一、証明",
        "付録A—証明",
        "附録B\uff1a証明",
    ],
)
def test_attached_appendix_heading_variants_are_classified_generically(title: str) -> None:
    content = _content([_section("sec-app", "", title, [_para("blk-app", "Appendix prose.")])])

    scope = compute_translation_scope(content)

    assert scope.appendix_section_ids == ["sec-app"]
    assert scope.in_scope_block_ids == ["blk-app"]


@pytest.mark.parametrize(
    "title", ["AppendixAnalysis", "AppendixAProof", "付録Analysis", "付録一証明"]
)
def test_appendix_prefix_inside_an_ordinary_word_is_not_an_appendix(title: str) -> None:
    content = _content(
        [
            _section(
                "sec-analysis",
                "1",
                title,
                [_para("blk-analysis", "Main prose.")],
            )
        ]
    )

    scope = compute_translation_scope(content)

    assert scope.appendix_section_ids == []
    assert scope.in_scope_block_ids == ["blk-analysis"]


@pytest.mark.parametrize(
    "roman",
    ["I", "IV", "IX", "XL", "XC", "CD", "CM", "M", "MMMCMXCIX"],
)
def test_roman_number_roots_are_main_without_an_explicit_appendix_title(roman: str) -> None:
    content = _content(
        [
            _section(
                f"sec-roman-{roman.lower()}",
                roman,
                "Main section",
                [_para(f"blk-roman-{roman.lower()}", "Main prose.")],
            ),
            _section(
                "sec-explicit-appendix-c",
                "C",
                "Appendix C",
                [_para("blk-explicit-appendix-c", "Appendix prose.")],
            ),
        ]
    )

    scope = compute_translation_scope(content)

    assert scope.appendix_section_ids == ["sec-explicit-appendix-c"]
    assert scope.in_scope_block_ids == [
        f"blk-roman-{roman.lower()}",
        "blk-explicit-appendix-c",
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            _content(
                [
                    _section("sec-dup", "1", "First", [_para("blk-1", "First.")]),
                    _section("sec-dup", "2", "Second", [_para("blk-2", "Second.")]),
                ]
            ),
            "duplicate section id: sec-dup",
        ),
        (
            _content(
                [
                    _section("sec-1", "1", "First", [_para("blk-dup", "First.")]),
                    _section("sec-2", "2", "Second", [_para("blk-dup", "Second.")]),
                ]
            ),
            "duplicate block id: blk-dup",
        ),
    ],
)
def test_scope_and_plan_fail_closed_on_global_duplicate_ids(
    content: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        compute_translation_scope(content)
    with pytest.raises(ValueError, match=re.escape(message)):
        translation_core.build_translation_plan(content, TranslationSettings(), pages=1)


def test_explicit_scope_appendix_opt_out_preserves_main_and_excludes_references() -> None:
    scope = compute_translation_scope(
        _content_with_main_appendices_and_references(),
        include_appendix=False,
    )

    assert scope.in_scope_block_ids == ["blk-main"]
    assert scope.sections == [{"section_id": "sec-main", "block_ids": ["blk-main"]}]


def test_translation_plan_contract_fallback_filtering_and_monotonic_merge() -> None:
    plan_type = getattr(translation_core, "TranslationPlan", None)
    build_plan = getattr(translation_core, "build_translation_plan", None)
    resolve_plan = getattr(translation_core, "resolve_translation_plan", None)
    merge_plans = getattr(translation_core, "merge_translation_plans", None)
    scope_from_plan = getattr(translation_core, "translation_scope_from_plan", None)
    assert plan_type is not None
    assert callable(build_plan)
    assert callable(resolve_plan)
    assert callable(merge_plans)
    assert callable(scope_from_plan)

    content = _content_with_main_appendices_and_references()
    full = build_plan(content, TranslationSettings(), pages=64)
    subset = build_plan(
        content,
        TranslationSettings(auto_translate_appendix=False, translate_table_cells=False),
        pages=64,
    )

    assert full.model_dump(mode="json") == {
        "version": 1,
        "include_appendix": True,
        "translate_table_cells": True,
        "suggest_section_selection_over_30_pages": False,
        "target_section_ids": [
            "sec-main",
            "sec-appendix-en",
            "sec-appendix-nested",
            "sec-appendix-ja",
        ],
        "target_block_ids": [
            "blk-main",
            "blk-appendix-en",
            "blk-appendix-nested",
            "blk-appendix-ja",
        ],
        "auxiliary_block_ids": [],
        "pages": 64,
    }
    assert subset.target_section_ids == ["sec-main"]
    assert subset.target_block_ids == ["blk-main"]
    assert subset.include_appendix is False
    assert subset.translate_table_cells is False

    # Existing rows without a plan, or with malformed/stale plans, safely use full scope.
    assert resolve_plan(content, None, pages=64) == full
    assert resolve_plan(content, {"version": 999}, pages=64) == full
    stale = subset.model_dump(mode="json")
    stale["target_block_ids"] = ["blk-does-not-exist"]
    assert resolve_plan(content, stale, pages=64) == full
    extra = full.model_dump(mode="json") | {"paper_id": "paper-specific"}
    assert resolve_plan(content, extra, pages=64) == full

    subset_scope = scope_from_plan(content, subset)
    assert subset_scope.in_scope_block_ids == ["blk-main"]
    assert subset_scope.sections == [{"section_id": "sec-main", "block_ids": ["blk-main"]}]

    expanded = merge_plans(content, subset.model_dump(mode="json"), full)
    assert expanded.target_section_ids == full.target_section_ids
    assert expanded.target_block_ids == full.target_block_ids
    assert expanded.include_appendix is True

    not_shrunk = merge_plans(content, full.model_dump(mode="json"), subset)
    assert not_shrunk.target_section_ids == full.target_section_ids
    assert not_shrunk.target_block_ids == full.target_block_ids
    assert not_shrunk.include_appendix is True


def test_empty_translation_plan_is_valid_and_does_not_fallback_to_full_scope() -> None:
    content = _content([_section("sec-main", "1", "Main", [_para("blk-main", "Main prose.")])])
    empty = translation_core.TranslationPlan(
        include_appendix=False,
        translate_table_cells=False,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=[],
        target_block_ids=[],
        pages=1,
    )

    resolved = translation_core.resolve_translation_plan(
        content,
        empty.model_dump(mode="json"),
        pages=1,
    )
    scope = translation_core.translation_scope_from_plan(content, resolved, pages=1)

    assert resolved == empty
    assert scope.sections == []
    assert scope.in_scope_block_ids == []


def test_translation_plan_requires_raw_version_but_accepts_legacy_v1_without_auxiliary() -> None:
    content = _content_with_main_appendices_and_references()
    subset = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=64,
    )
    legacy_v1 = subset.model_dump(mode="json")
    legacy_v1.pop("auxiliary_block_ids", None)

    resolved_legacy = translation_core.resolve_translation_plan(content, legacy_v1, pages=64)
    assert resolved_legacy.target_block_ids == ["blk-main"]
    assert resolved_legacy.auxiliary_block_ids == []

    versionless = dict(legacy_v1)
    versionless.pop("version")
    resolved_versionless = translation_core.resolve_translation_plan(
        content,
        versionless,
        pages=64,
    )
    assert resolved_versionless.target_block_ids == [
        "blk-main",
        "blk-appendix-en",
        "blk-appendix-nested",
        "blk-appendix-ja",
    ]
    assert resolved_versionless.auxiliary_block_ids == []


def test_auxiliary_appendix_blocks_extend_execution_without_changing_primary_scope() -> None:
    content = _content_with_main_appendices_and_references()
    subset = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=64,
    )
    raw = subset.model_dump(mode="json")
    raw["auxiliary_block_ids"] = [
        "blk-appendix-en",
        "blk-appendix-nested",
        "blk-appendix-ja",
    ]

    resolved = translation_core.resolve_translation_plan(content, raw, pages=64)
    primary = translation_core.translation_scope_from_plan(content, resolved, pages=64)
    execution = translation_core.translation_execution_scope_from_plan(
        content,
        resolved,
        pages=64,
    )

    assert resolved.include_appendix is False
    assert primary.in_scope_block_ids == ["blk-main"]
    assert execution.in_scope_block_ids == [
        "blk-main",
        "blk-appendix-en",
        "blk-appendix-nested",
        "blk-appendix-ja",
    ]
    assert [section["section_id"] for section in execution.sections] == [
        "sec-main",
        "sec-appendix-en",
        "sec-appendix-nested",
        "sec-appendix-ja",
    ]


@pytest.mark.parametrize(
    "auxiliary_ids",
    [
        ["blk-main"],
        ["blk-appendix-en", "blk-appendix-en"],
        ["blk-appendix-ja", "blk-appendix-en"],
        ["blk-does-not-exist"],
        ["blk-reference-en"],
    ],
)
def test_invalid_auxiliary_targets_fall_back_to_full_primary_scope(
    auxiliary_ids: list[str],
) -> None:
    content = _content_with_main_appendices_and_references()
    subset = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=64,
    ).model_dump(mode="json")
    subset["auxiliary_block_ids"] = auxiliary_ids

    resolved = translation_core.resolve_translation_plan(content, subset, pages=64)

    assert resolved.target_block_ids == [
        "blk-main",
        "blk-appendix-en",
        "blk-appendix-nested",
        "blk-appendix-ja",
    ]
    assert resolved.auxiliary_block_ids == []


def test_merge_translation_plans_preserves_auxiliary_union_and_promotes_primary_ids() -> None:
    content = _content_with_main_appendices_and_references()
    existing = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=64,
    ).model_dump(mode="json")
    existing["auxiliary_block_ids"] = [
        "blk-appendix-en",
        "blk-appendix-nested",
        "blk-appendix-ja",
    ]
    requested = translation_core.TranslationPlan(
        include_appendix=True,
        translate_table_cells=True,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=["sec-main", "sec-appendix-en"],
        target_block_ids=["blk-main", "blk-appendix-en"],
        auxiliary_block_ids=[],
        pages=64,
    )

    merged = translation_core.merge_translation_plans(
        content,
        existing,
        requested,
        pages=64,
    )

    assert merged.target_block_ids == ["blk-main", "blk-appendix-en"]
    assert merged.auxiliary_block_ids == [
        "blk-appendix-nested",
        "blk-appendix-ja",
    ]


def test_translation_plan_pydantic_limits_accept_boundaries_and_reject_excess() -> None:
    from alinea_core.translation.pipeline import (
        MAX_TRANSLATION_PLAN_BLOCK_IDS,
        MAX_TRANSLATION_PLAN_ID_LENGTH,
        MAX_TRANSLATION_PLAN_PAGES,
        MAX_TRANSLATION_PLAN_SECTION_IDS,
    )
    from pydantic import ValidationError

    base = {
        "include_appendix": True,
        "translate_table_cells": True,
        "suggest_section_selection_over_30_pages": False,
        "target_section_ids": ["s"] * MAX_TRANSLATION_PLAN_SECTION_IDS,
        "target_block_ids": ["b"] * MAX_TRANSLATION_PLAN_BLOCK_IDS,
        "pages": MAX_TRANSLATION_PLAN_PAGES,
    }
    boundary = translation_core.TranslationPlan.model_validate(base)
    assert len(boundary.target_section_ids) == MAX_TRANSLATION_PLAN_SECTION_IDS
    assert len(boundary.target_block_ids) == MAX_TRANSLATION_PLAN_BLOCK_IDS

    invalid_payloads = [
        base | {"target_section_ids": ["s"] * (MAX_TRANSLATION_PLAN_SECTION_IDS + 1)},
        base | {"target_block_ids": ["b"] * (MAX_TRANSLATION_PLAN_BLOCK_IDS + 1)},
        base | {"target_section_ids": ["s" * (MAX_TRANSLATION_PLAN_ID_LENGTH + 1)]},
        base | {"pages": MAX_TRANSLATION_PLAN_PAGES + 1},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            translation_core.TranslationPlan.model_validate(payload)


def test_translation_plan_enforces_combined_block_limit_and_primary_auxiliary_disjointness() -> (
    None
):
    from alinea_core.translation.pipeline import MAX_TRANSLATION_PLAN_BLOCK_IDS
    from pydantic import ValidationError

    primary_count = MAX_TRANSLATION_PLAN_BLOCK_IDS // 2
    base = {
        "version": 1,
        "include_appendix": True,
        "translate_table_cells": True,
        "suggest_section_selection_over_30_pages": False,
        "target_section_ids": [],
        "target_block_ids": [f"target-{index}" for index in range(primary_count)],
        "auxiliary_block_ids": [
            f"auxiliary-{index}" for index in range(MAX_TRANSLATION_PLAN_BLOCK_IDS - primary_count)
        ],
        "pages": 1,
    }

    boundary = translation_core.TranslationPlan.model_validate(base)
    assert len(boundary.target_block_ids) + len(boundary.auxiliary_block_ids) == (
        MAX_TRANSLATION_PLAN_BLOCK_IDS
    )

    with pytest.raises(ValidationError):
        aux_ids = cast(list[str], base["auxiliary_block_ids"])
        translation_core.TranslationPlan.model_validate(
            base | {"auxiliary_block_ids": [*aux_ids, "overflow"]}
        )
    with pytest.raises(ValidationError):
        target_ids = cast(list[str], base["target_block_ids"])
        translation_core.TranslationPlan.model_validate(
            base | {"auxiliary_block_ids": [target_ids[0]]}
        )


def test_build_translation_plan_enforces_document_identifier_limit() -> None:
    from alinea_core.translation.pipeline import MAX_TRANSLATION_PLAN_ID_LENGTH

    content = _content(
        [
            _section(
                "s" * (MAX_TRANSLATION_PLAN_ID_LENGTH + 1),
                "1",
                "Main",
                [_para("blk-main", "Main prose.")],
            )
        ]
    )

    with pytest.raises(ValueError, match="section id exceeds"):
        translation_core.build_translation_plan(content, TranslationSettings(), pages=1)


@pytest.mark.parametrize(
    "oversized_field",
    [
        "target_section_ids",
        "target_block_ids",
        "combined_block_ids",
        "target_id",
        "pages",
        "wrong_type",
    ],
)
def test_oversized_raw_plan_falls_back_before_pydantic_validation(
    oversized_field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alinea_core.translation.pipeline import (
        MAX_TRANSLATION_PLAN_BLOCK_IDS,
        MAX_TRANSLATION_PLAN_ID_LENGTH,
        MAX_TRANSLATION_PLAN_PAGES,
        MAX_TRANSLATION_PLAN_SECTION_IDS,
    )

    content = _content([_section("sec-main", "1", "Main", [_para("blk-main", "Main prose.")])])
    raw: dict[str, Any] = {
        "version": 1,
        "include_appendix": True,
        "translate_table_cells": True,
        "suggest_section_selection_over_30_pages": False,
        "target_section_ids": ["sec-main"],
        "target_block_ids": ["blk-main"],
        "pages": 1,
    }
    if oversized_field == "target_section_ids":
        raw[oversized_field] = ["s"] * (MAX_TRANSLATION_PLAN_SECTION_IDS + 1)
    elif oversized_field == "target_block_ids":
        raw[oversized_field] = ["b"] * (MAX_TRANSLATION_PLAN_BLOCK_IDS + 1)
    elif oversized_field == "combined_block_ids":
        raw["target_block_ids"] = ["b"] * (MAX_TRANSLATION_PLAN_BLOCK_IDS // 2 + 1)
        raw["auxiliary_block_ids"] = ["a"] * (MAX_TRANSLATION_PLAN_BLOCK_IDS // 2)
    elif oversized_field == "target_id":
        raw["target_block_ids"] = ["b" * (MAX_TRANSLATION_PLAN_ID_LENGTH + 1)]
    elif oversized_field == "pages":
        raw[oversized_field] = MAX_TRANSLATION_PLAN_PAGES + 1
    else:
        raw["target_block_ids"] = "blk-main"

    monkeypatch.setattr(
        translation_core.TranslationPlan,
        "model_validate",
        lambda *_args, **_kwargs: pytest.fail("oversized raw plan reached model_validate"),
    )

    resolved = translation_core.resolve_translation_plan(content, raw, pages=1)

    assert resolved.target_section_ids == ["sec-main"]
    assert resolved.target_block_ids == ["blk-main"]


def test_translation_set_model_exposes_nullable_jsonb_plan() -> None:
    assert hasattr(TranslationSet, "plan")
    column = TranslationSet.__table__.c.plan
    assert column.type.__class__.__name__ == "JSONB"
    assert column.nullable is True


def test_make_batches_respects_max_blocks() -> None:
    items = [
        BlockToTranslate(encoded=encode_block(_para(f"b{i}", "text")), block_type="paragraph")
        for i in range(13)
    ]
    batches = make_batches(items)
    assert [len(b) for b in batches] == [8, 5]


async def test_schema_validation_failure_splits_multi_block_translation_batch() -> None:
    provider = _SchemaFailureOnMultiTargetProvider()
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])
    items = [
        BlockToTranslate(
            encoded=encode_block(_para("blk-1", "First paragraph.")), block_type="paragraph"
        ),
        BlockToTranslate(
            encoded=encode_block(_para("blk-2", "Second paragraph.")), block_type="paragraph"
        ),
    ]

    result = await translation_core.translate_batch(
        router,
        items,
        translation_core.TranslationContext(),
    )

    assert [unit.block_id for unit in result] == ["blk-1", "blk-2"]
    assert all(unit.quality_flags == [] for unit in result)
    assert provider.calls == 3


async def test_single_block_schema_failure_uses_bounded_source_fallback() -> None:
    provider = _AlwaysSchemaFailureProvider()
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])
    item = BlockToTranslate(
        encoded=encode_block(_para("blk-1", "First paragraph.")),
        block_type="paragraph",
    )

    [result] = await translation_core.translate_batch(
        router,
        [item],
        translation_core.TranslationContext(),
    )

    assert result.block_id == "blk-1"
    assert result.state == "source_fallback"
    assert result.text_ja == ""
    assert result.quality_flags == ["placeholder_mismatch"]
    assert provider.calls == 3


# ===========================================================================
# PY-TR-07: 設定 4 項目(4f)の反映
# ===========================================================================


def _content_with_appendix() -> dict[str, Any]:
    return _content(
        [
            _section("sec-1", "1", "Introduction", [_para("blk-a", "Body.")]),
            _section("sec-A", "A", "Appendix", [_para("blk-app", "Appendix body.")]),
        ]
    )


def test_settings_reflected_in_job_plan() -> None:
    content = _content_with_appendix()

    # 既定(付録まで全文翻訳・自然訳・30ページ超も欠落なく積む)
    default = TranslationSettings()
    plan = plan_initial_translation(content, default, pages=10)
    assert plan.section_ids == ["sec-1", "sec-A"]
    assert plan.include_appendix is True
    assert plan.style == "natural"
    assert plan.propose_section_selection is False

    # 30 ページ超でも既定は全セクションを積む。
    over = plan_initial_translation(content, default, pages=42)
    assert over.propose_section_selection is False
    assert over.section_ids == ["sec-1", "sec-A"]

    # 明示 opt-out は付録を積まない。
    without_app = TranslationSettings(auto_translate_appendix=False)
    without_app_plan = plan_initial_translation(content, without_app, pages=10)
    assert without_app_plan.section_ids == ["sec-1"]
    assert without_app_plan.include_appendix is False

    # 提案を明示 ON にした場合だけ30ページ超で選択待ち。
    suggest = TranslationSettings(suggest_section_selection_over_30_pages=True)
    suggested = plan_initial_translation(content, suggest, pages=42)
    assert suggested.propose_section_selection is True
    assert suggested.section_ids == []

    # 既定スタイル・表セル設定が反映される
    literal = TranslationSettings(default_style="literal", translate_table_cells=True)
    lp = plan_initial_translation(content, literal, pages=10)
    assert lp.style == "literal"
    assert lp.translate_table_cells is True


def test_default_initial_plan_schedules_nested_appendices_over_thirty_pages() -> None:
    plan = plan_initial_translation(
        _content_with_main_appendices_and_references(),
        TranslationSettings(),
        pages=64,
    )

    assert plan.section_ids == [
        "sec-main",
        "sec-appendix-en",
        "sec-appendix-nested",
        "sec-appendix-ja",
    ]
    assert plan.include_appendix is True
    assert plan.propose_section_selection is False


def test_settings_from_user_settings() -> None:
    parsed = TranslationSettings.from_user_settings(
        {
            "translation": {
                "default_style": "literal",
                "auto_translate_appendix": True,
                "translate_table_cells": True,
                "suggest_section_selection_over_30_pages": False,
            }
        }
    )
    assert parsed == TranslationSettings(
        default_style="literal",
        auto_translate_appendix=True,
        translate_table_cells=True,
        suggest_section_selection_over_30_pages=False,
    )
    # 未指定は全文翻訳の新既定。明示 false とは区別する。
    missing = TranslationSettings.from_user_settings(None)
    assert missing == TranslationSettings()
    assert missing.auto_translate_appendix is True
    assert missing.translate_table_cells is True
    assert missing.suggest_section_selection_over_30_pages is False
    explicit_false = TranslationSettings.from_user_settings(
        {
            "translation": {
                "auto_translate_appendix": False,
                "translate_table_cells": False,
                "suggest_section_selection_over_30_pages": False,
            }
        }
    )
    assert explicit_false.auto_translate_appendix is False
    assert explicit_false.translate_table_cells is False
    assert explicit_false.suggest_section_selection_over_30_pages is False


# ===========================================================================
# PY-TR-02: プレースホルダ検証失敗 → 原文フォールバック(P3)
# ===========================================================================


async def test_placeholder_failure_falls_back_in_memory() -> None:
    # トークンを含む段落。LLM が毎回トークンを落とす → 3 回とも失敗 → 原文フォールバック。
    provider = _ScriptProvider(_drop_tokens)
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])
    unit = await translate_block(_para_ref("blk-1", "See ", "eq-5", " for the loss."), router)
    assert unit.state == "source_fallback"  # 黙って壊れない(引き継ぎメモ)
    assert unit.quality_flags == ["placeholder_mismatch"]
    assert unit.text_ja == ""  # plans/06 §4.6: 原文表示に委ねる(API は text_ja: null)
    assert unit.content_ja == []
    assert unit.db_state() == "machine"  # DB CHECK は machine/edited/protected のみ
    # 初回 + 再試行 2 回 = 計 3 リクエスト(docs/03 §4)
    assert provider.calls == 3


async def test_placeholder_fallback_persisted(db_session: AsyncSession) -> None:
    content = _content(
        [_section("sec-1", "1", "Introduction", [_para_ref("blk-1", "See ", "eq-5", " here.")])]
    )
    _paper, rev, tset = await _make_set(db_session, content=content)
    result = await translate_section(db_session, tset.id, "sec-1", _router(_drop_tokens))

    assert result.fallback == 1
    row = (
        await db_session.execute(
            select(TranslationUnit).where(
                TranslationUnit.set_id == tset.id, TranslationUnit.block_id == "blk-1"
            )
        )
    ).scalar_one()
    assert row.state == "machine"  # source_fallback は machine に写る
    assert row.quality_flags == ["placeholder_mismatch"]
    assert row.text_ja == ""
    assert not row.content_ja  # 空リスト(原文フォールバック)
    # フォールバック行は表示可能扱いされない → 進捗に入らない
    assert result.progress_pct == 0


async def test_refresh_status_uses_stored_targets_and_legacy_rows_fall_back_full(
    db_session: AsyncSession,
) -> None:
    content = _content_with_appendix()
    subset_plan = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=48,
    )
    _paper, _revision, subset_set = await _make_set(
        db_session,
        content=content,
        plan=subset_plan.model_dump(mode="json"),
    )

    subset_result = await translate_section(
        db_session,
        subset_set.id,
        "sec-1",
        _router(),
        block_ids=["blk-a"],
    )

    assert subset_result.progress_pct == 100
    assert subset_result.set_status == "complete"

    _paper2, _revision2, legacy_set = await _make_set(db_session, content=content)
    legacy_result = await translate_section(
        db_session,
        legacy_set.id,
        "sec-1",
        _router(),
        block_ids=["blk-a"],
    )

    assert legacy_result.progress_pct == 50
    assert legacy_result.set_status == "partial"


async def test_personal_set_units_overlay_exact_base_by_block_id(
    db_session: AsyncSession,
) -> None:
    content = _content(
        [
            _section(
                "sec-main",
                "1",
                "Main",
                [_para(f"blk-{index}", f"Text {index}.") for index in range(1, 6)],
            )
        ]
    )
    _paper, _revision, shared = await _make_set(
        db_session,
        content=content,
        plan=translation_core.build_translation_plan(
            content,
            TranslationSettings(),
            pages=5,
        ).model_dump(mode="json"),
    )
    user = User(id=_id(), email=f"{_id()}@overlay.test")
    db_session.add(user)
    await db_session.flush()
    personal = TranslationSet(
        id=_id(),
        revision_id=shared.revision_id,
        style=shared.style,
        scope="personal",
        user_id=str(user.id),
        base_set_id=str(shared.id),
        plan=dict(shared.plan or {}),
        status="pending",
    )
    db_session.add(personal)
    await db_session.flush()

    def unit(
        translation_set: TranslationSet,
        block_id: str,
        text_ja: str,
        *,
        source_hash: str,
        flags: list[str] | None = None,
    ) -> TranslationUnit:
        return TranslationUnit(
            set_id=str(translation_set.id),
            block_id=block_id,
            source_hash=source_hash,
            content_ja=[{"t": "text", "v": text_ja}],
            text_ja=text_ja,
            state="machine",
            quality_flags=flags or [],
        )

    db_session.add_all(
        [
            unit(shared, "blk-1", "共有1", source_hash="same-source"),
            unit(shared, "blk-2", "共有2", source_hash="base-2"),
            unit(shared, "blk-3", "共有3", source_hash="base-3"),
            unit(shared, "blk-4", "共有4", source_hash="same-source"),
            unit(shared, "blk-5", "共有5", source_hash="base-5"),
            unit(personal, "blk-2", "個人2", source_hash="personal-2"),
            unit(
                personal,
                "blk-3",
                "表示不可3",
                source_hash="personal-3",
                flags=["placeholder_mismatch"],
            ),
        ]
    )
    await db_session.commit()

    resolve_set_units = getattr(translation_core, "resolve_translation_set_units", None)
    assert callable(resolve_set_units)
    resolved = await resolve_set_units(db_session, personal)

    assert list(resolved) == ["blk-1", "blk-2", "blk-3", "blk-4", "blk-5"]
    assert resolved["blk-1"].set_id == shared.id
    assert resolved["blk-2"].set_id == personal.id
    assert resolved["blk-2"].text_ja == "個人2"
    assert resolved["blk-3"].quality_flags == ["placeholder_mismatch"]
    assert resolved["blk-1"].source_hash == resolved["blk-4"].source_hash == "same-source"

    status, progress_pct = await _refresh_set_status(
        db_session, personal, DocumentContent.model_validate(content)
    )
    assert status == "complete"
    assert progress_pct == 80


async def test_effective_personal_plan_keeps_primary_and_dynamically_inherits_base_work(
    db_session: AsyncSession,
) -> None:
    content = _content_with_main_appendices_and_references()
    shared_plan = translation_core.TranslationPlan(
        include_appendix=True,
        translate_table_cells=True,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=["sec-main", "sec-appendix-en"],
        target_block_ids=["blk-main", "blk-appendix-en"],
        auxiliary_block_ids=["blk-appendix-nested"],
        pages=64,
    )
    _paper, revision, shared = await _make_set(
        db_session,
        content=content,
        plan=shared_plan.model_dump(mode="json"),
    )
    owner = User(id=_id(), email=f"{_id()}@effective-plan.test")
    db_session.add(owner)
    await db_session.flush()
    personal_plan = translation_core.TranslationPlan(
        include_appendix=False,
        translate_table_cells=False,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=["sec-main"],
        target_block_ids=["blk-main"],
        auxiliary_block_ids=["blk-appendix-ja"],
        pages=64,
    )
    personal = TranslationSet(
        id=_id(),
        revision_id=str(revision.id),
        style="natural",
        scope="personal",
        user_id=str(owner.id),
        base_set_id=str(shared.id),
        plan=personal_plan.model_dump(mode="json"),
        status="pending",
    )
    db_session.add(personal)
    await db_session.commit()

    effective = await translation_core.resolve_effective_translation_plan(
        db_session,
        personal,
        content,
        pages=64,
    )

    assert effective.target_block_ids == ["blk-main"]
    assert effective.auxiliary_block_ids == [
        "blk-appendix-en",
        "blk-appendix-nested",
        "blk-appendix-ja",
    ]


async def test_long_paper_personal_selection_does_not_import_base_plan_targets(
    db_session: AsyncSession,
) -> None:
    content = _content_with_main_appendices_and_references()
    shared_plan = translation_core.build_translation_plan(
        content,
        translation_core.TranslationSettings(),
        pages=64,
    )
    _paper, revision, shared = await _make_set(
        db_session,
        content=content,
        plan=shared_plan.model_dump(mode="json"),
    )
    owner = User(id=_id(), email=f"{_id()}@long-selection.test")
    db_session.add(owner)
    await db_session.flush()
    pending = translation_core.build_ingest_translation_plan(
        content,
        translation_core.TranslationSettings(suggest_section_selection_over_30_pages=True),
        pages=64,
    )
    selected = translation_core.select_translation_plan_sections(
        content,
        pending,
        ["sec-main"],
    )
    personal = TranslationSet(
        id=_id(),
        revision_id=str(revision.id),
        style="natural",
        scope="personal",
        user_id=str(owner.id),
        base_set_id=str(shared.id),
        plan=selected.model_dump(mode="json"),
        status="pending",
    )
    db_session.add(personal)
    await db_session.commit()

    effective = await translation_core.resolve_effective_translation_plan(
        db_session,
        personal,
        content,
        pages=64,
    )

    assert effective.target_block_ids == ["blk-main"]
    assert effective.auxiliary_block_ids == []


@pytest.mark.parametrize(
    "base_kind",
    ["valid_shared", "cross_revision", "cross_style", "personal", "missing"],
)
async def test_personal_set_units_only_loads_valid_shared_base_relation(
    base_kind: str,
    db_session: AsyncSession,
) -> None:
    content = _content([_section("sec-main", "1", "Main", [_para("blk-own", "Own prose.")])])
    _paper, revision, valid_shared = await _make_set(db_session, content=content)
    empty_plan = translation_core.TranslationPlan(
        include_appendix=False,
        translate_table_cells=False,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=[],
        target_block_ids=[],
        auxiliary_block_ids=[],
        pages=1,
    )
    owner = User(id=_id(), email=f"{_id()}@base-owner.test")
    db_session.add(owner)
    await db_session.flush()

    base = valid_shared
    if base_kind == "cross_revision":
        _paper2, _revision2, base = await _make_set(db_session, content=content)
    elif base_kind == "cross_style":
        base = TranslationSet(
            id=_id(),
            revision_id=str(revision.id),
            style="literal",
            scope="shared",
            status="complete",
        )
        db_session.add(base)
        await db_session.flush()
    elif base_kind == "personal":
        other_user = User(id=_id(), email=f"{_id()}@base-other.test")
        db_session.add(other_user)
        await db_session.flush()
        base = TranslationSet(
            id=_id(),
            revision_id=str(revision.id),
            style="natural",
            scope="personal",
            user_id=str(other_user.id),
            base_set_id=str(valid_shared.id),
            status="complete",
        )
        db_session.add(base)
        await db_session.flush()

    personal = TranslationSet(
        id=_id(),
        revision_id=str(revision.id),
        style="natural",
        scope="personal",
        user_id=str(owner.id),
        base_set_id=str(base.id),
        plan=empty_plan.model_dump(mode="json"),
        status="partial",
    )
    db_session.add(personal)
    await db_session.flush()
    db_session.add_all(
        [
            TranslationUnit(
                set_id=str(base.id),
                block_id="blk-secret",
                source_hash="secret",
                content_ja=[{"t": "text", "v": "SECRET"}],
                text_ja="SECRET",
                quality_flags=[],
            ),
            TranslationUnit(
                set_id=str(personal.id),
                block_id="blk-own",
                source_hash="own",
                content_ja=[{"t": "text", "v": "OWN"}],
                text_ja="OWN",
                quality_flags=[],
            ),
        ]
    )
    await db_session.commit()

    probe = personal
    if base_kind == "missing":
        probe = TranslationSet(
            id=str(personal.id),
            revision_id=str(revision.id),
            style="natural",
            scope="personal",
            user_id=str(owner.id),
            base_set_id=_id(),
            plan=empty_plan.model_dump(mode="json"),
            status="partial",
        )
    resolved = await translation_core.resolve_translation_set_units(db_session, probe)

    if base_kind == "valid_shared":
        assert set(resolved) == {"blk-secret", "blk-own"}
        assert resolved["blk-secret"].text_ja == "SECRET"
    else:
        assert set(resolved) == {"blk-own"}
    assert resolved["blk-own"].text_ja == "OWN"

    effective = await translation_core.resolve_effective_translation_plan(
        db_session,
        probe,
        content,
        pages=1,
    )
    assert effective.target_block_ids == []
    assert effective.auxiliary_block_ids == (["blk-own"] if base_kind == "valid_shared" else [])


async def test_refresh_empty_persisted_target_is_complete(
    db_session: AsyncSession,
) -> None:
    content = _content([_section("sec-main", "1", "Main", [_para("blk-main", "Main prose.")])])
    empty_plan = translation_core.TranslationPlan(
        include_appendix=False,
        translate_table_cells=False,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=[],
        target_block_ids=[],
        pages=1,
    )
    _paper, _revision, tset = await _make_set(
        db_session,
        content=content,
        plan=empty_plan.model_dump(mode="json"),
    )

    status, progress_pct = await _refresh_set_status(
        db_session,
        tset,
        DocumentContent.model_validate(content),
    )

    assert status == "complete"
    assert progress_pct == 100


async def test_blocking_retry_uses_configured_escalation_route(db_session: AsyncSession) -> None:
    """回帰: 追加再送が未定義 task=translation_retry を使うと ingest が retry で止まる。"""
    content = _content(
        [_section("sec-1", "1", "Introduction", [_para_ref("blk-1", "See ", "eq-5", " here.")])]
    )
    _paper, _rev, tset = await _make_set(db_session, content=content)
    provider = _ScriptProvider(_drop_tokens)
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])

    result = await translate_section(db_session, tset.id, "sec-1", router)

    assert result.fallback == 1
    assert "translation" in provider.tasks
    assert "retranslation_escalation" in provider.tasks
    assert "translation_retry" not in provider.tasks


async def test_retry_failed_section_uses_escalation_route(db_session: AsyncSession) -> None:
    """失敗分の明示リトライは通常翻訳ルートに戻さず、上位再翻訳ルートを使う。"""
    content = _content(
        [_section("sec-1", "1", "Introduction", [_para_ref("blk-1", "See ", "eq-5", " here.")])]
    )
    _paper, _rev, tset = await _make_set(db_session, content=content)
    provider = _ScriptProvider(_drop_tokens)
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])

    result = await translate_section(
        db_session,
        tset.id,
        "sec-1",
        router,
        block_ids=["blk-1"],
        reason="retry_failed",
    )

    assert result.fallback == 1
    assert provider.tasks
    assert set(provider.tasks) == {"retranslation_escalation"}


@pytest.mark.parametrize("reason", ["initial", "on_demand", "table", "retry_failed"])
async def test_translate_section_without_explicit_ids_never_expands_an_excluded_section(
    reason: str,
    db_session: AsyncSession,
) -> None:
    content = _content_with_appendix()
    plan = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=2,
    ).model_dump(mode="json")
    plan["auxiliary_block_ids"] = ["blk-app"]
    _paper, _revision, tset = await _make_set(db_session, content=content, plan=plan)
    provider = _ScriptProvider()
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])

    result = await translate_section(
        db_session,
        tset.id,
        "sec-A",
        router,
        reason=reason,
    )

    assert result.block_ids == []
    assert result.translated == 0
    assert provider.calls == 0


@pytest.mark.parametrize(
    ("section_id", "block_ids", "reason"),
    [
        ("sec-A", ["blk-app", "blk-app"], "on_demand"),
        ("sec-main", ["blk-app"], "on_demand"),
        ("sec-main", ["blk-equation"], "on_demand"),
        ("sec-A", ["blk-app"], "initial"),
        ("sec-A", ["blk-app-unrequested"], "on_demand"),
        ("sec-A", ["blk-app-unrequested"], "retry_failed"),
    ],
)
async def test_translate_section_rejects_explicit_ids_outside_reason_scope_before_llm(
    section_id: str,
    block_ids: list[str],
    reason: str,
    db_session: AsyncSession,
) -> None:
    content = _content(
        [
            _section(
                "sec-main",
                "1",
                "Main",
                [_para("blk-main", "Main prose."), _equation("blk-equation", "x=y")],
            ),
            _section(
                "sec-A",
                "A",
                "Appendix",
                [
                    _para("blk-app", "Requested appendix prose."),
                    _para("blk-app-unrequested", "Unrequested appendix prose."),
                ],
            ),
        ]
    )
    plan = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=2,
    ).model_dump(mode="json")
    plan["auxiliary_block_ids"] = ["blk-app"]
    _paper, _revision, tset = await _make_set(db_session, content=content, plan=plan)
    provider = _ScriptProvider()
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])

    with pytest.raises(ValueError):
        await translate_section(
            db_session,
            tset.id,
            section_id,
            router,
            block_ids=block_ids,
            reason=reason,
        )

    assert provider.calls == 0


async def test_translate_section_allows_primary_initial_and_auxiliary_on_demand_work(
    db_session: AsyncSession,
) -> None:
    content = _content_with_appendix()
    plan = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=2,
    ).model_dump(mode="json")
    plan["auxiliary_block_ids"] = ["blk-app"]
    _paper, _revision, tset = await _make_set(db_session, content=content, plan=plan)
    provider = _ScriptProvider()
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])

    primary = await translate_section(
        db_session,
        tset.id,
        "sec-1",
        router,
        block_ids=["blk-a"],
        reason="initial",
    )
    auxiliary = await translate_section(
        db_session,
        tset.id,
        "sec-A",
        router,
        block_ids=["blk-app"],
        reason="on_demand",
    )

    assert primary.progress_pct == 100
    assert auxiliary.progress_pct == 100
    assert auxiliary.set_status == "complete"
    assert provider.calls == 2


async def test_auxiliary_units_do_not_advance_primary_status_or_progress(
    db_session: AsyncSession,
) -> None:
    content = _content_with_appendix()
    plan = translation_core.build_translation_plan(
        content,
        TranslationSettings(auto_translate_appendix=False),
        pages=2,
    ).model_dump(mode="json")
    plan["auxiliary_block_ids"] = ["blk-app"]
    _paper, _revision, tset = await _make_set(db_session, content=content, plan=plan)

    result = await translate_section(
        db_session,
        tset.id,
        "sec-A",
        _router(),
        block_ids=["blk-app"],
        reason="on_demand",
    )

    assert result.set_status == "pending"
    assert result.progress_pct == 0


# ===========================================================================
# PY-TR-04: 全ブロック翻訳・状態遷移 pending→partial→complete・進捗分母
# ===========================================================================


async def test_translate_section_status_transitions(db_session: AsyncSession) -> None:
    content = _content(
        [
            _section(
                "sec-1", "1", "Introduction", [_para("blk-a", "First."), _para("blk-b", "Second.")]
            ),
            _section("sec-2", "2", "Method", [_para("blk-c", "Third."), _para("blk-d", "Fourth.")]),
            _section("sec-ref", "", "References", [_ref_entry("blk-r", "[1] ...")]),
        ]
    )
    _paper, rev, tset = await _make_set(db_session, content=content)

    # 分母は自動翻訳対象のみ(参考文献除外)= 4
    assert len(compute_translation_scope(content).in_scope_block_ids) == 4

    router = _router()
    r1 = await translate_section(db_session, tset.id, "sec-1", router)
    assert r1.translated == 2
    assert r1.set_status == "partial"
    assert r1.progress_pct == 50

    r2 = await translate_section(db_session, tset.id, "sec-2", router)
    assert r2.set_status == "complete"
    assert r2.progress_pct == 100

    # 全 unit が日本語訳を持ち、ブロッキングフラグなし
    rows = (
        (await db_session.execute(select(TranslationUnit).where(TranslationUnit.set_id == tset.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 4
    assert all(r.text_ja and r.state == "machine" for r in rows)


async def test_translate_section_with_job_store_progress(db_session: AsyncSession) -> None:
    """回帰: job_store を渡しても MissingGreenlet にならない(JobStore の expire_all 起因)。

    set_progress がセッション内の他 ORM(保持中の tset)を失効させると、次の属性
    アクセスが同期 lazy load になり MissingGreenlet で全翻訳ジョブが落ちていた。
    """
    from alinea_core.jobs.store import JobStore

    content = _content(
        [
            _section(
                "sec-1", "1", "Introduction", [_para("blk-a", "First."), _para("blk-b", "Second.")]
            ),
            _section("sec-2", "2", "Method", [_para("blk-c", "Third.")]),
        ]
    )
    _paper, _rev, tset = await _make_set(db_session, content=content)
    store = JobStore(db_session)
    jid = await store.enqueue(
        kind="translation",
        payload={"set_id": tset.id, "section_id": "sec-1", "reason": "initial"},
        idempotency_key=f"tr:{tset.id}:sec-1:regression",
    )
    assert await store.claim(jid) is not None

    result = await translate_section(
        db_session, tset.id, "sec-1", _router(), job_id=jid, job_store=store
    )
    assert result.translated == 2

    job = await store.get(jid)
    assert job is not None and job.progress > 0


async def test_translate_section_idempotent_skip(db_session: AsyncSession) -> None:
    content = _content([_section("sec-1", "1", "Introduction", [_para("blk-a", "Body text.")])])
    _paper, rev, tset = await _make_set(db_session, content=content)
    provider = _ScriptProvider()
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])
    first = await translate_section(db_session, tset.id, "sec-1", router)
    assert first.translated == 1
    calls_after_first = provider.calls
    # 2 回目: source_hash 一致でスキップ、LLM を呼ばない(冪等。§3.3)
    second = await translate_section(db_session, tset.id, "sec-1", router)
    assert second.skipped == 1
    assert second.translated == 0
    assert provider.calls == calls_after_first


async def test_translate_section_retries_existing_blocking_unit(
    db_session: AsyncSession,
) -> None:
    block = _para("blk-a", "Body text.")
    content = _content([_section("sec-1", "1", "Introduction", [block])])
    _paper, _rev, tset = await _make_set(db_session, content=content)
    encoded = encode_block(block)
    db_session.add(
        TranslationUnit(
            set_id=tset.id,
            block_id="blk-a",
            source_hash=encoded.source_hash,
            content_ja=[],
            text_ja="",
            state="machine",
            quality_flags=["placeholder_mismatch"],
        )
    )
    await db_session.commit()

    provider = _ScriptProvider()
    router = LLMRouter([("fake", "deepseek-v4-flash", provider)])
    result = await translate_section(db_session, tset.id, "sec-1", router)
    assert result.skipped == 0
    assert result.translated == 1
    assert provider.calls == 1
    row = (
        await db_session.execute(
            select(TranslationUnit).where(
                TranslationUnit.set_id == tset.id, TranslationUnit.block_id == "blk-a"
            )
        )
    ).scalar_one()
    assert row.text_ja
    assert row.quality_flags == []


# ===========================================================================
# PY-TR-05: 用語スナップショット(shared=global のみ・3 層マージ・ハッシュ正準)
# ===========================================================================


async def test_build_snapshot_shared_and_merge(db_session: AsyncSession) -> None:
    user = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(id=_id(), title="P", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    li = LibraryItem(id=_id(), user_id=user.id, paper_id=paper.id)
    db_session.add(li)
    await db_session.flush()

    g_global = Glossary(id=_id(), scope="global", name="ML seed")
    g_user = Glossary(id=_id(), scope="user", user_id=user.id)
    g_paper = Glossary(id=_id(), scope="paper", library_item_id=li.id)
    db_session.add_all([g_global, g_user, g_paper])
    await db_session.flush()

    # global 語はサービス全体で共有され、コミット済みデータが実 DB に蓄積するため、
    # 語名を実行ごとに一意化して他テスト/再実行と衝突させない。
    sfx = uuid.uuid4().hex[:8]
    rf = f"rectified flow {sfx}"
    loss = f"loss {sfx}"
    drift = f"drift {sfx}"
    db_session.add_all(
        [
            GlossaryTerm(
                id=_id(),
                glossary_id=g_global.id,
                source_term=rf,
                target_term="整流フロー(global)",
                policy="both",
            ),
            GlossaryTerm(id=_id(), glossary_id=g_global.id, source_term=loss, target_term="損失"),
            GlossaryTerm(
                id=_id(), glossary_id=g_user.id, source_term=rf, target_term="整流フロー(user)"
            ),
            GlossaryTerm(
                id=_id(),
                glossary_id=g_paper.id,
                source_term=rf,
                target_term="整流フロー(paper)",
                auto_extracted=False,
            ),
            # 未確定(自動抽出)の paper 語はスナップショットに入らない
            GlossaryTerm(
                id=_id(),
                glossary_id=g_paper.id,
                source_term=drift,
                target_term="ドリフト",
                auto_extracted=True,
            ),
        ]
    )
    await db_session.commit()

    # shared: origin は global のみ
    shared_snap, shared_hash = await build_snapshot(
        db_session, user_id=user.id, library_item_id=li.id, shared=True
    )
    assert {e["origin"] for e in shared_snap} == {"global"}
    rf_shared = next(e for e in shared_snap if e["source_term"] == rf)
    assert rf_shared["target_term"] == "整流フロー(global)"

    # personal: paper > user > global。未確定語(drift)は含まない
    snap, phash = await build_snapshot(
        db_session, user_id=user.id, library_item_id=li.id, shared=False
    )
    by_term = {e["source_term"]: e for e in snap}
    assert by_term[rf]["target_term"] == "整流フロー(paper)"
    assert by_term[rf]["origin"] == "paper"
    assert by_term[loss]["target_term"] == "損失"
    assert drift not in by_term
    # source_term 小文字順にソート
    assert [e["source_term"] for e in snap] == sorted(e["source_term"] for e in snap)
    assert shared_hash != phash


def test_glossary_hash_is_order_independent() -> None:
    a = {"source_term": "loss", "target_term": "損失", "policy": "translate", "origin": "global"}
    b = {"source_term": "flow", "target_term": "フロー", "policy": "translate", "origin": "global"}
    # 正準化はキー順・空白に不変。ここでは同一内容の 2 表現で一致を確認
    assert glossary_hash([a, b]) == glossary_hash([dict(reversed(a.items())), b])
    assert len(glossary_hash([a, b])) == 16


# ===========================================================================
# PY-TR-10: 共有キャッシュ・personal フォークのマージ解決
# ===========================================================================


async def _seed_unit(
    db: AsyncSession, set_id: str, block_id: str, text_ja: str, *, state: str = "machine"
) -> None:
    db.add(
        TranslationUnit(
            set_id=set_id,
            block_id=block_id,
            source_hash="h-" + block_id,
            content_ja=[{"t": "text", "v": text_ja}],
            text_ja=text_ja,
            state=state,
        )
    )
    await db.commit()


async def test_shared_cache_and_personal_merge(db_session: AsyncSession) -> None:
    content = _content(
        [_section("sec-1", "1", "Introduction", [_para("blk-a", "A."), _para("blk-b", "B.")])]
    )
    _paper, rev, shared = await _make_set(db_session, content=content)
    await _seed_unit(db_session, shared.id, "blk-a", "共有の訳A")
    await _seed_unit(db_session, shared.id, "blk-b", "共有の訳B")
    shared.status = "complete"
    await db_session.commit()

    # 2 人目のユーザー: 完了済み shared セットが即時解決され、翻訳ジョブは不要
    found = await find_shared_set(db_session, rev.id, "natural")
    assert found is not None and found.id == shared.id and found.status == "complete"

    user2 = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user2)
    await db_session.commit()

    resolved = await resolve_display_units(db_session, rev.id, "natural", user2.id)
    assert resolved["blk-a"].text_ja == "共有の訳A"  # personal なし → shared
    assert resolved["blk-b"].text_ja == "共有の訳B"

    # personal フォーク: blk-a のみ差分を持つ(手動編集相当)
    personal = TranslationSet(
        id=_id(),
        revision_id=rev.id,
        style="natural",
        scope="personal",
        user_id=user2.id,
        base_set_id=shared.id,
        glossary_snapshot=[],
        status="complete",
    )
    db_session.add(personal)
    await db_session.commit()
    await _seed_unit(db_session, personal.id, "blk-a", "私の編集A", state="edited")

    merged = await resolve_display_units(db_session, rev.id, "natural", user2.id)
    assert merged["blk-a"].text_ja == "私の編集A"  # personal 優先
    assert merged["blk-a"].state == "edited"
    assert merged["blk-b"].text_ja == "共有の訳B"  # personal になければ shared

    # 他ユーザー(user3)は personal を見ない
    user3 = User(id=_id(), email=f"{_id()}@t.test")
    db_session.add(user3)
    await db_session.commit()
    other = await resolve_display_units(db_session, rev.id, "natural", user3.id)
    assert other["blk-a"].text_ja == "共有の訳A"


def test_resolve_translation_prefers_personal() -> None:
    personal = {"blk-a": "私の訳"}
    base = {"blk-a": "共有訳", "blk-b": "共有B"}
    assert resolve_translation(personal, base, "blk-a") == "私の訳"
    assert resolve_translation(personal, base, "blk-b") == "共有B"
    assert resolve_translation(None, base, "blk-b") == "共有B"
    assert resolve_translation({}, {}, "blk-x") is None


@pytest.mark.parametrize("style", ["natural", "literal"])
def test_batch_schema_name_stable(style: str) -> None:
    # system プリアンブルはスタイル別 2 系統(キャッシュ 2 系統。§15.2)
    from alinea_core.translation import build_system_preamble

    assert "文体規定" in build_system_preamble(style)
