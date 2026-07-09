"""解説図ジョブの worker 実行(M2-06。plans/07 §6)。

- PY-FIG-05: provider 3 値(openai/google/xai)での生成・S3 保存・slot/version 管理。
- PY-FIG-06: 画像生成プロンプト仕様(テンプレート契約テスト。「画像内に文字を描かない」指示を
  含み、重要情報はキャプション側に保持される)。

ImageRouter は本ファイル専用の :class:`~alinea_llm.testing.fake_provider.FakeImageProvider`
で差し替える。DB は実 PostgreSQL、S3 は実 MinIO(worker conftest の規約と同じ。実通信なし)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from alinea_core.article.sources import collect_article_sources
from alinea_core.db.models import (
    Article,
    ArticleBlock,
    DocumentRevision,
    ExplainerFigure,
    Job,
    LibraryItem,
    Paper,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.jobs.store import JobStore
from alinea_llm.errors import ErrorKind
from alinea_llm.router import ImageRouter
from alinea_llm.testing.fake_provider import FakeImageProvider
from alinea_worker.tasks.generate_explainer_figure import (
    EXPLAINER_STYLE_PREAMBLE,
    ExplainerBrief,
    build_explainer_prompt,
    create_explainer_figures_v1,
    run_explainer_figure_job,
    sync_explainer_figures_for_regenerate,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _uid() -> str:
    return str(uuid.uuid4())


def _content() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Method"),
                blocks=[
                    Block(
                        id="blk-p1",
                        type="paragraph",
                        inlines=[Inline(t="text", v="We learn a straight transport map.")],
                    )
                ],
            )
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


async def _sources(db: AsyncSession, seed: dict[str, Any]) -> Any:
    return await collect_article_sources(
        db,
        library_item=seed["library_item"],
        paper=seed["paper"],
        revision=seed["revision"],
        user=seed["user"],
        include_math=False,
    )


def _job(seed: dict[str, Any]) -> Job:
    return Job(
        id=_uid(),
        kind="figure",
        status="running",
        priority="interactive",
        user_id=seed["user"].id,
        library_item_id=seed["library_item"].id,
        payload={},
    )


# --------------------------------------------------------------------------- #
# PY-FIG-05: provider 3 値・S3 保存・slot/version 管理
# --------------------------------------------------------------------------- #
async def test_py_fig_05_creates_v1_for_each_slot_and_saves_to_storage(
    db_session: AsyncSession,
) -> None:
    seed = await _seed(db_session)
    sources = await _sources(db_session, seed)
    job = _job(seed)
    image_provider = FakeImageProvider(name="openai")
    ctx = {"image_router": ImageRouter([("openai", "gpt-image-2", image_provider)])}

    briefs = [
        ExplainerBrief(
            slot=0,
            image_brief_en="a straight path between two distributions",
            caption_ja="軌道の直線化",
            evidence=["blk-p1"],
        ),
        ExplainerBrief(
            slot=1,
            image_brief_en="a distillation process into one step",
            caption_ja="1ステップへの蒸留",
            evidence=[],
        ),
    ]
    created = await create_explainer_figures_v1(
        ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=briefs
    )
    await db_session.commit()

    assert set(created) == {0, 1}
    for slot, row in created.items():
        assert row.slot == slot
        assert row.version == 1
        assert row.is_current is True
        assert row.provider == "openai"
        assert row.image_storage_key
        assert row.image_storage_key.startswith(f"renders/explainer/{row.id}/v1")
    assert image_provider.calls == 2


async def test_py_fig_05_provider_switch_google_and_xai(db_session: AsyncSession) -> None:
    for provider_name, model in (
        ("google", "gemini-3.1-flash-image"),
        ("xai", "grok-imagine-image"),
    ):
        seed = await _seed(db_session)  # 記事ごとに新規 — 版一意制約(article_id,slot,version)回避
        sources = await _sources(db_session, seed)
        job = _job(seed)
        image_provider = FakeImageProvider(name=provider_name)
        ctx = {"image_router": ImageRouter([(provider_name, model, image_provider)])}
        briefs = [
            ExplainerBrief(slot=0, image_brief_en="concept", caption_ja="キャプション", evidence=[])
        ]
        created = await create_explainer_figures_v1(
            ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=briefs
        )
        await db_session.commit()
        assert created[0].provider == provider_name
        assert created[0].model == model


async def test_py_fig_05_regenerate_bumps_version_and_flips_current(
    db_session: AsyncSession,
) -> None:
    seed = await _seed(db_session)
    sources = await _sources(db_session, seed)
    job = _job(seed)
    image_provider = FakeImageProvider(name="google")
    ctx = {"image_router": ImageRouter([("google", "gemini-3.1-flash-image", image_provider)])}
    briefs = [
        ExplainerBrief(
            slot=0, image_brief_en="concept v1", caption_ja="キャプション v1", evidence=[]
        )
    ]
    created = await create_explainer_figures_v1(
        ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=briefs
    )
    await db_session.commit()
    v1 = created[0]

    store = JobStore(db_session)
    regen_job_id = await store.enqueue(
        kind="figure",
        priority="interactive",
        user_id=str(seed["user"].id),
        library_item_id=str(seed["library_item"].id),
        article_id=str(seed["article"].id),
        payload={
            "figure_kind": "explainer",
            "figure_id": str(v1.id),
            "instruction": "もう少し明るい配色で",
        },
    )
    regen_job = await store.claim(regen_job_id)
    assert regen_job is not None
    await run_explainer_figure_job(ctx, store, regen_job)

    refreshed_v1 = await db_session.get(ExplainerFigure, v1.id)
    assert refreshed_v1 is not None
    assert refreshed_v1.is_current is False

    current = (
        await db_session.execute(
            select(ExplainerFigure).where(
                ExplainerFigure.article_id == seed["article"].id,
                ExplainerFigure.slot == 0,
                ExplainerFigure.is_current.is_(True),
            )
        )
    ).scalar_one()
    assert current.version == 2
    assert current.id != v1.id
    assert "もう少し明るい配色で" in current.prompt
    assert image_provider.calls == 2


async def test_py_fig_05_single_regenerate_reuses_persisted_image_brief_en(
    db_session: AsyncSession,
) -> None:
    """単体再生成は article_blocks.content.explainer.image_brief_en を忠実に再現する
    (figures レーン deviations #6 の解消。plans/07 §4.3 の永続化を使い、キャプションの
    代替に頼らない)。
    """
    seed = await _seed(db_session)
    sources = await _sources(db_session, seed)
    job = _job(seed)
    image_provider = FakeImageProvider(name="google")
    ctx = {"image_router": ImageRouter([("google", "gemini-3.1-flash-image", image_provider)])}
    briefs = [
        ExplainerBrief(
            slot=0,
            image_brief_en="a straight path between two distributions",
            caption_ja="キャプションはブリーフと異なる文言",
            evidence=[],
        )
    ]
    created = await create_explainer_figures_v1(
        ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=briefs
    )
    v1 = created[0]
    # 記事ブロック側にも image_brief_en が永続化されている状態を模す(§4.3)。
    db_session.add(
        ArticleBlock(
            article_id=str(seed["article"].id),
            position=0,
            type="explainer_figure",
            content={
                "slot": 0,
                "image_brief_en": "a straight path between two distributions",
                "caption_ja": "キャプションはブリーフと異なる文言",
            },
        )
    )
    await db_session.commit()

    store = JobStore(db_session)
    regen_job_id = await store.enqueue(
        kind="figure",
        priority="interactive",
        user_id=str(seed["user"].id),
        library_item_id=str(seed["library_item"].id),
        article_id=str(seed["article"].id),
        payload={"figure_kind": "explainer", "figure_id": str(v1.id)},
    )
    regen_job = await store.claim(regen_job_id)
    assert regen_job is not None
    await run_explainer_figure_job(ctx, store, regen_job)

    current = (
        await db_session.execute(
            select(ExplainerFigure).where(
                ExplainerFigure.article_id == seed["article"].id,
                ExplainerFigure.slot == 0,
                ExplainerFigure.is_current.is_(True),
            )
        )
    ).scalar_one()
    assert "a straight path between two distributions" in current.prompt
    assert "キャプションはブリーフと異なる文言" not in current.prompt


# --------------------------------------------------------------------------- #
# 記事 ✦指示つき再生成の version-aware 解説図同期(plans/07 §4.5 step8)
# --------------------------------------------------------------------------- #
async def test_sync_creates_v1_for_new_slot(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    sources = await _sources(db_session, seed)
    job = _job(seed)
    image_provider = FakeImageProvider(name="google")
    ctx = {"image_router": ImageRouter([("google", "gemini-3.1-flash-image", image_provider)])}
    briefs = [
        ExplainerBrief(slot=0, image_brief_en="concept", caption_ja="キャプション", evidence=[])
    ]

    updated = await sync_explainer_figures_for_regenerate(
        ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=briefs
    )
    await db_session.commit()

    assert updated[0].version == 1
    assert updated[0].is_current is True
    assert image_provider.calls == 1


async def test_sync_reuses_when_prompt_unchanged_and_bumps_when_changed(
    db_session: AsyncSession,
) -> None:
    seed = await _seed(db_session)
    sources = await _sources(db_session, seed)
    job = _job(seed)
    image_provider = FakeImageProvider(name="google")
    ctx = {"image_router": ImageRouter([("google", "gemini-3.1-flash-image", image_provider)])}
    briefs = [
        ExplainerBrief(
            slot=0, image_brief_en="concept v1", caption_ja="旧キャプション", evidence=[]
        )
    ]
    v1_map = await create_explainer_figures_v1(
        ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=briefs
    )
    await db_session.commit()
    v1 = v1_map[0]
    assert image_provider.calls == 1

    # 同一 image_brief_en(プロンプト不変)→ 画像は再生成せず、キャプションのみ更新する。
    unchanged_briefs = [
        ExplainerBrief(
            slot=0, image_brief_en="concept v1", caption_ja="新キャプション", evidence=[]
        )
    ]
    reused = await sync_explainer_figures_for_regenerate(
        ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=unchanged_briefs
    )
    await db_session.commit()
    assert image_provider.calls == 1  # 増えない(再利用)
    assert reused[0].id == v1.id
    assert reused[0].version == 1
    assert reused[0].caption == "新キャプション"

    rows_after_reuse = (
        (
            await db_session.execute(
                select(ExplainerFigure).where(
                    ExplainerFigure.article_id == seed["article"].id,
                    ExplainerFigure.slot == 0,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_after_reuse) == 1  # 一意制約 (article_id, slot, version) に衝突しない

    # image_brief_en が変わる → version+1・is_current 付替え。
    changed_briefs = [
        ExplainerBrief(
            slot=0, image_brief_en="concept v2", caption_ja="新キャプション", evidence=[]
        )
    ]
    bumped = await sync_explainer_figures_for_regenerate(
        ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=changed_briefs
    )
    await db_session.commit()
    assert image_provider.calls == 2
    assert bumped[0].version == 2
    assert bumped[0].id != v1.id

    refreshed_v1 = await db_session.get(ExplainerFigure, v1.id)
    assert refreshed_v1 is not None
    assert refreshed_v1.is_current is False

    rows_after_bump = (
        (
            await db_session.execute(
                select(ExplainerFigure).where(
                    ExplainerFigure.article_id == seed["article"].id,
                    ExplainerFigure.slot == 0,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_after_bump) == 2  # v1(旧版)+ v2(新版)。一意制約に衝突しない。


async def test_sync_skips_slot_on_provider_chain_exhausted(db_session: AsyncSession) -> None:
    seed = await _seed(db_session)
    sources = await _sources(db_session, seed)
    job = _job(seed)
    failing_provider = FakeImageProvider(name="google", fail=True, error_kind=ErrorKind.SERVER)
    ctx = {"image_router": ImageRouter([("google", "gemini-3.1-flash-image", failing_provider)])}
    briefs = [
        ExplainerBrief(slot=0, image_brief_en="concept", caption_ja="キャプション", evidence=[])
    ]

    updated = await sync_explainer_figures_for_regenerate(
        ctx, db_session, article=seed["article"], sources=sources, job=job, briefs=briefs
    )
    await db_session.commit()

    assert updated == {}  # 部分成功: 例外を伝播させず該当 slot をスキップする
    rows = (
        (
            await db_session.execute(
                select(ExplainerFigure).where(ExplainerFigure.article_id == seed["article"].id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


# --------------------------------------------------------------------------- #
# PY-FIG-06: 画像生成プロンプト仕様(テンプレート契約テスト)
# --------------------------------------------------------------------------- #
def test_py_fig_06_prompt_forbids_text_and_preserves_caption_separately() -> None:
    prompt = build_explainer_prompt("a straight path between two distributions")
    assert "NO text" in prompt
    assert "NO letters" in prompt
    assert "NO digits" in prompt
    assert "NO formulas" in prompt
    assert "NO labels" in prompt
    # 重要情報(用語・数値)はプロンプトではなくキャプション側に置く方針(caption は別引数)。
    assert "FID" not in prompt
    assert EXPLAINER_STYLE_PREAMBLE in prompt
    assert "Concept to illustrate: a straight path between two distributions" in prompt


def test_py_fig_06_instruction_appends_verbatim_japanese_template() -> None:
    prompt = build_explainer_prompt("concept", instruction="もっと明るく")
    assert "Revision request — follow this Japanese instruction from the user:" in prompt
    assert "「もっと明るく」" in prompt


def test_py_fig_06_no_instruction_omits_revision_section() -> None:
    prompt = build_explainer_prompt("concept")
    assert "Revision request" not in prompt
