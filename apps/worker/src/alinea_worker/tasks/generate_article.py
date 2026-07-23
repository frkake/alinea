"""記事生成ジョブ(plans/07 §4)。``jobs.kind = 'article'`` の全用途は ``payload.op`` で判別する。

- ``generate``: 初回生成(plans/03 §19.2)。stage: ``collecting_sources → generating → rendering
  → complete``。
- ``regenerate``: ✦指示つき再生成(§19.3)。version+1・instruction 指定時のみ
  ``instructions_history`` に追記(§4.6)。
- ``block_rewrite``: ホバーツールバーからのブロック単体書き直し・指示なし再生成(§4.8・§19.5)。
  記事 version は変えない。

LLMRouter は ``ctx['router']`` から注入する(apps 間 import を避けるための DI。translate.py
と同じ規約)。素材収集・検証・正規化のロジックは :mod:`alinea_core.article` に委譲する。

図の生成(plans/07 §4.5 step8)は本ファイルが :mod:`alinea_worker.tasks.
generate_overview_figure` / :mod:`alinea_worker.tasks.generate_explainer_figure` を呼び出して
配線する(初回生成時のみ全体概要図 v1 を生成し記事単体で概要図は作らない — §5.3。解説図は初回は
v1、✦ 指示つき再生成は version-aware に同期する — §4.5 step8)。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import structlog
from alinea_core.article import (
    ARTICLE_BLOCK_SCHEMA_SPEC,
    ARTICLE_SCHEMA_SPEC,
    PRESET_INCLUDE_MATH_DEFAULT,
    ArticleGenerationError,
    ArticleSources,
    BlockTypeMismatchError,
    NormalizedArticle,
    NormalizedBlock,
    build_article_block_system_prompt,
    build_article_block_wire,
    build_article_system_prompt,
    build_article_user_prompt,
    build_attribution_block,
    build_block_rewrite_user_prompt,
    build_regenerate_suffix,
    collect_article_sources,
    normalize_article,
    normalize_rewritten_block,
)
from alinea_core.article.storage_keys import article_snapshot_key, article_versions_cache_key
from alinea_core.article.wire import EvidenceDisplayResolver
from alinea_core.db.models import (
    Article,
    ArticleBlock,
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    User,
)
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import article_block_to_plain
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage
from alinea_llm.errors import ProviderChainExhausted
from alinea_llm.router import LLMRouter
from alinea_llm.types import ContentPart, LLMRequest, LLMResponse, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_worker.tasks.generate_explainer_figure import (
    ExplainerBrief,
    create_explainer_figures_v1,
    sync_explainer_figures_for_regenerate,
)
from alinea_worker.tasks.generate_overview_figure import (
    OverviewFigureGenerationError,
    create_overview_figure_v1,
)

log = structlog.get_logger("alinea.worker")

MAX_STRUCTURAL_RETRIES = 1  # §4.3: discussion 欠落等は「生成失敗として再試行 1 回」


# ---------------------------------------------------------------------------
# コンテキスト読み込み
# ---------------------------------------------------------------------------
async def _load_library_item_context(
    session: AsyncSession, library_item_id: str
) -> tuple[LibraryItem, Paper, DocumentRevision, User]:
    item = await session.get(LibraryItem, library_item_id)
    if item is None:
        raise LookupError(f"library item not found: {library_item_id}")
    paper = await session.get(Paper, item.paper_id)
    if paper is None or paper.latest_revision_id is None:
        raise LookupError(f"paper/revision not found for library item: {library_item_id}")
    revision = await get_latest_paper_revision(session, paper)
    if revision is None:
        raise LookupError(f"revision not found: {paper.latest_revision_id}")
    user = await session.get(User, item.user_id)
    if user is None:
        raise LookupError(f"user not found: {item.user_id}")
    return item, paper, revision, user


async def _load_article_context(
    session: AsyncSession, article_id: str
) -> tuple[Article, LibraryItem, Paper, DocumentRevision, User]:
    article = await session.get(Article, article_id)
    if article is None:
        raise LookupError(f"article not found: {article_id}")
    item, paper, revision, user = await _load_library_item_context(
        session, str(article.library_item_id)
    )
    return article, item, paper, revision, user


async def _current_blocks(session: AsyncSession, article_id: str) -> list[ArticleBlock]:
    rows = (
        (
            await session.execute(
                select(ArticleBlock)
                .where(ArticleBlock.article_id == article_id)
                .order_by(ArticleBlock.position.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _collect_user_highlight_ids(blocks: list[ArticleBlock]) -> frozenset[str]:
    ids: set[str] = set()
    for blk in blocks:
        if blk.type != "discussion":
            continue
        for item in (blk.content or {}).get("items", []):
            if isinstance(item, dict) and item.get("origin") == "user_highlight":
                aid = item.get("annotation_id")
                if aid:
                    ids.add(str(aid))
    return frozenset(ids)


def _dump_blocks_plain(blocks: list[ArticleBlock]) -> str:
    lines: list[str] = []
    for blk in blocks:
        plain = article_block_to_plain(blk.type, blk.content or {})
        lines.append(f"[{blk.type}] {plain}" if plain else f"[{blk.type}]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 図の生成(§4.5 step8: 全体概要図・解説図)
# ---------------------------------------------------------------------------
def _explainer_briefs_from_normalized(blocks: list[NormalizedBlock]) -> list[ExplainerBrief]:
    """正規化済み記事ブロックから解説図の生成材料を抽出する(§4.3)。

    ``evidence_anchors`` は既に :mod:`alinea_core.article.postprocess` の
    ``_evidence_anchors`` で実在検証済みの anchor dict(``block_id`` = 元の evidence 参照)
    なので、そこから参照文字列を復元して :class:`ExplainerBrief.evidence` に渡す。
    """
    briefs: list[ExplainerBrief] = []
    for block in blocks:
        if block.type != "explainer_figure":
            continue
        briefs.append(
            ExplainerBrief(
                slot=int(block.content.get("slot", 0)),
                image_brief_en=str(block.content.get("image_brief_en", "")),
                caption_ja=str(block.content.get("caption_ja", "")),
                evidence=[str(a.get("block_id", "")) for a in block.evidence_anchors],
            )
        )
    return briefs


async def _generate_explainer_figures(
    ctx: dict[str, Any],
    store: JobStore,
    *,
    article: Article,
    sources: ArticleSources,
    job: Job,
    briefs: list[ExplainerBrief],
    is_regenerate: bool,
) -> None:
    """explainer_figure ブロックの briefs から解説図を生成する(§4.5 step8)。

    ``ctx['image_router']`` が無い場合でも記事生成自体は成功させ、部分失敗として
    ジョブログに記録する(P3。docs/07 §1.4 の「オンデマンド」方針とプロバイダ未構成環境の両立)。
    """
    if not briefs:
        return
    if ctx.get("image_router") is None:
        await store.record_partial_failure(
            str(job.id),
            "rendering",
            {
                "reason": "explainer_image_provider_not_configured",
                "slots": [b.slot for b in briefs],
            },
        )
        return
    if is_regenerate:
        await sync_explainer_figures_for_regenerate(
            ctx, store.session, article=article, sources=sources, job=job, briefs=briefs
        )
    else:
        await create_explainer_figures_v1(
            ctx, store.session, article=article, sources=sources, job=job, briefs=briefs
        )


async def _generate_overview_figure_v1(
    ctx: dict[str, Any],
    store: JobStore,
    *,
    article: Article,
    sources: ArticleSources,
    user: User,
    job: Job,
) -> None:
    """記事初回生成の rendering 段で全体概要図 v1 を生成する(§5.3。記事単体では生成しない)。

    DSL 生成の数値照合エラー・プロバイダチェーン全滅は記事生成自体を失敗させず、部分失敗として
    記録する(初回生成には「既存版」が無く §5.2 の版保持ができないため、記事は概要図無しで
    成功させ、後から「✦ 書き直し指示」相当の再生成で作り直せるようにする — P3)。
    """
    try:
        router = await ctx["user_router_factory"].for_job(
            user_id=str(job.user_id), task="overview_figure_dsl"
        )
        await create_overview_figure_v1(
            ctx, store.session, router=router, article=article, sources=sources, user=user, job=job
        )
    except (OverviewFigureGenerationError, ProviderChainExhausted) as exc:
        await store.record_partial_failure(
            str(job.id),
            "rendering",
            {"reason": "overview_figure_generation_failed", "detail": str(exc)},
        )


# ---------------------------------------------------------------------------
# LLM 呼び出し(structured)
# ---------------------------------------------------------------------------
async def _call_structured(
    router: LLMRouter,
    *,
    task: str,
    system: str,
    user_text: str,
    schema: Any,
    job: Job,
) -> LLMResponse:
    request = LLMRequest(
        model="",
        system=[ContentPart.from_text(system, cache_hint=True)],
        messages=[Message(role="user", parts=[ContentPart.from_text(user_text)])],
        max_output_tokens=12000,
        effort="medium",
        timeout_s=120.0,
        metadata={"task": task},
    )
    resp = await router.complete(
        task,
        schema=schema,
        mode="structured",
        request=request,
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
    )
    assert resp.parsed is not None  # generate_structured は必ず parsed を埋める(§12)
    return resp


async def _generate_with_retry(
    router: LLMRouter,
    *,
    system: str,
    user_text: str,
    sources: ArticleSources,
    job: Job,
    previous_user_highlight_ids: frozenset[str] = frozenset(),
) -> tuple[NormalizedArticle, LLMResponse]:
    """§4.3: discussion 欠落などの構造検証失敗は 1 回だけ同一プロンプトで再試行する。"""
    last_err: ArticleGenerationError | None = None
    for _attempt in range(MAX_STRUCTURAL_RETRIES + 1):
        resp = await _call_structured(
            router,
            task="article",
            system=system,
            user_text=user_text,
            schema=ARTICLE_SCHEMA_SPEC,
            job=job,
        )
        assert resp.parsed is not None
        try:
            return (
                normalize_article(
                    resp.parsed, sources, previous_user_highlight_ids=previous_user_highlight_ids
                ),
                resp,
            )
        except ArticleGenerationError as exc:
            last_err = exc
    assert last_err is not None
    raise last_err


async def _rewrite_with_retry(
    router: LLMRouter,
    *,
    system: str,
    user_text: str,
    sources: ArticleSources,
    expected_type: str,
    job: Job,
    previous_user_highlight_ids: frozenset[str] = frozenset(),
) -> NormalizedBlock:
    """§4.8: type 変更は許可しない。不一致・検証失敗は 1 回だけ再試行する。"""
    last_err: Exception | None = None
    for _attempt in range(MAX_STRUCTURAL_RETRIES + 1):
        resp = await _call_structured(
            router,
            task="article",
            system=system,
            user_text=user_text,
            schema=ARTICLE_BLOCK_SCHEMA_SPEC,
            job=job,
        )
        assert resp.parsed is not None
        try:
            return normalize_rewritten_block(
                resp.parsed,
                sources,
                expected_type=expected_type,
                previous_user_highlight_ids=previous_user_highlight_ids,
            )
        except (BlockTypeMismatchError, ArticleGenerationError) as exc:
            last_err = exc
    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# DB 書き込み・版スナップショット(§4.5 step5-7、§4.6)
# ---------------------------------------------------------------------------
def _row_from_normalized(article_id: str, position: int, block: NormalizedBlock) -> ArticleBlock:
    return ArticleBlock(
        article_id=article_id,
        position=position,
        type=block.type,
        content=block.content,
        text_plain=article_block_to_plain(block.type, block.content),
        evidence_anchors=block.evidence_anchors,
        origin=block.origin,
    )


async def _replace_blocks(
    session: AsyncSession, article: Article, normalized: NormalizedArticle, paper: Paper
) -> list[ArticleBlock]:
    await session.execute(delete(ArticleBlock).where(ArticleBlock.article_id == article.id))
    await session.flush()
    all_blocks = [*normalized.blocks, build_attribution_block(paper)]
    rows = [
        _row_from_normalized(str(article.id), position, block)
        for position, block in enumerate(all_blocks)
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def _save_snapshot(
    ctx: dict[str, Any],
    article: Article,
    rows: list[ArticleBlock],
    *,
    preset: str,
    include_math: bool,
    instruction: str | None,
) -> None:
    """版スナップショットを S3 に保存し、Redis の版メタ一覧キャッシュへ追記する(§4.6)。

    Redis 追記はベストエフォート(キャッシュミス時は空一覧を返す — followup: S3 一覧操作が
    :class:`~alinea_core.storage.s3.S3Storage` に無いため走査フォールバックは未実装)。
    """
    storage = ctx.get("s3") or S3Storage()
    generated_at = article.generated_at or dt.datetime.now(dt.UTC)
    snapshot = {
        "version": article.version,
        "generated_at": generated_at.isoformat(),
        "preset": preset,
        "include_math": include_math,
        "instruction": instruction,
        "title": article.title,
        "blocks": [
            {
                "position": row.position,
                "type": row.type,
                "content": row.content,
                "evidence_anchors": row.evidence_anchors,
                "origin": row.origin,
            }
            for row in rows
        ],
    }
    key = article_snapshot_key(str(article.id), article.version)
    body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
    await storage.put(storage.assets_bucket, key, body, content_type="application/json")

    redis = ctx.get("redis")
    if redis is not None:
        meta = json.dumps(
            {
                "version": article.version,
                "generated_at": snapshot["generated_at"],
                "preset": preset,
                "instruction": instruction,
            },
            ensure_ascii=False,
        )
        try:
            await redis.rpush(article_versions_cache_key(str(article.id)), meta)
        except Exception as exc:  # ベストエフォートキャッシュ(§4.6)。失敗してもジョブは継続する。
            log.warning("article_version_cache_append_failed", error=str(exc))


async def _update_snapshot_current_version(
    ctx: dict[str, Any], article: Article, session: AsyncSession
) -> None:
    """ブロック単体書き直し後、現行版スナップショットを上書き更新する(§4.6)。"""
    rows = await _current_blocks(session, str(article.id))
    await _save_snapshot(
        ctx,
        article,
        rows,
        preset=article.preset,
        include_math=article.include_math,
        instruction=None,
    )


# ---------------------------------------------------------------------------
# op='generate'(初回生成。plans/03 §19.2)
# ---------------------------------------------------------------------------
async def _run_generate(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    session = store.session
    payload = job.payload or {}
    library_item_id = str(payload.get("library_item_id") or job.library_item_id)
    item, paper, revision, user = await _load_library_item_context(session, library_item_id)

    preset = str(payload.get("preset", "beginner"))
    include_math = bool(payload.get("include_math", PRESET_INCLUDE_MATH_DEFAULT.get(preset, False)))

    existing = (
        await session.execute(
            select(Article).where(Article.library_item_id == str(item.id), Article.preset == preset)
        )
    ).scalar_one_or_none()
    if existing is not None:
        job.article_id = str(existing.id)
        await session.commit()
        await store.succeed(
            str(job.id),
            {"article_id": str(existing.id), "version": existing.version, "preset": preset},
        )
        return

    await store.checkpoint(str(job.id), "collecting_sources", progress=10)
    sources = await collect_article_sources(
        session,
        library_item=item,
        paper=paper,
        revision=revision,
        user=user,
        include_math=include_math,
    )

    await store.checkpoint(str(job.id), "generating", progress=40)
    router = await ctx["user_router_factory"].for_job(user_id=str(job.user_id), task="article")
    system = build_article_system_prompt(preset, include_math=include_math)
    user_text = build_article_user_prompt(sources)
    normalized, resp = await _generate_with_retry(
        router, system=system, user_text=user_text, sources=sources, job=job
    )

    await store.checkpoint(str(job.id), "rendering", progress=80)
    article = Article(
        id=str(uuid.uuid4()),
        library_item_id=str(item.id),
        title=normalized.title,
        preset=preset,
        include_math=include_math,
        version=1,
        provider=resp.provider,
        model=resp.model,
    )
    session.add(article)
    await session.flush()
    rows = await _replace_blocks(session, article, normalized, paper)
    await _save_snapshot(
        ctx, article, rows, preset=preset, include_math=include_math, instruction=None
    )

    # 図の生成(§4.5 step8): 初回生成時のみ全体概要図 v1 を生成し、explainer_figure ブロックの
    # 各 slot について解説図 v1 を生成する(§5.3・§6.1)。いずれもプロバイダ未構成・生成失敗は
    # 記事生成自体を失敗させない(部分成功。P3)。
    await _generate_overview_figure_v1(
        ctx, store, article=article, sources=sources, user=user, job=job
    )
    explainer_briefs = _explainer_briefs_from_normalized(normalized.blocks)
    await _generate_explainer_figures(
        ctx,
        store,
        article=article,
        sources=sources,
        job=job,
        briefs=explainer_briefs,
        is_regenerate=False,
    )

    await session.commit()
    await store.succeed(str(job.id), {"article_id": str(article.id), "version": article.version})


# ---------------------------------------------------------------------------
# op='regenerate'(✦ 指示つき再生成。plans/03 §19.3)
# ---------------------------------------------------------------------------
async def _run_regenerate(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    session = store.session
    payload = job.payload or {}
    article_id = str(payload["article_id"])
    article, item, paper, revision, user = await _load_article_context(session, article_id)

    preset = str(payload.get("preset") or article.preset)
    creates_variant = preset != article.preset
    if payload.get("include_math") is not None:
        include_math = bool(payload["include_math"])
    elif payload.get("preset") is not None:
        include_math = PRESET_INCLUDE_MATH_DEFAULT.get(preset, article.include_math)
    else:
        include_math = article.include_math
    instruction = str(payload.get("instruction") or "").strip()

    await store.checkpoint(str(job.id), "collecting_sources", progress=10)
    sources = await collect_article_sources(
        session,
        library_item=item,
        paper=paper,
        revision=revision,
        user=user,
        include_math=include_math,
    )

    existing_blocks = await _current_blocks(session, str(article.id))
    previous_user_highlight_ids = _collect_user_highlight_ids(existing_blocks)
    instructions_history = list(article.instructions_history or [])

    regen_suffix = None
    if instruction:
        regen_suffix = build_regenerate_suffix(
            instructions_history=[str(i) for i in instructions_history],
            instruction=instruction,
            current_article_plain=_dump_blocks_plain(existing_blocks),
        )

    await store.checkpoint(str(job.id), "generating", progress=40)
    router = await ctx["user_router_factory"].for_job(user_id=str(job.user_id), task="article")
    system = build_article_system_prompt(preset, include_math=include_math)
    user_text = build_article_user_prompt(sources, regenerate_suffix=regen_suffix)
    normalized, resp = await _generate_with_retry(
        router,
        system=system,
        user_text=user_text,
        sources=sources,
        job=job,
        previous_user_highlight_ids=previous_user_highlight_ids,
    )

    await store.checkpoint(str(job.id), "rendering", progress=80)
    if creates_variant:
        article = Article(
            id=str(uuid.uuid4()),
            library_item_id=str(item.id),
            title=normalized.title,
            preset=preset,
            include_math=include_math,
            version=1,
            provider=resp.provider,
            model=resp.model,
            instructions_history=[instruction] if instruction else [],
        )
        session.add(article)
        job.article_id = str(article.id)
    else:
        article.title = normalized.title
        article.include_math = include_math
        article.version = article.version + 1
        article.generated_at = dt.datetime.now(dt.UTC)
        article.provider = resp.provider
        article.model = resp.model
        if instruction:
            article.instructions_history = [*instructions_history, instruction]
    await session.flush()
    rows = await _replace_blocks(session, article, normalized, paper)
    await _save_snapshot(
        ctx,
        article,
        rows,
        preset=preset,
        include_math=include_math,
        instruction=instruction or None,
    )

    if creates_variant:
        await _generate_overview_figure_v1(
            ctx, store, article=article, sources=sources, user=user, job=job
        )

    # 同じ読者タイプの再生成では概要図を維持する。別タイプへの変更は新しい記事として作成し、
    # その記事専用の概要図・解説図を初回生成する。
    explainer_briefs = _explainer_briefs_from_normalized(normalized.blocks)
    await _generate_explainer_figures(
        ctx,
        store,
        article=article,
        sources=sources,
        job=job,
        briefs=explainer_briefs,
        is_regenerate=not creates_variant,
    )

    await session.commit()
    await store.succeed(str(job.id), {"article_id": str(article.id), "version": article.version})


# ---------------------------------------------------------------------------
# op='block_rewrite'(§4.8・§19.5)
# ---------------------------------------------------------------------------
async def _run_block_rewrite(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    session = store.session
    payload = job.payload or {}
    article_id = str(payload["article_id"])
    block_pk = int(payload["block_pk"])
    instruction = payload.get("instruction")

    article, item, paper, revision, user = await _load_article_context(session, article_id)
    target = await session.get(ArticleBlock, block_pk)
    if target is None or str(target.article_id) != str(article.id):
        raise LookupError(f"article block not found: {block_pk}")
    if target.type == "attribution":
        # API 層が 403 で先に弾く想定(plans/03 §19.5)。ここに到達したら防御的に中断する。
        raise PermissionError("attribution block is locked")

    all_blocks = await _current_blocks(session, str(article.id))
    headings_outline = "\n".join(
        f"- {b.content.get('text', '')}" for b in all_blocks if b.type == "heading"
    )
    idx = next((i for i, b in enumerate(all_blocks) if b.id == target.id), None)
    neighbors = []
    if idx is not None:
        for i in (idx - 1, idx + 1):
            if 0 <= i < len(all_blocks):
                neighbors.append(all_blocks[i])
    neighbor_plain = _dump_blocks_plain(neighbors)
    target_wire = build_article_block_wire(
        pk=target.id,
        type_=target.type,
        content=target.content or {},
        evidence_anchors=target.evidence_anchors or [],
        origin=target.origin,
        resolver=EvidenceDisplayResolver(DocumentContent.model_validate(revision.content)),
    )
    target_json = json.dumps(target_wire, ensure_ascii=False)

    include_math = article.include_math
    await store.checkpoint(str(job.id), "collecting_sources", progress=10)
    sources = await collect_article_sources(
        session,
        library_item=item,
        paper=paper,
        revision=revision,
        user=user,
        include_math=include_math,
    )

    excerpt_lines: list[str] = []
    for anchor in target.evidence_anchors or []:
        if not isinstance(anchor, dict):
            continue
        bid = str(anchor.get("block_id", ""))
        source_text = sources.block_source_text.get(bid)
        if source_text is not None:
            excerpt_lines.append(f"[{bid}] {source_text}")
    evidence_source_excerpt = "\n".join(excerpt_lines)

    previous_user_highlight_ids = (
        _collect_user_highlight_ids([target]) if target.type == "discussion" else frozenset()
    )

    await store.checkpoint(str(job.id), "generating", progress=40)
    router = await ctx["user_router_factory"].for_job(user_id=str(job.user_id), task="article")
    system = build_article_block_system_prompt(include_math=include_math)
    user_text = build_block_rewrite_user_prompt(
        headings_outline=headings_outline or "(なし)",
        neighbor_blocks_plain=neighbor_plain or "(なし)",
        target_block_json=target_json,
        evidence_source_excerpt=evidence_source_excerpt or "(なし)",
        instruction=str(instruction) if instruction else None,
    )
    normalized_block = await _rewrite_with_retry(
        router,
        system=system,
        user_text=user_text,
        sources=sources,
        expected_type=target.type,
        job=job,
        previous_user_highlight_ids=previous_user_highlight_ids,
    )

    await store.checkpoint(str(job.id), "rendering", progress=80)
    target.content = normalized_block.content
    target.evidence_anchors = normalized_block.evidence_anchors
    target.text_plain = article_block_to_plain(normalized_block.type, normalized_block.content)
    await session.flush()
    await _update_snapshot_current_version(ctx, article, session)
    await session.commit()

    result_wire = build_article_block_wire(
        pk=target.id,
        type_=target.type,
        content=target.content or {},
        evidence_anchors=target.evidence_anchors or [],
        origin=target.origin,
        resolver=EvidenceDisplayResolver(DocumentContent.model_validate(revision.content)),
    )
    await store.succeed(str(job.id), {"block": result_wire})


# ---------------------------------------------------------------------------
# ディスパッチャ
# ---------------------------------------------------------------------------
async def run_article_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='article'`` のディスパッチャ。``payload.op`` で処理を振り分ける。"""
    payload = job.payload or {}
    op = str(payload.get("op", "generate"))

    if op == "generate":
        await _run_generate(ctx, store, job)
    elif op == "regenerate":
        await _run_regenerate(ctx, store, job)
    elif op == "block_rewrite":
        await _run_block_rewrite(ctx, store, job)
    else:
        raise NotImplementedError(f"article op not supported: {op}")
