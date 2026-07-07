"""解説図ジョブ(plans/07 §6、kind='figure' / payload.figure_kind='explainer')。

- 新規生成は記事生成・再生成に付随する(単体の新規作成 API は無い。docs/07 §1.4)。
  :func:`create_explainer_figures_v1` を記事ジョブ(:mod:`yakudoku_worker.tasks.
  generate_article`)の rendering 段から呼ぶ想定 — **その呼び出し配線は generate_article.py
  側の担当(M2-03/04 レーン)であり、本ファイルの所有範囲外のため followups に記載する。**
- 単体再生成(``POST /api/explainer-figures/{figure_id}/regenerate``)は
  :func:`run_explainer_figure_job` が ``jobs(kind='figure')`` として処理する(plans/03 §20.2)。
- ``run_figure_job`` は ``kind='figure'`` 全体のディスパッチャ(``payload.figure_kind`` で
  overview/explainer に振り分ける)。``HANDLERS['figure'] = run_figure_job`` の main.py 登録は
  followups へ。

ImageRouter は ``ctx['image_router']`` から注入する(apps 間 import を避けるための DI)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.article import EvidenceDisplayResolver, build_evidence_wire
from yakudoku_core.article.sources import ArticleSources, collect_article_sources
from yakudoku_core.db.models import (
    Article,
    DocumentRevision,
    ExplainerFigure,
    Job,
    LibraryItem,
    Paper,
    User,
)
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.storage.s3 import S3Storage
from yakudoku_llm.errors import ProviderChainExhausted
from yakudoku_llm.router import ImageRouter
from yakudoku_llm.types import ImageRequest

log = structlog.get_logger("yakudoku.worker")

#: 画像プロンプト構成規則(plans/07 §6.2 逐語)。
#: 概要図ラスターモード(§5.5)も同一プリアンブルを再利用する。
EXPLAINER_STYLE_PREAMBLE = (
    "Flat editorial illustration for a calm, scholarly reading app. "
    "Muted low-saturation palette: dusty slate blue (#3E5C76), warm beige (#F4F3EF), "
    "soft sage green (#659471), charcoal gray (#2B2E33) on an off-white background (#FBFAF7). "
    "Clean geometric shapes, thin lines, generous whitespace, subtle depth. "
    "Strictly NO text, NO letters, NO digits, NO formulas, NO labels, NO watermarks, NO logos."
)


def build_explainer_prompt(image_brief_en: str, *, instruction: str | None = None) -> str:
    """plans/07 §6.2 逐語。``instruction`` があれば §6.1 の逐語テンプレートを追記する。"""
    prompt = f"{EXPLAINER_STYLE_PREAMBLE}\n\nConcept to illustrate: {image_brief_en}"
    if instruction:
        prompt += (
            "\n\nRevision request — follow this Japanese instruction from the user: "
            f"「{instruction}」"
        )
    return prompt


def explainer_image_storage_key(explainer_figure_id: str, version: int) -> str:
    return f"renders/explainer/{explainer_figure_id}/v{version}.png"


@dataclass(frozen=True)
class ExplainerBrief:
    """記事ブロック(type='explainer_figure')の LLM 出力から抽出する生成材料(plans/07 §4.3)。"""

    slot: int
    image_brief_en: str
    caption_ja: str
    evidence: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 根拠アンカー解決(overview 側と同一規則。plans/07 §4.3)
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
    anchor_dicts = _evidence_anchor_dicts(evidence, sources)
    resolver = EvidenceDisplayResolver(sources.content)
    return build_evidence_wire(anchor_dicts, resolver)


# --------------------------------------------------------------------------- #
# 生成(初回・再生成 共通)
# --------------------------------------------------------------------------- #
async def _generate_and_store(
    ctx: dict[str, Any],
    session: AsyncSession,
    *,
    article: Article,
    job: Job,
    slot: int,
    version: int,
    image_brief_en: str,
    caption: str,
    evidence_wire: list[dict[str, Any]],
    current: ExplainerFigure | None,
) -> ExplainerFigure:
    image_router: ImageRouter | None = ctx.get("image_router")
    if image_router is None:
        raise RuntimeError("no image provider configured (ctx['image_router'] is None)")

    prompt = build_explainer_prompt(image_brief_en)
    result = await image_router.generate(
        prompt,
        task="explainer_image",
        request=ImageRequest(model="", prompt=prompt),
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
    )

    if current is not None:
        current.is_current = False
        session.add(current)

    row = ExplainerFigure(
        article_id=str(article.id),
        slot=slot,
        version=version,
        is_current=True,
        provider=result.provider,
        model=result.model,
        prompt=prompt,
        image_storage_key="",  # 直後に確定 id で書き直す(下記)
        caption=caption,
        evidence_anchors=evidence_wire,
    )
    session.add(row)
    await session.flush()

    storage: S3Storage = ctx.get("s3") or S3Storage()
    key = explainer_image_storage_key(str(row.id), version)
    await storage.put(storage.assets_bucket, key, result.image_bytes, content_type="image/png")
    row.image_storage_key = key
    await session.flush()
    return row


async def create_explainer_figures_v1(
    ctx: dict[str, Any],
    session: AsyncSession,
    *,
    article: Article,
    sources: ArticleSources,
    job: Job,
    briefs: list[ExplainerBrief],
) -> dict[int, ExplainerFigure]:
    """記事初回・再生成の rendering 段で slot ごとに v1 を生成する(plans/07 §6.1)。

    画像生成チェーン全滅(:class:`ProviderChainExhausted`)は該当 slot をスキップし、記事ジョブ
    自体は成功させる(部分成功。docs/09 §2)。失敗は呼び出し側の処理ログに委ねる。
    """
    created: dict[int, ExplainerFigure] = {}
    for brief in briefs:
        evidence_wire = resolve_evidence_wire(list(brief.evidence), sources)
        try:
            row = await _generate_and_store(
                ctx,
                session,
                article=article,
                job=job,
                slot=brief.slot,
                version=1,
                image_brief_en=brief.image_brief_en,
                caption=brief.caption_ja,
                evidence_wire=evidence_wire,
                current=None,
            )
        except ProviderChainExhausted as exc:
            await log.awarning(
                "explainer_figure_generation_failed",
                article_id=str(article.id),
                slot=brief.slot,
                error=str(exc),
            )
            continue
        created[brief.slot] = row
    return created


# --------------------------------------------------------------------------- #
# jobs(kind='figure', payload.figure_kind='explainer')ディスパッチ(単体再生成。plans/03 §20.2)
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
    if paper is None or paper.latest_revision_id is None:
        raise LookupError(f"paper/revision not found for library item: {item.id}")
    revision = await session.get(DocumentRevision, paper.latest_revision_id)
    if revision is None:
        raise LookupError(f"revision not found: {paper.latest_revision_id}")
    user = await session.get(User, item.user_id)
    if user is None:
        raise LookupError(f"user not found: {item.user_id}")
    return article, item, paper, revision, user


async def run_explainer_figure_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='figure'`` / ``payload.figure_kind='explainer'`` の実処理(単体再生成)。"""
    session = store.session
    payload = job.payload or {}
    figure_id = str(payload["figure_id"])
    instruction = payload.get("instruction")

    current = await session.get(ExplainerFigure, figure_id)
    if current is None:
        raise LookupError(f"explainer figure not found: {figure_id}")
    article, item, paper, revision, user = await _load_figure_article_context(
        session, str(current.article_id)
    )

    await store.checkpoint(str(job.id), "generating", progress=40)
    sources = await collect_article_sources(
        session,
        library_item=item,
        paper=paper,
        revision=revision,
        user=user,
        include_math=article.include_math,
    )
    evidence_refs = [str(a.get("block_id", "")) for a in (current.evidence_anchors or [])]
    evidence_wire = resolve_evidence_wire(evidence_refs, sources)

    image_router: ImageRouter | None = ctx.get("image_router")
    if image_router is None:
        raise RuntimeError("no image provider configured (ctx['image_router'] is None)")
    # ``image_brief_en`` は記事生成時のみモデルへ渡す材料(§4.3)であり再生成時は保存済みプロンプト
    # から Concept 節を再利用できないため、直近キャプションを英語ブリーフの代替として使う
    # (deviations: 厳密には image_brief_en の保存が必要。followups 参照)。
    prompt = build_explainer_prompt(
        current.caption or "the concept of this figure",
        instruction=str(instruction) if instruction else None,
    )

    await store.checkpoint(str(job.id), "rendering", progress=80)
    result = await image_router.generate(
        prompt,
        task="explainer_image",
        request=ImageRequest(model="", prompt=prompt),
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
    )

    current.is_current = False
    session.add(current)
    new_version = current.version + 1
    row = ExplainerFigure(
        article_id=str(article.id),
        slot=current.slot,
        version=new_version,
        is_current=True,
        provider=result.provider,
        model=result.model,
        prompt=prompt,
        image_storage_key="",
        caption=current.caption,
        evidence_anchors=evidence_wire or current.evidence_anchors,
    )
    session.add(row)
    await session.flush()

    storage: S3Storage = ctx.get("s3") or S3Storage()
    key = explainer_image_storage_key(str(row.id), new_version)
    await storage.put(storage.assets_bucket, key, result.image_bytes, content_type="image/png")
    row.image_storage_key = key
    await session.flush()

    await session.commit()
    await store.succeed(str(job.id), {"explainer_figure_id": str(row.id), "version": row.version})


# --------------------------------------------------------------------------- #
# kind='figure' 全体のディスパッチャ(payload.figure_kind で振り分け)
# --------------------------------------------------------------------------- #
async def run_figure_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``HANDLERS['figure']`` に登録する想定のディスパッチャ(main.py 配線は followups)。"""
    from yakudoku_worker.tasks.generate_overview_figure import run_overview_figure_job

    payload = job.payload or {}
    figure_kind = str(payload.get("figure_kind", ""))
    if figure_kind == "overview":
        await run_overview_figure_job(ctx, store, job)
    elif figure_kind == "explainer":
        await run_explainer_figure_job(ctx, store, job)
    else:
        raise NotImplementedError(f"figure_kind not supported: {figure_kind}")


__all__ = [
    "EXPLAINER_STYLE_PREAMBLE",
    "ExplainerBrief",
    "build_explainer_prompt",
    "create_explainer_figures_v1",
    "explainer_image_storage_key",
    "resolve_evidence_wire",
    "run_explainer_figure_job",
    "run_figure_job",
]
