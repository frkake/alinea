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
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import (
    DocumentRevision,
    Glossary,
    GlossaryTerm,
    LibraryItem,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)
from yakudoku_core.translation import (
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
from yakudoku_core.translation.pipeline import BlockToTranslate, strip_tokens
from yakudoku_core.translation.placeholder import TOKEN_RE
from yakudoku_core.translation.prompts.templates import TranslationBatchOut
from yakudoku_llm.router import LLMRouter
from yakudoku_llm.types import LLMRequest, LLMResponse, StreamEvent

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

    def _targets(self, req: LLMRequest) -> list[tuple[str, str, str]]:
        text = "".join(
            p.text or "" for msg in req.messages if msg.role == "user" for p in msg.parts
        )
        return _TARGET_RE.findall(text)

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
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


def test_scope_excludes_appendix_reference_and_nontext() -> None:
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
        ]
    )
    scope = compute_translation_scope(content)
    assert scope.in_scope_block_ids == ["blk-a", "blk-b"]  # equation/appendix/reference 除外
    assert scope.appendix_section_ids == ["sec-A"]
    assert scope.reference_section_ids == ["sec-ref"]
    assert scope.sections == [{"section_id": "sec-1", "block_ids": ["blk-a", "blk-b"]}]


def test_make_batches_respects_max_blocks() -> None:
    items = [
        BlockToTranslate(encoded=encode_block(_para(f"b{i}", "text")), block_type="paragraph")
        for i in range(13)
    ]
    batches = make_batches(items)
    assert [len(b) for b in batches] == [6, 6, 1]


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

    # 既定(付録を訳さない・自然訳・提案 ON)
    default = TranslationSettings()
    plan = plan_initial_translation(content, default, pages=10)
    assert plan.section_ids == ["sec-1"]  # 付録は積まない
    assert plan.style == "natural"
    assert plan.propose_section_selection is False

    # 付録を自動翻訳する → 付録セクションも積む
    with_app = TranslationSettings(auto_translate_appendix=True)
    assert "sec-A" in plan_initial_translation(content, with_app, pages=10).section_ids

    # 30 ページ超 + 提案 ON → 選択提案・ジョブは積まない(P6)
    over = plan_initial_translation(content, default, pages=42)
    assert over.propose_section_selection is True
    assert over.section_ids == []

    # 提案 OFF なら 30 ページ超でも積む
    no_suggest = TranslationSettings(suggest_section_selection_over_30_pages=False)
    assert plan_initial_translation(content, no_suggest, pages=42).section_ids == ["sec-1"]

    # 既定スタイル・表セル設定が反映される
    literal = TranslationSettings(default_style="literal", translate_table_cells=True)
    lp = plan_initial_translation(content, literal, pages=10)
    assert lp.style == "literal"
    assert lp.translate_table_cells is True


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
    # 欠損は既定
    assert TranslationSettings.from_user_settings(None) == TranslationSettings()


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
    from yakudoku_core.jobs.store import JobStore

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
    from yakudoku_core.translation import build_system_preamble

    assert "文体規定" in build_system_preamble(style)
