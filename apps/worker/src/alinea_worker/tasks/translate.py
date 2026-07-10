"""翻訳ジョブ(plans/06 §3.1・§11・§13)。

``jobs.kind = 'translation'`` の全用途は ``payload.reason`` で判別する(plans/06 §3.1)。

- ``initial`` / ``literal`` / ``on_demand`` / ``table``: セクション単位のバッチ翻訳
  (:func:`alinea_core.translation.pipeline.translate_section` に委譲。M0-17)。
- ``retranslate`` / ``instructed``: 単一ユニットの再翻訳(plans/06 §11.1)。結果は
  ``translation_units.proposal`` に保存する(直接上書きしない)。``instruction`` があれば
  指示つき、無ければ通常の再翻訳。``placeholder_mismatch`` の案は保存せずジョブを failed に
  する(壊れた訳を見せない。P3)。タスクルートは ``retranslation_escalation``
  (plans/04 §8。worker のルータ構築は 1 本のみのため実際のチェーン切替は followup — 下記
  :mod:`alinea_worker.bootstrap` の制約に同じ)。
- ``glossary_change``: 訳語変更の影響ブロックを一括再翻訳し、対象 ``TranslationSet``(訳語変更
  適用時に確定済みの personal セット)へ直接 UPSERT する(plans/06 §8.4)。通常の
  ``translation`` タスクルートを使う(エスカレーションしない)。

進捗は :class:`~alinea_core.jobs.store.JobStore` 経由で ``jobs`` テーブルに反映し(SSE は
jobs を読む)、``ctx['publish']`` があれば ``translation.unit_completed`` 相当を発行する。
LLMRouter は ``ctx['router']`` から注入する(apps 間 import を避けるための DI)。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from alinea_core.db.models import DocumentRevision, Job, Paper, TranslationSet, TranslationUnit
from alinea_core.document.blocks import Block, DocumentContent
from alinea_core.ingest import joblog
from alinea_core.ingest.progress import finalize_ingest_if_body_complete
from alinea_core.jobs.store import JobStore
from alinea_core.settings import get_settings
from alinea_core.storage.s3 import S3Storage
from alinea_core.translation.glossary import format_glossary_lines, glossary_hash
from alinea_core.translation.pipeline import (
    TranslatedUnit,
    TranslationContext,
    translate_block,
    translate_section,
)
from alinea_core.translation.prompts import (
    build_paper_context,
    build_system_preamble,
    field_profile,
)
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_worker import notify
from alinea_worker.latex_pdf import LatexPdfBuildError, build_latex_translation_pdfs_if_ready

# translate_section が担当する reason(plans/06 §3.1)。
_SECTION_REASONS = frozenset({"initial", "literal", "on_demand", "table", "retry_failed"})
# 単一ユニット再翻訳(proposal 保存。plans/06 §11.1)。
_SINGLE_UNIT_REASONS = frozenset({"retranslate", "instructed"})
# 再翻訳のエスカレーション先タスク名(plans/04 §8)。
_RETRANSLATE_TASK = "retranslation_escalation"


class RetranslateBlockedError(Exception):
    """再翻訳案がプレースホルダ検証に失敗(plans/06 §11.1)。

    proposal を保存せずジョブを失敗させる。
    """


def _find_block(content: DocumentContent, block_id: str) -> Block | None:
    for _sec, blk in content.iter_blocks():
        if blk.id == block_id:
            return blk
    return None


def _authors_short(authors: list[Any] | None) -> str:
    names = [str(a.get("name", a)) if isinstance(a, dict) else str(a) for a in (authors or [])[:3]]
    suffix = " ほか" if len(authors or []) > 3 else ""
    return "、".join(names) + suffix if names else "(不明)"


def _toc_outline(content: DocumentContent) -> str:
    lines: list[str] = []
    for top in content.sections:
        lines.append(f"- {top.heading.number} {top.heading.title}".rstrip())
        for sub in top.sections:
            lines.append(f"  - {sub.heading.number} {sub.heading.title}".rstrip())
    return "\n".join(lines)


async def _load_revision_and_content(
    session: AsyncSession, tset: TranslationSet
) -> tuple[DocumentRevision, DocumentContent]:
    revision = await session.get(DocumentRevision, str(tset.revision_id))
    if revision is None:
        raise LookupError(f"document revision not found: {tset.revision_id}")
    return revision, DocumentContent.model_validate(revision.content)


async def _build_context(
    session: AsyncSession,
    tset: TranslationSet,
    revision: DocumentRevision,
    content: DocumentContent,
    *,
    reason: str,
    instruction: str,
    task: str,
) -> TranslationContext:
    """§5-6 のプロンプト文脈を構築する(パイプライン §5.3/§5.4 相当の簡略版)。

    セクション単位の前後ブロック文脈(§6)は単一ユニット/一括再翻訳では対象外
    (指示・用語表・論文スコープ文脈のみで十分。docs/03 §9)。
    """
    paper = await session.get(Paper, revision.paper_id)
    snapshot = list(tset.glossary_snapshot or [])
    paper_context = build_paper_context(
        title=paper.title if paper else "",
        authors_short=_authors_short(paper.authors if paper else []),
        profile_text=field_profile(paper.arxiv_categories if paper else []),
        toc_outline=_toc_outline(content),
        glossary_lines=format_glossary_lines(snapshot),
    )
    return TranslationContext(
        style=tset.style,
        snapshot=snapshot,
        revision_id=str(tset.revision_id),
        glossary_hash=glossary_hash(snapshot),
        system_preamble=build_system_preamble(tset.style),
        paper_context=paper_context,
        reason=reason,
        instruction=instruction,
        task=task,
    )


# ---------------------------------------------------------------------------
# retranslate / instructed(plans/06 §11.1)
# ---------------------------------------------------------------------------


async def _run_single_unit_reason(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    session = store.session
    payload = job.payload or {}
    unit = await session.get(TranslationUnit, int(payload["unit_id"]))
    if unit is None:
        raise LookupError(f"translation unit not found: {payload['unit_id']}")
    tset = await session.get(TranslationSet, str(unit.set_id))
    if tset is None:
        raise LookupError(f"translation set not found: {unit.set_id}")
    revision, content = await _load_revision_and_content(session, tset)
    block = _find_block(content, unit.block_id)
    if block is None:
        raise LookupError(f"block not found: {unit.block_id}")

    reason = str(payload.get("reason", "retranslate"))
    instruction = str(payload.get("instruction", ""))
    tr_ctx = await _build_context(
        session,
        tset,
        revision,
        content,
        reason=reason,
        instruction=instruction,
        task=_RETRANSLATE_TASK,
    )
    router = ctx["router"]
    result = await translate_block(
        block,
        router,
        block_type=block.type,
        ctx=tr_ctx,
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
    )
    if "placeholder_mismatch" in result.quality_flags:
        raise RetranslateBlockedError(
            f"再翻訳案がプレースホルダ検証に失敗しました(unit_id={unit.id})"
        )

    unit.proposal = {
        "text_ja": result.text_ja,
        "content_ja": result.content_ja,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "model": result.model,
    }
    await session.commit()
    await store.succeed(str(job.id), {"unit_id": str(unit.id), "proposal": True})


# ---------------------------------------------------------------------------
# glossary_change(plans/06 §8.4)
# ---------------------------------------------------------------------------


async def _upsert_glossary_unit(
    session: AsyncSession, set_id: str, block_id: str, translated: TranslatedUnit
) -> None:
    """訳語変更ジョブの結果を直接 UPSERT する(§8.4。proposal を経由せず即時反映)。"""
    stmt = pg_insert(TranslationUnit).values(
        set_id=set_id,
        block_id=block_id,
        source_hash=translated.source_hash,
        content_ja=translated.content_ja,
        text_ja=translated.text_ja,
        state=translated.db_state(),
        quality_flags=translated.quality_flags,
        model=translated.model,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_translation_units_set_block",
        set_={
            "source_hash": stmt.excluded.source_hash,
            "content_ja": stmt.excluded.content_ja,
            "text_ja": stmt.excluded.text_ja,
            "state": stmt.excluded.state,
            "quality_flags": stmt.excluded.quality_flags,
            "model": stmt.excluded.model,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def _run_glossary_change(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    session = store.session
    payload = job.payload or {}
    set_id = str(payload["set_id"])
    tset = await session.get(TranslationSet, set_id)
    if tset is None:
        raise LookupError(f"translation set not found: {set_id}")
    revision, content = await _load_revision_and_content(session, tset)
    block_ids = [str(b) for b in (payload.get("block_ids") or [])]

    tr_ctx = await _build_context(
        session,
        tset,
        revision,
        content,
        reason="glossary_change",
        instruction="",
        task="translation",
    )
    router = ctx["router"]
    translated_ids: list[str] = []
    for block_id in block_ids:
        block = _find_block(content, block_id)
        if block is None:  # 参照整合性が崩れている場合のみ(通常到達しない)。
            continue
        result = await translate_block(
            block,
            router,
            block_type=block.type,
            ctx=tr_ctx,
            user_id=str(job.user_id) if job.user_id else None,
            library_item_id=str(job.library_item_id) if job.library_item_id else None,
            job_id=str(job.id),
        )
        if "placeholder_mismatch" in result.quality_flags:
            continue  # 壊れた訳は反映しない(このブロックのみスキップ。他は続行。P3)
        await _upsert_glossary_unit(session, set_id, block_id, result)
        translated_ids.append(block_id)

    await session.commit()
    await store.succeed(
        str(job.id), {"translated": len(translated_ids), "block_ids": translated_ids}
    )


# ---------------------------------------------------------------------------
# ディスパッチャ
# ---------------------------------------------------------------------------


async def run_translation_job(ctx: dict[str, Any], store: JobStore, job: Job) -> None:
    """``kind='translation'`` のディスパッチャ。reason で処理を振り分ける。"""
    payload = job.payload or {}
    reason = str(payload.get("reason", "initial"))

    if reason in _SINGLE_UNIT_REASONS:
        try:
            await _run_single_unit_reason(ctx, store, job)
        except RetranslateBlockedError as exc:
            job.status = "failed"
            job.error = json.dumps(
                {"stage": job.stage, "code": "placeholder_mismatch", "message": str(exc)},
                ensure_ascii=False,
            )
            job.finished_at = dt.datetime.now(dt.UTC)
            await store.session.commit()
        return

    if reason == "glossary_change":
        await _run_glossary_change(ctx, store, job)
        return

    if reason not in _SECTION_REASONS:
        raise NotImplementedError(f"translation reason not supported: {reason}")

    router = ctx["router"]
    publish = ctx.get("publish")
    result = await translate_section(
        store.session,
        str(payload["set_id"]),
        str(payload["section_id"]),
        router,
        block_ids=payload.get("block_ids"),
        reason=reason,
        instruction=str(payload.get("instruction", "")),
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
        job_store=store,
        publish=publish,
    )
    await store.succeed(
        str(job.id),
        {
            "section_id": result.section_id,
            "translated": result.translated,
            "fallback": result.fallback,
            "skipped": result.skipped,
            "set_status": result.set_status,
            "progress_pct": result.progress_pct,
        },
    )

    # Retry/on-demand jobs can turn the final blocked unit into a usable translation
    # after the initial ingest has already finished. Rebuild only when quality progress
    # reaches 100; the PDF builder's translation digest makes duplicate calls cheap.
    if reason != "initial" and result.progress_pct >= 100:
        await _build_latex_translation_pdf_after_complete(ctx, store, None, str(payload["set_id"]))

    # arq 経路の完了確定(plans/05 §11.3): 初回全文翻訳の最後のジョブが親 ingest
    # ジョブと翻訳セットを complete にする(advisory lock で競合安全)。
    ingest_job_id = payload.get("ingest_job_id")
    if reason == "initial" and ingest_job_id:
        set_id = str(payload["set_id"])
        tset = await store.session.get(TranslationSet, set_id)
        if tset is not None:
            revision = await store.session.get(DocumentRevision, tset.revision_id)
            if revision is not None:
                completed = await finalize_ingest_if_body_complete(
                    store.session,
                    set_id=set_id,
                    ingest_job_id=str(ingest_job_id),
                    content=DocumentContent.model_validate(revision.content),
                    style=tset.style,
                    source_version=str(payload.get("source_version") or revision.source_version),
                    appendix_untranslated=bool(payload.get("appendix_untranslated", False)),
                )
                if completed:
                    await _build_latex_translation_pdf_after_complete(
                        ctx, store, str(ingest_job_id), set_id
                    )
                if completed and job.user_id and job.library_item_id:
                    # 取り込み完了通知(plans/05 §12.1)。job_id=親 ingest ジョブで 1 回限り。
                    paper = await store.session.get(Paper, revision.paper_id)
                    await notify.fire_translation_complete(
                        store.session,
                        ctx.get("redis"),
                        user_id=str(job.user_id),
                        library_item_id=str(job.library_item_id),
                        paper_title=paper.title if paper else "",
                        job_id=str(ingest_job_id),
                    )


async def _build_latex_translation_pdf_after_complete(
    ctx: dict[str, Any],
    store: JobStore,
    ingest_job_id: str | None,
    set_id: str,
) -> None:
    settings = ctx.get("settings") or get_settings()
    storage = ctx.get("s3") or S3Storage(settings)
    ingest_job = await store.session.get(Job, ingest_job_id) if ingest_job_id else None
    try:
        outcome = await build_latex_translation_pdfs_if_ready(
            store.session,
            storage,
            settings,
            set_id=set_id,
        )
    except LatexPdfBuildError as exc:
        if ingest_job is not None:
            await joblog.log(
                store.session,
                ingest_job,
                "translating_body",
                "warn",
                "日本語PDFのビルドに失敗(原文/訳文ビューは利用可能)",
                detail={"code": exc.kind, **exc.detail},
            )
        return
    if ingest_job is None:
        return
    if not outcome.built:
        if outcome.skipped_reason not in {"not_latex", "not_shared", "already_built"}:
            await joblog.log(
                store.session,
                ingest_job,
                "translating_body",
                "warn",
                "日本語PDFのビルドをスキップ",
                detail={"reason": outcome.skipped_reason},
            )
        return
    for warning in outcome.warnings:
        await joblog.log(store.session, ingest_job, "translating_body", "warn", warning)
    await joblog.log(
        store.session,
        ingest_job,
        "translating_body",
        "info",
        "日本語PDFをビルドしました",
        detail={
            "translated_pdf": outcome.translated_key,
        },
        timeline=True,
    )
