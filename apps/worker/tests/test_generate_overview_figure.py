"""全体概要図ジョブの worker 実行(M2-05。plans/07 §5)。

- PY-FIG-03: 版管理(初回 v1 生成 → ``rewrite`` ジョブで version+1・is_current 付替え・旧版データ不変)。
- PY-FIG-04: ラスターモード(設定 ``llm_routing.overview_figure_raster_mode``)の切替が
  設定のみで効く(既定 false = svg)。
- 数値照合チェック(plans/07 §5.2): 本文にない数値は再試行し、なお不一致ならジョブ失敗。

LLM は本ファイル専用の決定的 ``FigureScriptProvider``(実 jsonschema 検証つき)で差し替える。
DB は実 PostgreSQL、S3 は実 MinIO(worker conftest の規約と同じ。実通信なし)。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import pytest
from alinea_core.article.sources import collect_article_sources
from alinea_core.db.models import (
    Article,
    DocumentRevision,
    Job,
    LibraryItem,
    OverviewFigure,
    Paper,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.jobs.store import JobStore
from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.router import LLMRouter
from alinea_llm.structured import attach_parsed
from alinea_llm.types import LLMRequest, LLMResponse
from alinea_worker.tasks.generate_overview_figure import (
    OverviewFigureGenerationError,
    _load_figure_article_context,
    create_overview_figure_v1,
    generate_overview_dsl_with_retry,
    run_overview_figure_job,
)
from sqlalchemy.ext.asyncio import AsyncSession


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
                        inlines=[
                            Inline(t="text", v="Diffusion models require many sampling steps.")
                        ],
                    )
                ],
            ),
            Section(
                id="sec-2",
                heading=SectionHeading(number="2", title="Method"),
                blocks=[
                    Block(
                        id="blk-p2",
                        type="paragraph",
                        inlines=[Inline(t="text", v="We learn a straight transport map.")],
                    )
                ],
            ),
        ],
    )


async def _seed(db: AsyncSession) -> dict[str, Any]:
    user = User(id=_uid(), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=_uid(),
        title="Rectified Flow",
        arxiv_id=f"2209.{uuid.uuid4().hex[:5]}",
        published_on=dt.date(2022, 9, 7),
        license="cc-by-4.0",
        summary_lines=[
            "課題: 拡散モデルは多数のサンプリングステップを要する",
            "手法: 始点と終点を直線で結ぶ輸送を学習する",
            "結果: reflow と蒸留で FID 4.85 を1ステップで達成した",
        ],
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
    library_item = LibraryItem(id=_uid(), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(library_item)
    await db.flush()
    article = Article(id=_uid(), library_item_id=library_item.id, title="やさしい解説", version=1)
    db.add(article)
    await db.commit()
    return {
        "user": user,
        "paper": paper,
        "revision": revision,
        "library_item": library_item,
        "article": article,
    }


def _overview_dsl_payload(
    *, body2: str = "reflow と蒸留で FID 4.85 を1ステップで達成した"
) -> dict[str, Any]:
    return {
        "layout": "flow-3",
        "cards": [
            {
                "role": "problem",
                "label": "課題",
                "heading": "多数のサンプリングステップが必要",
                "body": "拡散モデルは多数のサンプリングステップを要する。",
                "tone": "neutral",
            },
            {
                "role": "proposal",
                "label": "提案 — RECTIFIED FLOW",
                "heading": "直線輸送を学習する",
                "body": "始点と終点を直線で結ぶ輸送を学習する。",
                "tone": "accent",
            },
            {
                "role": "result",
                "label": "結果",
                "heading": "少ないステップで高品質",
                "body": body2,
                "tone": "green",
            },
        ],
        "connectors": [{"from": 0, "to": 1}, {"from": 1, "to": 2}],
        "evidence": ["blk-p1", "blk-p2"],
    }


class FigureScriptProvider:
    """決定的 LLMProvider。``overview_figure_dsl_v1`` を返す(実 jsonschema 検証つき)。"""

    name = "fake"

    def __init__(self, payloads: list[dict[str, Any]] | None = None) -> None:
        self.payloads = payloads if payloads is not None else [_overview_dsl_payload()]
        self.calls = 0

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        spec = req.json_schema
        assert spec is not None
        if spec.name != "overview_figure_dsl_v1":  # pragma: no cover
            raise ProviderError(ErrorKind.SCHEMA_VALIDATION, self.name, req.model, "unknown schema")
        idx = min(self.calls, len(self.payloads) - 1)
        data = self.payloads[idx]
        self.calls += 1
        resp = LLMResponse(
            text=json.dumps(data, ensure_ascii=False), provider=self.name, model=req.model
        )
        return attach_parsed(resp, spec)

    async def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(self, req: LLMRequest) -> Any:  # pragma: no cover
        raise NotImplementedError
        yield  # 到達不能(async generator 型に合わせるためのダミー yield)

    async def count_tokens(self, req: LLMRequest) -> int:  # pragma: no cover
        return 1


def _router(provider: FigureScriptProvider) -> LLMRouter:
    return LLMRouter([("fake", "claude-opus-4-8", provider)])


class _FakeFactory:
    """テスト用 UserRouterFactory: 全タスク共通で固定 router を返す。"""

    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def for_job(self, *, user_id: str, task: str) -> LLMRouter:
        return self._router


def _factory(provider: FigureScriptProvider) -> _FakeFactory:
    return _FakeFactory(_router(provider))


async def _sources(db: AsyncSession, seed: dict[str, Any]) -> Any:
    return await collect_article_sources(
        db,
        library_item=seed["library_item"],
        paper=seed["paper"],
        revision=seed["revision"],
        user=seed["user"],
        include_math=False,
    )


def _job(seed: dict[str, Any]) -> Any:
    return Job(
        id=_uid(),
        kind="figure",
        status="running",
        priority="interactive",
        user_id=seed["user"].id,
        library_item_id=seed["library_item"].id,
        payload={},
    )


async def test_figure_context_rejects_latest_revision_from_another_paper(
    db_session: AsyncSession,
) -> None:
    seed = await _seed(db_session)
    foreign_paper = Paper(id=_uid(), title="Foreign paper", visibility="public")
    db_session.add(foreign_paper)
    await db_session.flush()
    foreign_revision = DocumentRevision(
        id=_uid(),
        paper_id=str(foreign_paper.id),
        parser_version="foreign-test",
        quality_level="A",
        source_format="latex",
        content=_content().model_dump(mode="json"),
    )
    db_session.add(foreign_revision)
    await db_session.flush()
    seed["paper"].latest_revision_id = foreign_revision.id
    await db_session.commit()

    with pytest.raises(LookupError, match="paper/revision not found"):
        await _load_figure_article_context(db_session, str(seed["article"].id))


# --------------------------------------------------------------------------- #
# PY-FIG-03: 版管理
# --------------------------------------------------------------------------- #
async def test_py_fig_03_initial_v1_then_rewrite_bumps_version(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    provider = FigureScriptProvider()
    sources = await _sources(db_session, seed)
    job = _job(seed)

    v1 = await create_overview_figure_v1(
        {},
        db_session,
        router=_router(provider),
        article=seed["article"],
        sources=sources,
        user=seed["user"],
        job=job,
    )
    await db_session.commit()
    assert v1.version == 1
    assert v1.is_current is True
    assert v1.render_mode == "svg"
    original_dsl = json.loads(json.dumps(v1.dsl))

    store = JobStore(db_session)
    rewrite_job_id = await store.enqueue(
        kind="figure",
        priority="interactive",
        user_id=str(seed["user"].id),
        library_item_id=str(seed["library_item"].id),
        article_id=str(seed["article"].id),
        payload={
            "figure_kind": "overview",
            "article_id": str(seed["article"].id),
            "instruction": "もっと簡潔に",
        },
    )
    rewrite_provider = FigureScriptProvider(
        payloads=[_overview_dsl_payload(body2="reflow と蒸留で FID 4.85 を1ステップで達成(簡潔版)")]
    )
    rewrite_job = await store.claim(rewrite_job_id)
    assert rewrite_job is not None
    await run_overview_figure_job(
        {"user_router_factory": _factory(rewrite_provider)}, store, rewrite_job
    )

    refreshed_v1 = await db_session.get(OverviewFigure, v1.id)
    assert refreshed_v1 is not None
    assert refreshed_v1.is_current is False
    assert json.loads(json.dumps(refreshed_v1.dsl)) == original_dsl  # 旧版データ不変

    from sqlalchemy import select

    current = (
        await db_session.execute(
            select(OverviewFigure).where(
                OverviewFigure.article_id == seed["article"].id, OverviewFigure.is_current.is_(True)
            )
        )
    ).scalar_one()
    assert current.version == 2
    assert current.instruction == "もっと簡潔に"
    assert "簡潔版" in current.dsl["cards"][2]["body"]

    # restore 相当: 旧版へ is_current を付け替える(新行を作らない。plans/07 §5.3)。
    # 部分一意索引(uq_overview_figures_current)は即時制約のため、先に旧現行行を false にしてから
    # flush し、その後で復元先を true にする(2 段階更新)。
    current.is_current = False
    await db_session.flush()
    refreshed_v1.is_current = True
    await db_session.commit()
    restored = await db_session.get(OverviewFigure, v1.id)
    assert restored is not None
    assert restored.is_current is True
    assert restored.version == 1


# --------------------------------------------------------------------------- #
# PY-FIG-04: ラスターモード切替(既定 false = svg)
# --------------------------------------------------------------------------- #
async def test_py_fig_04_raster_mode_off_by_default(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    provider = FigureScriptProvider()
    sources = await _sources(db_session, seed)
    job = _job(seed)

    row = await create_overview_figure_v1(
        {},
        db_session,
        router=_router(provider),
        article=seed["article"],
        sources=sources,
        user=seed["user"],
        job=job,
    )
    await db_session.commit()
    assert row.render_mode == "svg"
    assert row.image_storage_key is None
    assert row.svg_storage_key is not None
    assert row.provider == ""  # provider/model はラスターモード時のみ埋める(plans/02 §4.11)


async def test_py_fig_04_raster_mode_on_generates_image_via_image_router(
    db_session: AsyncSession,
) -> None:
    from alinea_llm.router import ImageRouter
    from alinea_llm.testing.fake_provider import FakeImageProvider

    seed = await _seed(db_session)
    seed["user"].settings = {"llm_routing": {"overview_figure_raster_mode": True}}
    await db_session.flush()

    class HeadingsProvider(FigureScriptProvider):
        async def generate_structured(self, req: LLMRequest) -> LLMResponse:
            spec = req.json_schema
            assert spec is not None
            if spec.name == "overview_headings_en_v1":
                data: dict[str, Any] = {"en": ["problem", "proposal", "result"]}
                resp = LLMResponse(text=json.dumps(data), provider=self.name, model=req.model)
                return attach_parsed(resp, spec)
            return await super().generate_structured(req)

    provider = HeadingsProvider()
    sources = await _sources(db_session, seed)
    job = _job(seed)

    image_provider = FakeImageProvider(name="google")
    router = _router(provider)
    image_router = ImageRouter([("google", "gemini-3.1-flash-image", image_provider)])
    ctx = {"image_router": image_router}

    row = await create_overview_figure_v1(
        ctx,
        db_session,
        router=router,
        article=seed["article"],
        sources=sources,
        user=seed["user"],
        job=job,
    )
    await db_session.commit()
    assert row.render_mode == "raster"
    assert row.image_storage_key is not None
    assert row.provider == "google"
    assert row.model == "gemini-3.1-flash-image"
    assert "NO text" in row.prompt
    assert image_provider.calls == 1


# --------------------------------------------------------------------------- #
# 数値照合チェック(plans/07 §5.2): 本文にない数値は再試行し、なお不一致ならジョブ失敗
# --------------------------------------------------------------------------- #
async def test_numeric_mismatch_retries_then_raises(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    bad = _overview_dsl_payload(body2="reflow と蒸留で FID 9.99 を1ステップで達成した")
    provider = FigureScriptProvider(payloads=[bad, bad, bad])
    sources = await _sources(db_session, seed)
    job = _job(seed)

    with pytest.raises(OverviewFigureGenerationError, match="数値照合エラー"):
        await generate_overview_dsl_with_retry(
            _router(provider),
            material_text=(
                sources.summary_text + "\n" + sources.bibliography_text + "\n" + sources.body_text
            ),
            job=job,
        )
    assert provider.calls == 3  # 初回 + 再試行 2 回


# --------------------------------------------------------------------------- #
# 回帰(live-acceptance): 共有 FakeLLM fixture が概要図 DSL の実スキーマに追随していること。
#
# コミット 3c90c95 が ``OVERVIEW_FIGURE_DSL_JSON_SCHEMA`` の required に ``evidence`` を追加した
# のに ``fake_provider._DEFAULT_STRUCTURED["overview_figure_dsl_v1"]`` を更新しなかったため、
# ALINEA_FAKE_LLM=1 経路(E2E・開発)で ``attach_parsed`` が schema_validation で必ず落ち、
# 概要図ジョブが partial_failure に握り潰されて記事に「✦ 全体概要図」が描画されなくなっていた。
# 本テストは E2E スタックと同一の経路(実 FakeLLMProvider.generate_structured と実 schema spec)
# で fixture を検証し、fixture ドリフトを二度と通さないための門番。
# --------------------------------------------------------------------------- #
async def test_shared_fake_fixture_satisfies_real_overview_dsl_schema() -> None:
    from alinea_llm.testing.fake_provider import _DEFAULT_STRUCTURED, FakeLLMProvider
    from alinea_llm.types import ContentPart, Message
    from alinea_worker.tasks.generate_overview_figure import OVERVIEW_FIGURE_DSL_SCHEMA_SPEC

    # fixture は schema の全 required キーを含む(欠けると E2E で握り潰される)。
    fixture = _DEFAULT_STRUCTURED[OVERVIEW_FIGURE_DSL_SCHEMA_SPEC.name]
    assert set(OVERVIEW_FIGURE_DSL_SCHEMA_SPEC.json_schema["required"]).issubset(fixture)

    # E2E と同一経路: 実 FakeLLMProvider が実 schema spec で attach_parsed を通す
    # (fixture が schema 不整合なら ProviderError(SCHEMA_VALIDATION) を送出する)。
    provider = FakeLLMProvider()
    resp = await provider.generate_structured(
        LLMRequest(
            model="claude-opus-4-8",
            messages=[Message(role="user", parts=[ContentPart(type="text", text="x")])],
            json_schema=OVERVIEW_FIGURE_DSL_SCHEMA_SPEC,
        )
    )
    assert resp.parsed is not None
    assert "evidence" in resp.parsed
