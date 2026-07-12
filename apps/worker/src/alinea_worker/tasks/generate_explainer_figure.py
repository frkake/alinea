"""解説図ジョブ(plans/07 §6、kind='figure' / payload.figure_kind='explainer')。

- 新規生成は記事生成・再生成に付随する(単体の新規作成 API は無い。docs/07 §1.4)。
  :func:`create_explainer_figures_v1`(初回。version は常に 1)と
  :func:`sync_explainer_figures_for_regenerate`(✦ 指示つき再生成。既存 slot は version+1、
  ``image_brief_en`` から組み立てたプロンプトが現行版と同一なら再利用してコストを節約する —
  plans/07 §4.5 step8)は、記事ジョブ(:mod:`alinea_worker.tasks.generate_article`)の
  rendering 段から呼ぶ(呼び出し配線は generate_article.py 側)。
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
from alinea_core.article import EvidenceDisplayResolver, build_evidence_wire
from alinea_core.article.sources import ArticleSources, collect_article_sources
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
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage
from alinea_llm.errors import ProviderChainExhausted
from alinea_llm.router import ImageRouter
from alinea_llm.types import ImageRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger("alinea.worker")

#: 解説図を論文固有の技術模式図として生成するための共通プリアンブル。
EXPLAINER_STYLE_PREAMBLE = (
    "Precise technical explanatory schematic for a scholarly paper. "
    "Muted low-saturation palette: dusty slate blue (#3E5C76), warm beige (#F4F3EF), "
    "soft sage green (#659471), charcoal gray (#2B2E33) on an off-white background (#FBFAF7). "
    "Use clean geometric components, distinct stages, meaningful directional arrows, consistent "
    "visual encoding, generous whitespace, and a clear left-to-right or top-to-bottom "
    "reading order. "
    "Prefer architecture diagrams, data-flow diagrams, or side-by-side comparisons over decorative "
    "illustrations. Do not use people, scenery, clouds, mist, balance scales, gears, or other "
    "generic metaphors unless they are literal parts of the method. Include only 1 to 5 short "
    "English labels "
    "for paper-specific components when labels improve comprehension. NO paragraphs, NO decorative "
    "text, NO unsupported numbers, NO formulas, NO watermarks, NO logos."
)


def build_explainer_prompt(
    image_brief_en: str,
    *,
    caption_ja: str | None = None,
    instruction: str | None = None,
) -> str:
    """論文固有の構成と因果関係が一目で分かる技術図プロンプトを組み立てる。"""
    prompt = (
        f"{EXPLAINER_STYLE_PREAMBLE}\n\n"
        "Primary objective: make the paper's mechanism understandable without guessing.\n"
        f"Paper-specific diagram brief: {image_brief_en}\n"
        "Composition requirements:\n"
        "- Show the concrete inputs, processing stages, outputs, and comparison conditions "
        "named in the brief.\n"
        "- Use 3 to 7 visually distinct components and connect every dependency with an "
        "explicit arrow.\n"
        "- Make the main novelty or causal relationship visually dominant.\n"
        "- If the brief describes alternatives or baselines, place them in aligned "
        "side-by-side panels.\n"
        "- Do not replace technical components with abstract symbols or atmospheric decoration."
    )
    if caption_ja:
        prompt += f"\nJapanese caption context (use for meaning, not as image text): {caption_ja}"
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

    prompt = build_explainer_prompt(image_brief_en, caption_ja=caption)
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


async def _current_explainer_figure(
    session: AsyncSession, article_id: str, slot: int
) -> ExplainerFigure | None:
    return (
        await session.execute(
            select(ExplainerFigure).where(
                ExplainerFigure.article_id == article_id,
                ExplainerFigure.slot == slot,
                ExplainerFigure.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()


async def sync_explainer_figures_for_regenerate(
    ctx: dict[str, Any],
    session: AsyncSession,
    *,
    article: Article,
    sources: ArticleSources,
    job: Job,
    briefs: list[ExplainerBrief],
) -> dict[int, ExplainerFigure]:
    """記事 ✦指示つき再生成の rendering 段で slot ごとに解説図を同期する(plans/07 §4.5 step8)。

    ``image_brief_en`` から組み立てたプロンプトが現行版(is_current な行)の ``prompt`` と
    一致する slot は画像を再生成せず再利用する(キャプション・根拠のみ最新化)。異なる場合・
    新規 slot の場合のみ新版を生成し、既存版の ``(article_id, slot, version)`` と衝突しない
    ``current.version + 1``(新規なら 1)を採番する — :func:`create_explainer_figures_v1` を
    再生成経路にそのまま使うと version=1 固定のため一意制約に衝突する既知問題の解消。

    画像生成チェーン全滅時は該当 slot をスキップし記事ジョブ自体は成功させる(部分成功)。
    """
    updated: dict[int, ExplainerFigure] = {}
    for brief in briefs:
        current = await _current_explainer_figure(session, str(article.id), brief.slot)
        candidate_prompt = build_explainer_prompt(
            brief.image_brief_en,
            caption_ja=current.caption if current is not None else brief.caption_ja,
        )
        evidence_wire = resolve_evidence_wire(list(brief.evidence), sources)
        if current is not None and current.prompt == candidate_prompt:
            # 再利用: image_brief_en 不変 → 画像は生成し直さず、キャプション・根拠のみ更新する。
            current.caption = brief.caption_ja
            current.evidence_anchors = evidence_wire
            session.add(current)
            await session.flush()
            updated[brief.slot] = current
            continue
        try:
            row = await _generate_and_store(
                ctx,
                session,
                article=article,
                job=job,
                slot=brief.slot,
                version=(current.version + 1) if current is not None else 1,
                image_brief_en=brief.image_brief_en,
                caption=brief.caption_ja,
                evidence_wire=evidence_wire,
                current=current,
            )
        except ProviderChainExhausted as exc:
            await log.awarning(
                "explainer_figure_regenerate_failed",
                article_id=str(article.id),
                slot=brief.slot,
                error=str(exc),
            )
            continue
        updated[brief.slot] = row
    return updated


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
    if paper is None:
        raise LookupError(f"paper/revision not found for library item: {item.id}")
    revision = await get_latest_paper_revision(session, paper)
    if revision is None:
        raise LookupError(f"paper/revision not found for library item: {item.id}")
    user = await session.get(User, item.user_id)
    if user is None:
        raise LookupError(f"user not found: {item.user_id}")
    return article, item, paper, revision, user


async def _find_explainer_image_brief_en(
    session: AsyncSession, article_id: str, slot: int
) -> str | None:
    """現行記事ブロック(``article_blocks.content.explainer.image_brief_en``)から、
    記事生成時にモデルへ渡した英語ブリーフを再取得する(plans/07 §4.3 の永続化。
    figures レーン deviations #6 の解消: 単体再生成でも忠実に同一ブリーフを再現できる)。

    ブロックが見つからない(削除済み等)場合は None を返し、呼び出し側でキャプションを
    代替に使う(既存挙動を保つフォールバック)。
    """
    rows = (
        (
            await session.execute(
                select(ArticleBlock).where(
                    ArticleBlock.article_id == article_id,
                    ArticleBlock.type == "explainer_figure",
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        content = row.content or {}
        if int(content.get("slot", -1)) == slot:
            brief = content.get("image_brief_en")
            return str(brief) if brief else None
    return None


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
    # ``image_brief_en`` は article_blocks.content.explainer に永続化済み(§4.3)のため、
    # 単体再生成でも記事生成時と同一のブリーフを忠実に再現できる(見つからない場合のみ
    # キャプションを代替に使う — ブロック削除後の再生成等のフォールバック)。
    image_brief_en = await _find_explainer_image_brief_en(session, str(article.id), current.slot)
    prompt = build_explainer_prompt(
        image_brief_en or current.caption or "the concept of this figure",
        caption_ja=current.caption,
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
    from alinea_worker.tasks.generate_overview_figure import run_overview_figure_job

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
    "sync_explainer_figures_for_regenerate",
]
