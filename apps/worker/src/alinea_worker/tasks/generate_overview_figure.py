"""全体概要図ジョブ(plans/07 §5、kind='figure' / payload.figure_kind='overview')。

- 初回生成(v1)は記事初回・再生成ジョブ(:mod:`alinea_worker.tasks.generate_article`)の
  rendering 段から :func:`create_overview_figure_v1` を直接呼ぶ想定(§5.3「記事が存在しない
  状態で概要図単体は生成しない」)。**この呼び出し配線は generate_article.py 側の担当(M2-03/04
  レーン)であり、本ファイルの所有範囲外のため followups に記載する。**
- 書き直し(``POST .../overview-figure/rewrite``)は :func:`run_overview_figure_job` が
  ``jobs(kind='figure')`` として処理する(plans/07 §5.3)。

LLMRouter は ``ctx['router']``、ImageRouter(ラスターモード時)は ``ctx['image_router']`` から
注入する(apps 間 import を避けるための DI。generate_article.py と同じ規約)。SVG レンダリング
は :mod:`alinea_figures.overview_svg`(決定的・バイト同一)に委譲する。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import structlog
from alinea_core.article import ArticleSources, EvidenceDisplayResolver, build_evidence_wire
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
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage
from alinea_figures.dsl import (
    OVERVIEW_FIGURE_DSL_JSON_SCHEMA,
    OVERVIEW_FIGURE_DSL_SCHEMA_NAME,
    Card,
    OverviewFigureDslGenerated,
)
from alinea_figures.overview_svg import render_overview_svg
from alinea_llm.router import ImageRouter, LLMRouter
from alinea_llm.types import ContentPart, ImageRequest, LLMRequest, LLMResponse, Message
from alinea_llm.types import JsonSchemaSpec as _JsonSchemaSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger("alinea.worker")

JST = dt.timezone(dt.timedelta(hours=9))
MAX_GENERATION_ATTEMPTS = 3  # 初回 + 再試行 2 回(plans/07 §5.2「数値照合エラー」)
FOOTER_GENERATED_BY = "✦ AI 生成 · Alinea"

OVERVIEW_FIGURE_DSL_SCHEMA_SPEC = _JsonSchemaSpec(
    name=OVERVIEW_FIGURE_DSL_SCHEMA_NAME, json_schema=OVERVIEW_FIGURE_DSL_JSON_SCHEMA
)

# --------------------------------------------------------------------------- #
# プロンプト(plans/07 §5.2 逐語)
# --------------------------------------------------------------------------- #
OVERVIEW_SYSTEM_PROMPT = (
    "あなたは論文の「全体概要図」の図データ作成者です。課題 → 提案 → 結果 の 3 カードで"
    "論文の骨格を表す JSON を出力します。\n"
    "\n"
    "## 規則\n"
    "1. すべての記述は与えられた素材(論文本文)に根拠があること。本文にない主張・数値を"
    "書かない。数値(FID などのスコア)は素材から正確に転記する。\n"
    "2. 文字数制限: label 24 / heading 36 / body 80 文字以内。読み手が 5 秒で骨格を"
    "掴める密度にする。\n"
    '3. label は次の定型に従う: 1 枚目 "課題" / 2 枚目 "提案 — {手法名を大文字英語で}" '
    '/ 3 枚目 "結果"。\n'
    "4. heading は名詞止めまたは体言止めの 1 文。body は補足 1〜2 文(常体)。\n"
    "5. evidence には各カードの根拠となるブロックID(素材の行頭 [ID|位置])を合計 2〜4 個"
    "選ぶ(課題・提案・結果それぞれの出所)。\n"
    "6. JSON のみを出力する。\n"
)

_MATERIAL_CHAR_BUDGET = 24_000  # plans/07 §5.2 の ≤12,000 トークンの簡易近似(≈2 chars/token)


class OverviewFigureGenerationError(Exception):
    """DSL の構造検証・数値照合に失敗(§5.2)。呼び出し側で最大 2 回まで再試行する。"""


def build_overview_material_text(sources: ArticleSources) -> str:
    """素材(3行要約・アブスト・本文・表キャプション)を 1 テキストにまとめる(plans/07 §5.2 の近似)。

    厳密な「アブスト訳+イントロ先頭2,000トークン+結論全文+表キャプション最大10件」の個別選定は
    行わず、既存の :class:`ArticleSources` が持つ集約済みテキストを再利用する(deviations 参照)。
    """
    parts = [
        sources.summary_text,
        sources.bibliography_text,
        sources.figures_text,
        sources.body_text,
    ]
    material = "\n\n".join(p for p in parts if p)
    if len(material) > _MATERIAL_CHAR_BUDGET:
        material = material[:_MATERIAL_CHAR_BUDGET] + "\n…(以降は文字数上限のため省略しました)"
    return material


def build_overview_user_prompt(
    material_text: str, *, current_dsl: dict[str, Any] | None = None, instruction: str | None = None
) -> str:
    prompt = material_text
    if current_dsl is not None and instruction:
        import json

        prompt += (
            "\n\n## 現在の図データ\n"
            + json.dumps(current_dsl, ensure_ascii=False)
            + "\n## 書き直し指示(最優先)\n"
            + instruction
        )
    return prompt


# --------------------------------------------------------------------------- #
# 数値照合チェック(plans/07 §5.2)
# --------------------------------------------------------------------------- #
_NUMERIC_TOKEN = re.compile(r"[0-9][0-9.,×^%]*")  # noqa: RUF001 (plans/07 §5.2 逐語パターン)


def _numeric_tokens(cards: list[Card]) -> set[str]:
    tokens: set[str] = set()
    for card in cards:
        tokens |= set(_NUMERIC_TOKEN.findall(card.heading))
        tokens |= set(_NUMERIC_TOKEN.findall(card.body))
    return tokens


def verify_numeric_tokens(cards: list[Card], material_text: str) -> str | None:
    """本文に現れない数値トークンを 1 つ返す(なければ None)。"""
    for token in _numeric_tokens(cards):
        if token not in material_text:
            return token
    return None


# --------------------------------------------------------------------------- #
# LLM 構造化呼び出し + 再試行
# --------------------------------------------------------------------------- #
async def _call_dsl_structured(router: LLMRouter, *, user_text: str, job: Job) -> LLMResponse:
    request = LLMRequest(
        model="",
        system=[ContentPart.from_text(OVERVIEW_SYSTEM_PROMPT, cache_hint=True)],
        messages=[Message(role="user", parts=[ContentPart.from_text(user_text)])],
        max_output_tokens=4096,
        effort="high",
        timeout_s=120.0,
        metadata={"task": "overview_figure_dsl"},
    )
    resp = await router.complete(
        "overview_figure_dsl",
        schema=OVERVIEW_FIGURE_DSL_SCHEMA_SPEC,
        mode="structured",
        request=request,
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
    )
    assert resp.parsed is not None
    return resp


async def generate_overview_dsl_with_retry(
    router: LLMRouter,
    *,
    material_text: str,
    job: Job,
    current_dsl: dict[str, Any] | None = None,
    instruction: str | None = None,
) -> tuple[OverviewFigureDslGenerated, LLMResponse]:
    """構造検証・数値照合に失敗した場合、エラー内容を添えて最大 2 回まで再試行する(§5.2)。"""
    user_text = build_overview_user_prompt(
        material_text, current_dsl=current_dsl, instruction=instruction
    )
    last_error: str | None = None
    resp: LLMResponse | None = None
    for _attempt in range(MAX_GENERATION_ATTEMPTS):
        prompt = user_text
        if last_error:
            prompt += f"\n\n## 前回出力の検証エラー(修正して再出力してください)\n{last_error}"
        resp = await _call_dsl_structured(router, user_text=prompt, job=job)
        assert resp.parsed is not None
        try:
            generated = OverviewFigureDslGenerated.model_validate(resp.parsed)
        except Exception as exc:  # pydantic.ValidationError 等
            last_error = f"DSL 検証エラー: {exc}"
            continue
        bad_token = verify_numeric_tokens(generated.cards, material_text)
        if bad_token is not None:
            last_error = f'数値照合エラー: "{bad_token}" が本文に見つかりません'
            continue
        return generated, resp
    raise OverviewFigureGenerationError(last_error or "概要図 DSL の生成に失敗しました")


# --------------------------------------------------------------------------- #
# 根拠アンカー解決
# --------------------------------------------------------------------------- #
def _evidence_anchor_dicts(evidence: list[str], sources: ArticleSources) -> list[dict[str, Any]]:
    revision_id = str(sources.revision.id)
    out: list[dict[str, Any]] = []
    for ref in evidence:
        if ref.startswith("blk-"):
            if ref not in sources.block_ids:
                continue
        elif ref.startswith("sec-"):
            if ref not in sources.section_ids:
                continue
        else:
            continue
        out.append(
            {
                "revision_id": revision_id,
                "block_id": ref,
                "start": None,
                "end": None,
                "quote": None,
                "side": "source",
            }
        )
    return out


def resolve_evidence_wire(evidence: list[str], sources: ArticleSources) -> list[dict[str, Any]]:
    """``overview_figures.evidence_anchors`` に保存する wire 形(``{ref,display,anchor}[]``)。"""
    anchor_dicts = _evidence_anchor_dicts(evidence, sources)
    resolver = EvidenceDisplayResolver(sources.content)
    return build_evidence_wire(anchor_dicts, resolver)


def evidence_chip_displays(evidence_wire: list[dict[str, Any]]) -> list[str]:
    return [str(e["display"]) for e in evidence_wire]


# --------------------------------------------------------------------------- #
# ラスター生成モード(plans/07 §5.5)
# --------------------------------------------------------------------------- #
_HEADINGS_EN_SCHEMA = _JsonSchemaSpec(
    name="overview_headings_en_v1",
    json_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["en"],
        "properties": {
            "en": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "string"}}
        },
    },
)


def build_overview_raster_prompt(headings_en: tuple[str, str, str]) -> str:
    """§5.5: 共通スタイルを再利用しつつ、概要図の文字なし契約を優先する。"""
    from alinea_worker.tasks.generate_explainer_figure import EXPLAINER_STYLE_PREAMBLE

    text_constraint = (
        "Overview-specific constraint: Strictly NO text, NO letters, NO digits, NO formulas, "
        "NO labels, NO watermarks, NO logos. The SVG and caption carry all wording."
    )
    concept = (
        f"Concept: a three-stage flow diagram showing (1) {headings_en[0]}, "
        f"(2) {headings_en[1]}, (3) {headings_en[2]}, connected left to right by arrows. "
        "Abstract shapes only."
    )
    return f"{EXPLAINER_STYLE_PREAMBLE}\n{text_constraint}\n\n{concept}"


async def _translate_headings_en(
    router: LLMRouter, cards: list[Card], *, job: Job
) -> tuple[str, str, str]:
    request = LLMRequest(
        model="",
        messages=[
            Message(
                role="user",
                parts=[
                    ContentPart.from_text(
                        "Translate the following 3 Japanese headings to short English noun "
                        'phrases. Output JSON {"en": ["...", "...", "..."]}.\n'
                        + "\n".join(f"{i + 1}. {c.heading}" for i, c in enumerate(cards))
                    )
                ],
            )
        ],
        max_output_tokens=512,
        effort="low",
        metadata={"task": "summary"},
    )
    resp = await router.complete(
        "summary",
        schema=_HEADINGS_EN_SCHEMA,
        mode="structured",
        request=request,
        job_id=str(job.id),
    )
    assert resp.parsed is not None
    en = list(resp.parsed.get("en") or [])
    while len(en) < 3:
        en.append("")
    return (str(en[0]), str(en[1]), str(en[2]))


def raster_mode_enabled(user: User) -> bool:
    settings = user.settings or {}
    routing = settings.get("llm_routing") or {}
    return bool(routing.get("overview_figure_raster_mode", False))


async def _maybe_generate_raster(
    ctx: dict[str, Any], *, cards: list[Card], user: User, job: Job
) -> tuple[bytes, str, str, str] | None:
    """ラスター画像(bytes, provider, model, prompt)を返す。無効時/未構成時は None。"""
    if not raster_mode_enabled(user):
        return None
    image_router: ImageRouter | None = ctx.get("image_router")
    router: LLMRouter | None = ctx.get("router")
    if image_router is None or router is None:
        log.warning("overview_raster_skipped_no_router", job_id=str(job.id))
        return None
    headings_en = await _translate_headings_en(router, cards, job=job)
    prompt = build_overview_raster_prompt(headings_en)
    result = await image_router.generate(
        prompt,
        task="explainer_image",
        request=ImageRequest(model="", prompt=prompt),
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
    )
    return (result.image_bytes, result.provider, result.model, prompt)


# --------------------------------------------------------------------------- #
# S3 キー
# --------------------------------------------------------------------------- #
def overview_svg_storage_key(article_id: str, version: int) -> str:
    return f"renders/overview/{article_id}/v{version}.svg"


def overview_raster_storage_key(article_id: str, version: int) -> str:
    return f"renders/overview/{article_id}/v{version}.png"


# --------------------------------------------------------------------------- #
# v1 初回生成(記事生成ジョブの rendering 段から直接呼ぶ。§5.3)
# --------------------------------------------------------------------------- #
async def create_overview_figure_v1(
    ctx: dict[str, Any],
    session: AsyncSession,
    *,
    article: Article,
    sources: ArticleSources,
    user: User,
    job: Job,
) -> OverviewFigure:
    return await _create_or_rewrite(
        ctx,
        session,
        article=article,
        sources=sources,
        user=user,
        job=job,
        version=1,
        instruction=None,
        current=None,
    )


async def rewrite_overview_figure(
    ctx: dict[str, Any],
    session: AsyncSession,
    *,
    article: Article,
    sources: ArticleSources,
    user: User,
    job: Job,
    current: OverviewFigure,
    instruction: str | None,
) -> OverviewFigure:
    return await _create_or_rewrite(
        ctx,
        session,
        article=article,
        sources=sources,
        user=user,
        job=job,
        version=current.version + 1,
        instruction=instruction,
        current=current,
    )


async def _create_or_rewrite(
    ctx: dict[str, Any],
    session: AsyncSession,
    *,
    article: Article,
    sources: ArticleSources,
    user: User,
    job: Job,
    version: int,
    instruction: str | None,
    current: OverviewFigure | None,
) -> OverviewFigure:
    router: LLMRouter | None = ctx.get("router")
    if router is None:
        raise RuntimeError("no LLM provider configured (ctx['router'] is None)")

    material_text = build_overview_material_text(sources)
    generated, _resp = await generate_overview_dsl_with_retry(
        router,
        material_text=material_text,
        job=job,
        current_dsl=current.dsl if current is not None else None,
        instruction=instruction,
    )
    date_str = dt.datetime.now(JST).date().isoformat()
    render_dsl = generated.to_render_dsl(generated_by=FOOTER_GENERATED_BY, date=date_str)
    evidence_wire = resolve_evidence_wire(generated.evidence, sources)
    chips = evidence_chip_displays(evidence_wire)
    svg_bytes = render_overview_svg(render_dsl, evidence_chips=chips)

    storage: S3Storage = ctx.get("s3") or S3Storage()
    svg_key = overview_svg_storage_key(str(article.id), version)
    await storage.put(storage.assets_bucket, svg_key, svg_bytes, content_type="image/svg+xml")

    # ラスターモード(設定 llm_routing.overview_figure_raster_mode。既定 false — plans/07 §5.5)。
    # provider/model/prompt 列はラスターモード時のみ埋める(plans/02 §4.11 の DDL コメント)。
    render_mode = "svg"
    image_key: str | None = None
    raster_provider, raster_model, raster_prompt = "", "", ""
    raster = await _maybe_generate_raster(ctx, cards=render_dsl.cards, user=user, job=job)
    if raster is not None:
        image_bytes, raster_provider, raster_model, raster_prompt = raster
        render_mode = "raster"
        image_key = overview_raster_storage_key(str(article.id), version)
        await storage.put(storage.assets_bucket, image_key, image_bytes, content_type="image/png")

    if current is not None:
        current.is_current = False
        session.add(current)

    row = OverviewFigure(
        article_id=str(article.id),
        version=version,
        is_current=True,
        render_mode=render_mode,
        dsl=render_dsl.model_dump(by_alias=True),
        svg_storage_key=svg_key,
        image_storage_key=image_key,
        provider=raster_provider,
        model=raster_model,
        prompt=raster_prompt,
        instruction=instruction or "",
        evidence_anchors=evidence_wire,
    )
    session.add(row)
    await session.flush()
    return row


# --------------------------------------------------------------------------- #
# jobs(kind='figure', payload.figure_kind='overview')ディスパッチ(§5.3 書き直し)
# --------------------------------------------------------------------------- #
async def _load_figure_article_context(
    session: AsyncSession, article_id: str
) -> tuple[Article, LibraryItem, Paper, DocumentRevision, User]:
    article = await session.get(Article, article_id)
    if article is None:
        raise LookupError(f"article not found: {article_id}")
    item = await session.get(LibraryItem, article.library_item_id)
    if item is None:
        raise LookupError(f"library item not found: {article.library_item_id}")
    paper = await session.get(Paper, item.paper_id)
    if paper is None:
        raise LookupError(f"paper/revision not found for library item: {item.id}")
    revision = await get_latest_paper_revision(session, paper)
    if revision is None:
        raise LookupError(f"paper/revision not found for library item: {item.id}")
    user = await session.get(User, item.user_id)
    if user is None:
        raise LookupError(f"user not found: {item.user_id}")
    return article, item, paper, revision, user


async def _current_overview_figure(session: AsyncSession, article_id: str) -> OverviewFigure | None:
    return (
        await session.execute(
            select(OverviewFigure).where(
                OverviewFigure.article_id == article_id, OverviewFigure.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()


async def run_overview_figure_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='figure'`` / ``payload.figure_kind='overview'`` の実処理(書き直し。plans/07 §5.3)。

    ``HANDLERS['figure']`` への登録は :mod:`generate_explainer_figure` の
    ``run_figure_job`` ディスパッチャ経由(main.py への配線は followups)。
    """
    session = store.session
    payload = job.payload or {}
    article_id = str(payload["article_id"])
    instruction = payload.get("instruction")

    article, item, paper, revision, user = await _load_figure_article_context(session, article_id)
    current = await _current_overview_figure(session, article_id)
    if current is None:
        raise LookupError(f"current overview figure not found for article: {article_id}")

    await store.checkpoint(str(job.id), "generating_dsl", progress=30)
    sources = await collect_article_sources(
        session,
        library_item=item,
        paper=paper,
        revision=revision,
        user=user,
        include_math=article.include_math,
    )

    await store.checkpoint(str(job.id), "rendering_svg", progress=70)
    row = await rewrite_overview_figure(
        ctx,
        session,
        article=article,
        sources=sources,
        user=user,
        job=job,
        current=current,
        instruction=str(instruction) if instruction else None,
    )
    await session.commit()
    await store.succeed(str(job.id), {"overview_figure_id": str(row.id), "version": row.version})


__all__ = [
    "MAX_GENERATION_ATTEMPTS",
    "OVERVIEW_FIGURE_DSL_SCHEMA_SPEC",
    "OVERVIEW_SYSTEM_PROMPT",
    "OverviewFigureGenerationError",
    "build_overview_material_text",
    "build_overview_raster_prompt",
    "build_overview_user_prompt",
    "create_overview_figure_v1",
    "evidence_chip_displays",
    "generate_overview_dsl_with_retry",
    "overview_raster_storage_key",
    "overview_svg_storage_key",
    "raster_mode_enabled",
    "resolve_evidence_wire",
    "rewrite_overview_figure",
    "run_overview_figure_job",
    "verify_numeric_tokens",
]
