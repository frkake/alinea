"""記事生成ジョブの worker 実行(M2-03。plans/07 §4)。

- PY-ART-01: 初回生成(プリセット既定 include_math・attribution 自動挿入)・✦指示つき再生成
  (version+1・instructions_history 追記。指示なしは追記しない)・ブロック rewrite が対象
  ブロックのみ更新する。
- PY-ART-02: 「議論したい点」— 疑問ハイライト(color=question)由来項目に origin=user_highlight
  が付く。存在しない annotation_id を騙る項目は origin=ai に降格する。
- PY-ART-04: attribution ブロックが常に末尾(生成・再生成の両方)。ブロック rewrite で
  attribution を対象にすると防御的に中断する(API 層の 403 が主経路。plans/03 §19.5)。

LLM は本ファイル専用の決定的 ``ArticleScriptProvider``(実 jsonschema 検証つき)で差し替える。
DB は実 PostgreSQL、S3 は実 MinIO(worker conftest の規約と同じ。実通信なし)。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    DocumentRevision,
    LibraryItem,
    Paper,
    User,
)
from yakudoku_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from yakudoku_core.document.inlines import Inline
from yakudoku_core.jobs.store import JobStore
from yakudoku_llm.errors import ErrorKind, ProviderError
from yakudoku_llm.router import LLMRouter
from yakudoku_llm.structured import attach_parsed
from yakudoku_llm.types import LLMRequest, LLMResponse, StreamEvent
from yakudoku_worker.tasks.generate_article import run_article_job


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 文書内容(段落・数式・図。plans/07 §4.2 の素材収集対象)
# ---------------------------------------------------------------------------
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
                            Inline(
                                t="text",
                                v="Rectified flow learns a straight transport map "
                                "between two distributions.",
                            )
                        ],
                    ),
                    Block(
                        id="blk-eq1",
                        type="equation",
                        number="1",
                        label="eq:rf",
                        latex=r"\frac{d}{dt} z_t = v(z_t, t)",
                    ),
                    Block(
                        id="blk-fig1",
                        type="figure",
                        number="1",
                        asset_key="fig-1.png",
                        caption=[Inline(t="text", v="Straightened trajectories.")],
                    ),
                ],
            ),
            Section(
                id="sec-2",
                heading=SectionHeading(number="2", title="Method"),
                blocks=[
                    Block(
                        id="blk-p2",
                        type="paragraph",
                        inlines=[Inline(t="text", v="We use an EMA teacher for distillation.")],
                    ),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# DB シード(private Paper + DocumentRevision + LibraryItem)
# ---------------------------------------------------------------------------
async def _seed(db: AsyncSession, *, license_id: str = "cc-by-4.0") -> dict[str, Any]:
    user = User(id=_uid(), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=_uid(),
        title="Rectified Flow",
        authors=[{"name": "Xingchao Liu"}, {"name": "Qiang Liu"}],
        arxiv_id=f"2209.{uuid.uuid4().hex[:5]}",
        venue="ICLR 2023",
        published_on=dt.date(2022, 9, 7),
        license=license_id,
        summary_lines=["課題の要約行", "手法の要約行", "結果の要約行"],
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
    await db.commit()
    return {"user": user, "paper": paper, "revision": revision, "library_item": library_item}


async def _make_question_annotation(db: AsyncSession, *, library_item_id: str) -> Annotation:
    ann = Annotation(
        id=_uid(),
        library_item_id=library_item_id,
        kind="highlight",
        color="question",
        anchor={
            "revision_id": None,
            "block_id": "blk-p1",
            "start": None,
            "end": None,
            "quote": "Rectified flow learns a straight transport map between two distributions.",
            "side": "source",
        },
    )
    db.add(ann)
    await db.flush()
    return ann


# ---------------------------------------------------------------------------
# 記事構造 JSON(article_v1)の決定的フィクスチャ
# ---------------------------------------------------------------------------
def _article_payload(*, discussion_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = discussion_items or [
        {"text": "reflow を重ねると誤差は蓄積しないか", "origin": "ai"},
        {"text": "ベースラインの選び方は妥当か", "origin": "ai"},
    ]
    return {
        "title": "Rectified Flow を読む: 直線輸送への招待",
        "blocks": [
            {"type": "heading", "heading": {"level": 2, "text": "背景"}},
            {
                "type": "paragraph",
                "markdown": "整流フローは 2 つの分布の間を直線的に輸送する。",
                "evidence": ["blk-p1"],
            },
            {"type": "heading", "heading": {"level": 2, "text": "手法"}},
            {
                "type": "paragraph",
                "markdown": "蒸留には EMA 教師を用いる。",
                "evidence": ["blk-p2"],
            },
            {
                "type": "quote_source",
                "quote": {
                    "block_id": "blk-p1",
                    "text_en": "Rectified flow learns a straight transport map "
                    "between two distributions.",
                },
            },
            {
                "type": "figure_embed",
                "figure": {"block_id": "blk-fig1", "caption_ja": "軌道の直線化。"},
            },
            {"type": "heading", "heading": {"level": 2, "text": "まとめ"}},
            {"type": "paragraph", "markdown": "少ないステップで良好な結果が得られる。"},
            {"type": "discussion", "discussion": {"items": items}},
        ],
    }


class ArticleScriptProvider:
    """決定的 LLMProvider。article_v1 / article_block_v1 を返す(実 jsonschema 検証つき)。"""

    name = "fake"

    def __init__(
        self,
        article_payload: dict[str, Any] | None = None,
        block_payload: dict[str, Any] | None = None,
    ) -> None:
        self.article_payload = (
            article_payload if article_payload is not None else _article_payload()
        )
        self.block_payload = block_payload
        self.calls = 0

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        spec = req.json_schema
        assert spec is not None
        if spec.name == "article_v1":
            data = self.article_payload
        elif spec.name == "article_block_v1":
            assert self.block_payload is not None
            data = self.block_payload
        else:  # pragma: no cover — 未知スキーマはテスト書き漏れ
            raise ProviderError(ErrorKind.SCHEMA_VALIDATION, self.name, req.model, "unknown schema")
        resp = LLMResponse(
            text=json.dumps(data, ensure_ascii=False),
            provider=self.name,
            model=req.model,
            stop_reason="end",
        )
        return attach_parsed(resp, spec)

    async def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(self, req: LLMRequest) -> Any:  # pragma: no cover
        raise NotImplementedError
        yield StreamEvent(type="end")

    async def count_tokens(self, req: LLMRequest) -> int:  # pragma: no cover
        return 1


def _router(provider: ArticleScriptProvider) -> LLMRouter:
    return LLMRouter([("fake", "claude-opus-4-8", provider)])


async def _enqueue_generate(
    db: AsyncSession, *, ctx_data: dict[str, Any], preset: str = "beginner"
) -> str:
    store = JobStore(db)
    return await store.enqueue(
        kind="article",
        priority="interactive",
        user_id=str(ctx_data["user"].id),
        library_item_id=str(ctx_data["library_item"].id),
        payload={
            "op": "generate",
            "library_item_id": str(ctx_data["library_item"].id),
            "preset": preset,
        },
    )


async def _current_blocks(db: AsyncSession, article_id: str) -> list[ArticleBlock]:
    rows = (
        (
            await db.execute(
                select(ArticleBlock)
                .where(ArticleBlock.article_id == article_id)
                .order_by(ArticleBlock.position.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# ===========================================================================
# PY-ART-01: 初回生成・プリセット/include_math・attribution 自動挿入
# ===========================================================================
async def test_generate_creates_article_with_attribution_last(db_session: AsyncSession) -> None:
    ctx_data = await _seed(db_session)
    provider = ArticleScriptProvider()
    job_id = await _enqueue_generate(db_session, ctx_data=ctx_data, preset="beginner")
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None

    await run_article_job({"router": _router(provider)}, store, job)

    job = await store.get(job_id)
    assert job is not None
    assert job.status == "succeeded", job.error
    article_id = job.result["article_id"]
    article = await db_session.get(Article, article_id)
    assert article is not None
    assert article.version == 1
    assert article.preset == "beginner"
    assert article.include_math is False  # beginner 既定(plans/07 §4.1)
    assert article.title  # AI 生成タイトルが保存される

    blocks = await _current_blocks(db_session, str(article.id))
    assert len(blocks) >= 9  # 9 ブロック(モデル出力)+ attribution
    assert blocks[-1].type == "attribution"  # 出典は常に末尾(§4.5 step5・PY-ART-04)
    assert blocks[-1].content["text"]  # 出典文言が組み立てられている

    # quote_source は原文から一語一句そのまま(§4.5 step3)。
    quote_block = next(b for b in blocks if b.type == "quote_source")
    assert quote_block.content["text_en"] == (
        "Rectified flow learns a straight transport map between two distributions."
    )

    # figure_embed: cc-by-4.0 はクレジット自動付記(§4.5 step4)。
    figure_block = next(b for b in blocks if b.type == "figure_embed")
    assert figure_block.content["variant"] == "figure"
    assert "出典" in figure_block.content["credit"]
    assert "CC BY 4.0" in figure_block.content["license_badge"]

    # text_plain が導出され、article_block_to_plain と一致する(PGroonga 索引対象)。
    heading_block = next(b for b in blocks if b.type == "heading")
    assert heading_block.text_plain == heading_block.content["text"]


async def test_generate_respects_implementer_include_math_default(
    db_session: AsyncSession,
) -> None:
    ctx_data = await _seed(db_session)
    provider = ArticleScriptProvider()
    job_id = await _enqueue_generate(db_session, ctx_data=ctx_data, preset="implementer")
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_article_job({"router": _router(provider)}, store, job)

    job = await store.get(job_id)
    assert job is not None and job.status == "succeeded"
    article = await db_session.get(Article, job.result["article_id"])
    assert article is not None
    assert article.include_math is True  # implementer 既定(plans/07 §4.1)


async def test_generate_conflict_free_regenerate_bumps_version_and_history(
    db_session: AsyncSession,
) -> None:
    ctx_data = await _seed(db_session)
    provider = ArticleScriptProvider()
    job_id = await _enqueue_generate(db_session, ctx_data=ctx_data)
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_article_job({"router": _router(provider)}, store, job)
    job = await store.get(job_id)
    assert job is not None
    article_id = job.result["article_id"]

    # 指示なし再生成: version+1・instructions_history は追記されない。
    regen_job_id = await store.enqueue(
        kind="article",
        priority="interactive",
        user_id=str(ctx_data["user"].id),
        library_item_id=str(ctx_data["library_item"].id),
        article_id=article_id,
        payload={"op": "regenerate", "article_id": article_id},
    )
    regen_job = await store.claim(regen_job_id)
    assert regen_job is not None
    await run_article_job({"router": _router(provider)}, store, regen_job)
    article = await db_session.get(Article, article_id)
    assert article is not None
    assert article.version == 2
    assert article.instructions_history == []

    # 指示つき再生成: version+1・instructions_history に追記される(§4.6・PY-ART-01)。
    regen_job_id2 = await store.enqueue(
        kind="article",
        priority="interactive",
        user_id=str(ctx_data["user"].id),
        library_item_id=str(ctx_data["library_item"].id),
        article_id=article_id,
        payload={
            "op": "regenerate",
            "article_id": article_id,
            "instruction": "もっと簡単に書き直してください",
        },
    )
    regen_job2 = await store.claim(regen_job_id2)
    assert regen_job2 is not None
    await run_article_job({"router": _router(provider)}, store, regen_job2)
    await db_session.refresh(article)
    assert article.version == 3
    assert article.instructions_history == ["もっと簡単に書き直してください"]

    blocks = await _current_blocks(db_session, article_id)
    assert blocks[-1].type == "attribution"  # 再生成後も末尾は出典(PY-ART-04)


async def test_block_rewrite_updates_only_target_block(db_session: AsyncSession) -> None:
    ctx_data = await _seed(db_session)
    provider = ArticleScriptProvider()
    job_id = await _enqueue_generate(db_session, ctx_data=ctx_data)
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_article_job({"router": _router(provider)}, store, job)
    job = await store.get(job_id)
    assert job is not None
    article_id = job.result["article_id"]

    blocks_before = await _current_blocks(db_session, article_id)
    target = next(b for b in blocks_before if b.type == "paragraph")
    other_snapshot = {b.id: (b.content, b.text_plain) for b in blocks_before if b.id != target.id}

    rewrite_provider = ArticleScriptProvider(
        block_payload={"type": "paragraph", "markdown": "書き直した本文です。"}
    )
    rewrite_job_id = await store.enqueue(
        kind="article",
        priority="interactive",
        user_id=str(ctx_data["user"].id),
        library_item_id=str(ctx_data["library_item"].id),
        article_id=article_id,
        payload={"op": "block_rewrite", "article_id": article_id, "block_pk": target.id},
    )
    rewrite_job = await store.claim(rewrite_job_id)
    assert rewrite_job is not None
    await run_article_job({"router": _router(rewrite_provider)}, store, rewrite_job)

    rewrite_job = await store.get(rewrite_job_id)
    assert rewrite_job is not None
    assert rewrite_job.status == "succeeded", rewrite_job.error
    assert rewrite_job.result["block"]["content"]["markdown"] == "書き直した本文です。"

    article = await db_session.get(Article, article_id)
    assert article is not None
    assert article.version == 1  # ブロック書き直しは記事 version を進めない(§4.8)

    blocks_after = await _current_blocks(db_session, article_id)
    updated = next(b for b in blocks_after if b.id == target.id)
    assert updated.content["md"] == "書き直した本文です。"
    for blk in blocks_after:
        if blk.id == target.id:
            continue
        assert (blk.content, blk.text_plain) == other_snapshot[blk.id]  # 他ブロックは不変


# ===========================================================================
# PY-ART-02: 「議論したい点」— 疑問ハイライト由来の origin=user_highlight
# ===========================================================================
async def test_discussion_item_from_question_annotation_gets_user_highlight_origin(
    db_session: AsyncSession,
) -> None:
    ctx_data = await _seed(db_session)
    ann = await _make_question_annotation(db_session, library_item_id=ctx_data["library_item"].id)
    await db_session.commit()

    # 素材一覧の短縮参照は ann_01(唯一の注釈)。§4.2 の記法を模す。
    payload = _article_payload(
        discussion_items=[
            {
                "text": "reflow は誤差を蓄積しないか",
                "origin": "user_highlight",
                "annotation_id": "ann_01",
            },
            {
                "text": "存在しない注釈を騙る項目",
                "origin": "user_highlight",
                "annotation_id": "ann_99",
            },
            {"text": "実験設定は十分か", "origin": "ai"},
        ]
    )
    provider = ArticleScriptProvider(article_payload=payload)
    job_id = await _enqueue_generate(db_session, ctx_data=ctx_data)
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_article_job({"router": _router(provider)}, store, job)

    job = await store.get(job_id)
    assert job is not None and job.status == "succeeded", job.error if job else None
    article_id = job.result["article_id"]
    blocks = await _current_blocks(db_session, article_id)
    discussion = next(b for b in blocks if b.type == "discussion")
    items = discussion.content["items"]
    assert len(items) == 3

    valid_item = next(i for i in items if i["md"] == "reflow は誤差を蓄積しないか")
    assert valid_item["origin"] == "user_highlight"
    assert valid_item["annotation_id"] == str(ann.id)  # 実在 UUID に解決される

    bogus_item = next(i for i in items if i["md"] == "存在しない注釈を騙る項目")
    assert bogus_item["origin"] == "ai"  # 実在しない注釈は ai に降格(P3)
    assert bogus_item["annotation_id"] is None

    ai_item = next(i for i in items if i["md"] == "実験設定は十分か")
    assert ai_item["origin"] == "ai"


# ===========================================================================
# PY-ART-04: attribution ブロックは常に末尾・rewrite 対象外(worker 側の防御的中断)
# ===========================================================================
async def test_block_rewrite_of_attribution_is_rejected(db_session: AsyncSession) -> None:
    ctx_data = await _seed(db_session)
    provider = ArticleScriptProvider()
    job_id = await _enqueue_generate(db_session, ctx_data=ctx_data)
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_article_job({"router": _router(provider)}, store, job)
    job = await store.get(job_id)
    assert job is not None
    article_id = job.result["article_id"]

    blocks = await _current_blocks(db_session, article_id)
    attribution = next(b for b in blocks if b.type == "attribution")

    rewrite_job_id = await store.enqueue(
        kind="article",
        priority="interactive",
        user_id=str(ctx_data["user"].id),
        library_item_id=str(ctx_data["library_item"].id),
        article_id=article_id,
        payload={"op": "block_rewrite", "article_id": article_id, "block_pk": attribution.id},
    )
    rewrite_job = await store.claim(rewrite_job_id)
    assert rewrite_job is not None
    try:
        await run_article_job({"router": _router(provider)}, store, rewrite_job)
        raised = False
    except PermissionError:
        raised = True
    assert raised  # API 層の 403 が主経路。worker は防御的に中断する(plans/03 §19.5)。

    # 出典ブロックの内容は変わらない。
    await db_session.refresh(attribution)
    assert attribution.type == "attribution"
