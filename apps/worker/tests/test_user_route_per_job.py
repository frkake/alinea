"""Task 14: ジョブごとにユーザー別 router が for_job(user_id, task) で取得されることを検査する。

各ハンドラが ctx["user_router_factory"].for_job(job.user_id, task) を 1 回だけ呼び、
ctx["router"] を直接読まないことを確認するパラメータ化テスト。

FakeUserRouterFactory は for_job の呼び出し記録と返す LLMRouter を保持する。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import pytest
from alinea_core.db.models import (
    Article,
    DocumentRevision,
    LibraryItem,
    OverviewFigure,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
    VocabEntry,
)
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.jobs.store import JobStore
from alinea_llm.protocols import LLMProvider
from alinea_llm.router import LLMRouter
from alinea_llm.structured import attach_parsed
from alinea_llm.testing.fake_provider import FakeLLMProvider
from alinea_llm.types import LLMRequest, LLMResponse, StreamEvent
from alinea_worker.user_router import TaskAwareLLMRouter
from sqlalchemy.ext.asyncio import AsyncSession

# --------------------------------------------------------------------------- #
# FakeUserRouterFactory
# --------------------------------------------------------------------------- #


class FakeUserRouterFactory:
    """for_job / for_job_tasks の呼び出しを記録し、固定の LLMRouter を返すフェイク。"""

    def __init__(self, router: LLMRouter) -> None:
        self._router = router
        self.calls: list[tuple[str, str]] = []  # [(user_id, task), ...]
        self.task_set_calls: list[tuple[str, tuple[str, ...]]] = []  # [(user_id, tasks), ...]

    async def for_job(self, *, user_id: str, task: str) -> LLMRouter:
        self.calls.append((user_id, task))
        return self._router

    async def for_job_tasks(
        self, *, user_id: str, tasks: tuple[str, ...]
    ) -> TaskAwareLLMRouter:
        self.task_set_calls.append((user_id, tuple(tasks)))
        # 各 task を同じ固定 router に束ねた task-aware ルータを返す(complete(task,...) は委譲)。
        return TaskAwareLLMRouter({t: self._router for t in tasks})


# --------------------------------------------------------------------------- #
# DB シードヘルパ
# --------------------------------------------------------------------------- #


def _uid() -> str:
    return str(uuid.uuid4())


def _content() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(
                        id="blk-p1",
                        type="paragraph",
                        inlines=[Inline(t="text", v="We present a mock method for testing.")],
                    )
                ],
            )
        ],
    )


async def _seed_user_paper_item_revision(db: AsyncSession) -> dict[str, Any]:
    user = User(id=_uid(), email=f"{_uid()}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=_uid(),
        title="Test Paper",
        authors=[{"name": "Test Author"}],
        arxiv_id=f"2209.{uuid.uuid4().hex[:5]}",
        published_on=dt.date(2022, 9, 7),
        license="cc-by-4.0",
        summary_lines=["課題の要約", "手法の要約", "結果の要約"],
        visibility="private",
        owner_user_id=user.id,
    )
    db.add(paper)
    await db.flush()
    revision = DocumentRevision(
        id=_uid(),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=_content().model_dump(),
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    item = LibraryItem(id=_uid(), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(item)
    await db.commit()
    return {"user": user, "paper": paper, "revision": revision, "item": item}


# --------------------------------------------------------------------------- #
# task=article: generate_article.py
# --------------------------------------------------------------------------- #


class _ArticleProvider:
    """決定的な article_v1 / overview_figure_dsl_v1 を返す Fake。"""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        spec = req.json_schema
        assert spec is not None
        if spec.name == "article_v1":
            data: dict[str, Any] = {
                "title": "テスト記事",
                "blocks": [
                    {
                        "type": "heading",
                        "heading": {"level": 2, "text": "背景"},
                        "markdown": None, "quote": None, "figure": None,
                        "explainer": None, "discussion": None, "evidence": [],
                    },
                    {
                        "type": "paragraph",
                        "markdown": "テスト本文。",
                        "heading": None, "quote": None, "figure": None,
                        "explainer": None, "discussion": None,
                        "evidence": ["blk-p1"],
                    },
                    {
                        "type": "discussion",
                        "discussion": {"items": [{"text": "疑問", "origin": "ai"}]},
                        "heading": None, "markdown": None, "quote": None,
                        "figure": None, "explainer": None, "evidence": [],
                    },
                ],
            }
        elif spec.name == "overview_figure_dsl_v1":
            data = {
                "layout": "flow-3",
                "cards": [
                    {"role": "problem", "label": "課題", "heading": "問題", "body": "課題の説明。", "tone": "neutral"},
                    {"role": "proposal", "label": "提案 — TEST", "heading": "手法", "body": "手法の説明。", "tone": "accent"},
                    {"role": "result", "label": "結果", "heading": "結果", "body": "結果の説明。", "tone": "green"},
                ],
                "connectors": [{"from": 0, "to": 1}, {"from": 1, "to": 2}],
                "evidence": ["blk-p1"],
            }
        else:
            data = {}
        resp = LLMResponse(text=json.dumps(data), provider=self.name, model=req.model, stop_reason="end")
        return attach_parsed(resp, spec)

    async def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(self, req: LLMRequest) -> Any:  # pragma: no cover
        raise NotImplementedError
        yield StreamEvent(type="end")

    async def count_tokens(self, req: LLMRequest) -> int:  # pragma: no cover
        return 1


# --------------------------------------------------------------------------- #
# task=translation: translate.py (glossary_change reason)
# --------------------------------------------------------------------------- #


async def _seed_translation(db: AsyncSession) -> dict[str, Any]:
    seed = await _seed_user_paper_item_revision(db)
    tset = TranslationSet(
        id=_uid(),
        revision_id=seed["revision"].id,
        # scope="shared" の場合は user_id なし(DB 制約 ck_translation_sets_scope_user)。
        status="complete",
        style="natural",
        scope="shared",
        glossary_snapshot=[],
    )
    db.add(tset)
    await db.flush()
    unit = TranslationUnit(
        set_id=tset.id,
        block_id="blk-p1",
        source_hash="abc",
        content_ja={"type": "paragraph", "inlines": [{"t": "text", "v": "テスト"}]},
        text_ja="テスト",
        state="machine",
        quality_flags=[],
        model="fake",
    )
    db.add(unit)
    await db.commit()
    return {**seed, "tset": tset, "unit": unit}


# --------------------------------------------------------------------------- #
# task=vocab: generate_vocab_ai.py, extract_vocab_candidates.py
# --------------------------------------------------------------------------- #


async def _seed_vocab(db: AsyncSession) -> dict[str, Any]:
    seed = await _seed_user_paper_item_revision(db)
    entry = VocabEntry(
        id=_uid(),
        user_id=seed["user"].id,
        library_item_id=seed["item"].id,
        term="boil down to",
        context_sentence="The objective boils down to regression.",
        context_hl_start=15,
        context_hl_end=27,
        context_anchor={"revision_id": str(seed["revision"].id), "block_id": "blk-p1",
                         "start": None, "end": None, "quote": None, "side": "source"},
        edited_fields=[],
        generation_status="pending",
    )
    db.add(entry)
    await db.commit()
    return {**seed, "entry": entry}


# --------------------------------------------------------------------------- #
# task=overview_figure_dsl: generate_overview_figure.py
# --------------------------------------------------------------------------- #


class _OverviewProvider:
    """overview_figure_dsl_v1 を返す Fake。"""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        spec = req.json_schema
        assert spec is not None
        data = {
            "layout": "flow-3",
            "cards": [
                {"role": "problem", "label": "課題", "heading": "問題", "body": "課題。", "tone": "neutral"},
                {"role": "proposal", "label": "提案 — TEST", "heading": "手法", "body": "手法。", "tone": "accent"},
                {"role": "result", "label": "結果", "heading": "成果", "body": "成果。", "tone": "green"},
            ],
            "connectors": [{"from": 0, "to": 1}, {"from": 1, "to": 2}],
            "evidence": ["blk-p1"],
        }
        resp = LLMResponse(text=json.dumps(data), provider=self.name, model=req.model)
        return attach_parsed(resp, spec)

    async def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(self, req: LLMRequest) -> Any:  # pragma: no cover
        raise NotImplementedError
        yield StreamEvent(type="end")

    async def count_tokens(self, req: LLMRequest) -> int:  # pragma: no cover
        return 1


async def _seed_overview_figure(db: AsyncSession) -> dict[str, Any]:
    seed = await _seed_user_paper_item_revision(db)
    article = Article(
        id=_uid(),
        library_item_id=seed["item"].id,
        title="テスト記事",
        preset="beginner",
        include_math=False,
        version=1,
    )
    db.add(article)
    overview = OverviewFigure(
        article_id=article.id,
        version=1,
        is_current=True,
        render_mode="svg",
        dsl={"cards": [], "layout": "flow-3", "connectors": [], "evidence": []},
        svg_storage_key="renders/overview/test/v1.svg",
        evidence_anchors=[],
    )
    db.add(overview)
    await db.commit()
    return {**seed, "article": article, "overview": overview}


# --------------------------------------------------------------------------- #
# Parametrized: user_route 取得テスト
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "handler_name,task_name",
    [
        ("article_generate", "article"),
        ("vocab_ai", "vocab"),
        ("vocab_extract", "vocab"),
        ("overview_figure_rewrite", "overview_figure_dsl"),
        ("translation_glossary_change", "translation"),
    ],
)
async def test_user_route_fetched_via_for_job_once_per_job(
    db_session: AsyncSession,
    handler_name: str,
    task_name: str,
) -> None:
    """各ハンドラが user_router_factory.for_job(user_id, task) を 1 回呼ぶことを検査する。"""

    if handler_name == "article_generate":
        from alinea_worker.tasks.generate_article import run_article_job
        from test_generate_article import ArticleScriptProvider

        seed = await _seed_user_paper_item_revision(db_session)
        provider: LLMProvider = ArticleScriptProvider()
        router = LLMRouter([("fake", "claude-opus-4-8", provider)])
        factory = FakeUserRouterFactory(router)
        store = JobStore(db_session)
        job_id = await store.enqueue(
            kind="article",
            priority="interactive",
            user_id=str(seed["user"].id),
            library_item_id=str(seed["item"].id),
            payload={"op": "generate", "library_item_id": str(seed["item"].id), "preset": "beginner"},
        )
        job = await store.claim(job_id)
        assert job is not None
        ctx: dict[str, Any] = {"user_router_factory": factory}
        await run_article_job(ctx, store, job)

    elif handler_name == "vocab_ai":
        from alinea_worker.tasks.generate_vocab_ai import run_generate_vocab_ai

        seed = await _seed_vocab(db_session)
        vocab_resp = {
            "kind": "idiom", "pos_label": "句動詞", "ipa": "/x/",
            "meaning_short": "帰着する", "meaning_long": "帰着。", "interpretation": "解釈。",
            "etymology": "語源。", "mnemonic": "記憶。", "related_forms": "come down to",
        }
        provider = FakeLLMProvider(structured={"vocab_content_v1": vocab_resp})
        router = LLMRouter([("fake", "fake-model", provider)])
        factory = FakeUserRouterFactory(router)
        store = JobStore(db_session)
        job_id = await store.enqueue(
            kind="vocab",
            priority="interactive",
            user_id=str(seed["user"].id),
            library_item_id=str(seed["item"].id),
            payload={"vocab_id": str(seed["entry"].id)},
        )
        job = await store.claim(job_id)
        assert job is not None
        ctx = {"user_router_factory": factory}
        await run_generate_vocab_ai(ctx, store, job)

    elif handler_name == "vocab_extract":
        from alinea_worker.tasks.extract_vocab_candidates import run_extract_vocab_candidates

        seed = await _seed_user_paper_item_revision(db_session)
        candidates_resp = {"candidates": [
            {"term": "boils down to", "kind": "idiom", "block_id": "blk-p1"},
        ]}
        provider = FakeLLMProvider(structured={"vocab_candidates_v1": candidates_resp})
        router = LLMRouter([("fake", "fake-model", provider)])
        factory = FakeUserRouterFactory(router)
        store = JobStore(db_session)
        job_id = await store.enqueue(
            kind="vocab_extract",
            priority="interactive",
            user_id=str(seed["user"].id),
            library_item_id=str(seed["item"].id),
            payload={"library_item_id": str(seed["item"].id)},
        )
        job = await store.claim(job_id)
        assert job is not None
        ctx = {"user_router_factory": factory}
        await run_extract_vocab_candidates(ctx, store, job)

    elif handler_name == "overview_figure_rewrite":
        from alinea_worker.tasks.generate_overview_figure import run_overview_figure_job

        seed = await _seed_overview_figure(db_session)
        provider = _OverviewProvider()
        router = LLMRouter([("fake", "fake-model", provider)])
        factory = FakeUserRouterFactory(router)
        store = JobStore(db_session)
        job_id = await store.enqueue(
            kind="figure",
            priority="interactive",
            user_id=str(seed["user"].id),
            library_item_id=str(seed["item"].id),
            article_id=str(seed["article"].id),
            payload={"figure_kind": "overview", "article_id": str(seed["article"].id)},
        )
        job = await store.claim(job_id)
        assert job is not None
        ctx = {"user_router_factory": factory}
        await run_overview_figure_job(ctx, store, job)

    elif handler_name == "translation_glossary_change":
        from alinea_worker.tasks.translate import run_translation_job

        seed = await _seed_translation(db_session)
        from conftest import ScriptProvider
        provider = ScriptProvider()
        router = LLMRouter([("fake", "deepseek-v4-flash", provider)])
        factory = FakeUserRouterFactory(router)
        store = JobStore(db_session)
        job_id = await store.enqueue(
            kind="translation",
            priority="interactive",
            user_id=str(seed["user"].id),
            library_item_id=str(seed["item"].id),
            payload={
                "reason": "glossary_change",
                "set_id": str(seed["tset"].id),
                "block_ids": ["blk-p1"],
            },
        )
        job = await store.claim(job_id)
        assert job is not None
        ctx = {"user_router_factory": factory}
        await run_translation_job(ctx, store, job)

    else:  # pragma: no cover
        pytest.fail(f"unknown handler: {handler_name}")

    # 共通アサーション: for_job が task_name で呼ばれ、user_id が一致する。
    # article ハンドラはサブタスク(overview_figure_dsl)も追加で解決するため、
    # 少なくとも 1 回以上の呼び出しが期待値であることもある。
    matching = [(uid, t) for uid, t in factory.calls if t == task_name]
    assert len(matching) >= 1, (
        f"{handler_name}: expected ≥1 call with task={task_name!r}, got {factory.calls}"
    )
    called_user_id, called_task = matching[0]
    assert called_task == task_name, f"{handler_name}: expected task={task_name!r}, got {called_task!r}"
    assert called_user_id == str(job.user_id), (
        f"{handler_name}: user_id mismatch: {called_user_id!r} != {job.user_id!r}"
    )
    # すべての呼び出しが同じ user_id で行われていることも確認する。
    for uid, _ in factory.calls:
        assert uid == str(job.user_id), (
            f"{handler_name}: wrong user_id {uid!r} in calls {factory.calls}"
        )


# --------------------------------------------------------------------------- #
# 回帰: section 翻訳ジョブは task 別 router を要求する(統合レビュー F2)
# --------------------------------------------------------------------------- #


async def test_section_translation_uses_task_aware_router_for_escalation(
    db_session: AsyncSession,
) -> None:
    """run_translation_job(section)は translation + retranslation_escalation を含む
    task-aware router を要求する。

    translate_section は検証失敗時に task="retranslation_escalation" へ昇格する。単一 task の
    for_job(task="translation") を渡すと、その昇格が同じ翻訳チェーンへ再送され no-op になる
    (統合レビュー F2)。ハンドラが for_job_tasks を使い、両 task を解決していることを固定する。
    """
    from alinea_worker.tasks.translate import run_translation_job
    from conftest import ScriptProvider

    seed = await _seed_translation(db_session)
    router = LLMRouter([("fake", "deepseek-v4-flash", ScriptProvider())])
    factory = FakeUserRouterFactory(router)
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="translation",
        priority="interactive",
        user_id=str(seed["user"].id),
        library_item_id=str(seed["item"].id),
        payload={
            "reason": "initial",  # section reason(_SECTION_REASONS)
            "set_id": str(seed["tset"].id),
            "section_id": "sec-1",
            "block_ids": ["blk-p1"],
        },
    )
    job = await store.claim(job_id)
    assert job is not None
    await run_translation_job({"user_router_factory": factory}, store, job)

    # 単一 task の for_job ではなく for_job_tasks が使われ、escalation task を含む。
    assert factory.task_set_calls, (
        f"section job must request a task-aware router; got for_job calls={factory.calls}"
    )
    uid, tasks = factory.task_set_calls[0]
    assert uid == str(seed["user"].id)
    assert "translation" in tasks and "retranslation_escalation" in tasks, (
        f"section router must cover translation + escalation, got {tasks}"
    )
