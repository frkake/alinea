"""PresentationRunner: grounded slide plan -> SVG -> PPTX -> atomic artifact.

Pipeline stages (each updates ``JobStore.checkpoint`` + best-effort SSE):

    preparing_source -> planning -> authoring_slides -> validating
        -> exporting -> uploading

Boundaries enforced here:

- **Grounding.** The planning LLM output is validated against the source packet:
  nonexistent evidence anchors, duplicate figure ids, ungrounded numbers, and
  out-of-range slide counts are rejected. Exactly one repair attempt is made;
  if it still fails the job fails at the ``planning`` stage.
- **Minimal per-slide context.** SVG authoring receives only that slide's
  claims + cited excerpts/captions, never the whole packet or other slides.
- **SVG safety.** Every generated SVG passes
  :func:`alinea_worker.figure_assets.sanitize_svg_document` before it reaches the
  pinned ppt-master quality checker (which itself runs with no LLM keys and no
  network).
- **Atomic replacement.** The new PPTX is uploaded to a job-specific key and
  independently validated (ZIP structure, slide count, <=100 MiB, SHA-256)
  before the artifact row is updated; only after the DB commits is the old key
  deleted. A failed new generation never disturbs the previously downloadable
  artifact.
- **No residue.** A job-specific temp dir is created and always removed on
  success, failure, or cancellation.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

import structlog
from alinea_core.db.models import Job, LibraryItem, Paper, PresentationArtifact
from alinea_core.db.revisions import get_latest_paper_revision, get_paper_revision
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_llm.router import LLMRouter
from alinea_llm.types import ContentPart, LLMRequest, LLMResponse, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_worker.figure_assets import FigureAssetError, sanitize_svg_document
from alinea_worker.presentation.ppt_master import (
    PPT_MASTER_REVISION,
    PptMasterError,
    validate_pptx_package,
)
from alinea_worker.presentation.prompts import (
    PLAN_SYSTEM_PROMPT,
    SVG_SYSTEM_PROMPT,
    build_plan_user_prompt,
    build_svg_user_prompt,
)
from alinea_worker.presentation.schemas import (
    PRESET_SLIDE_RANGE,
    SLIDE_PLAN_SCHEMA_SPEC,
    SLIDE_SVG_SCHEMA_SPEC,
    SlidePlan,
    SlidePlanDocument,
    SlideSvg,
)
from alinea_worker.presentation.source_packet import SourcePacket, build_source_packet
from alinea_worker.presentation.svg_ppt import flatten_svg_for_ppt

log = structlog.get_logger("alinea.worker.presentation")

STAGES = (
    "preparing_source",
    "planning",
    "authoring_slides",
    "validating",
    "exporting",
    "uploading",
)

MAX_PPTX_BYTES = 100 * 1024 * 1024
# Per-slide SVG authoring fans out with this bound. Each call is independent
# (only its own slide's material) but slow at effort=high — live decks measured
# 68-176 s/slide, so a full 16-18 slide research_talk awaited one-at-a-time
# overran the 1800 s BulkWorker job_timeout during authoring_slides. A modest
# bound (well under the provider/proxy connection limits) collapses the wall
# clock to ~ceil(slides / bound) waves while keeping request pressure sane.
SVG_AUTHOR_CONCURRENCY = 4
_PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
# Same numeric-token shape used by the overview-figure grounding check.
_NUMERIC_TOKEN = re.compile(r"[0-9][0-9.,×^%]*")  # noqa: RUF001


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class PresentationError(RuntimeError):
    """Base error carrying the failing stage for job diagnostics."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message


class SlidePlanValidationError(PresentationError):
    """The slide plan could not be grounded even after one repair attempt."""

    def __init__(self, message: str) -> None:
        super().__init__("planning", message)


class PptxArtifactError(PresentationError):
    """The produced/uploaded PPTX failed independent validation."""


# --------------------------------------------------------------------------- #
# Adapter seam (ppt-master conversion)
# --------------------------------------------------------------------------- #
class PptMasterConverter(Protocol):
    """The single method PresentationRunner needs from the ppt-master adapter."""

    def convert(
        self, *, svg_source_dir: Path, notes_path: Path | None, work_dir: Path
    ) -> Any: ...


# --------------------------------------------------------------------------- #
# Plan grounding
# --------------------------------------------------------------------------- #
def _grounding_text(packet: SourcePacket) -> str:
    parts = [packet.bibliography]
    parts += [s.title for s in packet.sections]
    parts += [b.text for b in packet.blocks]
    parts += [f.caption for f in packet.figures]
    parts += [f.number for f in packet.figures]
    return "\n".join(p for p in parts if p)


def _normalize_numeric(text: str) -> str:
    """Fold LaTeX escapes / glyph variants so grounded numbers match verbatim.

    The paper body keeps LaTeX verbatim, so a grounded figure like ``88.55\\%``,
    ``1\\,000`` or ``2 \\times 2`` would never substring-match a slide's plain
    ``88.55%`` / ``1,000`` / ``4×``. Dropping the backslash and mapping the ×
    glyph to ``x`` makes the *paper's own* numbers match while leaving fabricated
    numbers unmatched.
    """
    return text.replace("\\", "").replace("×", "x")  # noqa: RUF001


def _ungrounded_number(texts: list[str], grounding: str) -> str | None:
    normalized_grounding = _normalize_numeric(grounding)
    for text in texts:
        for token in _NUMERIC_TOKEN.findall(text):
            if _normalize_numeric(token) not in normalized_grounding:
                return str(token)
    return None


def validate_slide_plan(
    plan: SlidePlanDocument, packet: SourcePacket, *, preset: str
) -> list[str]:
    """Return a list of grounding/contract errors (empty means the plan is valid)."""

    errors: list[str] = []
    slides = plan.slides
    low, high = PRESET_SLIDE_RANGE.get(preset, (12, 18))
    if not (low <= len(slides) <= high):
        errors.append(
            f"スライド枚数 {len(slides)} が用途の範囲 {low}〜{high} 外です。"
        )

    valid_figures = set(packet.figure_ids)
    # A slide may legitimately cite a figure/table as the evidence for a claim
    # (e.g. "Table 1 reports 88.55%"), so a figure id is a *grounded* evidence
    # anchor -- not a nonexistent one. Accept both prose/section anchors and
    # figure ids here; only ids absent from the packet entirely are rejected.
    valid_anchors = set(packet.anchor_ids) | valid_figures
    grounding = _grounding_text(packet)

    seen_figures: set[str] = set()
    for slide in slides:
        for anchor in slide.evidence_anchors:
            if anchor not in valid_anchors:
                errors.append(
                    f"slide {slide.index}: 存在しない evidence anchor '{anchor}'。"
                )
        for figure_id in slide.figure_ids:
            if figure_id not in valid_figures:
                errors.append(f"slide {slide.index}: 存在しない figure '{figure_id}'。")
            elif figure_id in seen_figures:
                errors.append(f"slide {slide.index}: figure '{figure_id}' が重複使用されています。")
            else:
                seen_figures.add(figure_id)
        bad = _ungrounded_number([*slide.claims, slide.speaker_notes], grounding)
        if bad is not None:
            errors.append(
                f"slide {slide.index}: 数値 '{bad}' が論文素材に見つかりません(根拠なし)。"
            )
    return errors


async def _call_plan(
    router: LLMRouter,
    *,
    packet: SourcePacket,
    preset: str,
    audience: str,
    instruction: str | None,
    repair_error: str | None,
    job: Job,
) -> LLMResponse:
    user_text = build_plan_user_prompt(
        packet,
        preset=preset,
        audience=audience,
        instruction=instruction,
        repair_error=repair_error,
    )
    request = LLMRequest(
        model="",
        system=[ContentPart.from_text(PLAN_SYSTEM_PROMPT, cache_hint=True)],
        messages=[Message(role="user", parts=[ContentPart.from_text(user_text)])],
        # A full research_talk deck (up to 18 slides, each with claims +
        # evidence anchors + verbose grounded speaker notes) serializes well
        # beyond 8192 tokens; with high-effort reasoning also drawing from the
        # completion budget, 8192 truncated the plan JSON mid-string
        # (stop_reason=max_tokens) -> schema_validation -> chain exhausted on an
        # OpenAI-only route. Give the plan the same generous budget as SVG
        # authoring so the whole grounded plan always fits.
        max_output_tokens=32768,
        effort="high",
        timeout_s=180.0,
        metadata={"task": "presentation"},
    )
    return await router.complete(
        "presentation",
        schema=SLIDE_PLAN_SCHEMA_SPEC,
        mode="structured",
        request=request,
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
    )


async def plan_slides(
    router: LLMRouter,
    *,
    packet: SourcePacket,
    preset: str,
    audience: str,
    instruction: str | None,
    job: Job,
) -> tuple[SlidePlanDocument, LLMResponse]:
    """Generate a grounded slide plan with exactly one repair attempt."""

    repair_error: str | None = None
    for attempt in range(2):  # initial + one repair
        resp = await _call_plan(
            router,
            packet=packet,
            preset=preset,
            audience=audience,
            instruction=instruction,
            repair_error=repair_error,
            job=job,
        )
        try:
            plan = SlidePlanDocument.model_validate(resp.parsed or {})
        except Exception as exc:  # pydantic.ValidationError etc.
            repair_error = f"スキーマ検証エラー: {exc}"
            continue
        errors = validate_slide_plan(plan, packet, preset=preset)
        if not errors:
            return plan, resp
        repair_error = "; ".join(errors[:8])
        log.info("presentation.plan_invalid", attempt=attempt, error_count=len(errors))
    raise SlidePlanValidationError(
        f"スライド構成の根拠検証に失敗しました: {repair_error}"
    )


# --------------------------------------------------------------------------- #
# Per-slide SVG authoring
# --------------------------------------------------------------------------- #
def _slide_excerpts(slide: SlidePlan, packet: SourcePacket) -> tuple[list[str], list[str]]:
    """Return (evidence excerpts, figure captions) cited by *this* slide only."""

    block_by_anchor = {b.anchor: b.text for b in packet.blocks}
    section_by_anchor = {s.anchor: s.title for s in packet.sections}
    figure_by_id = {f.figure_id: f for f in packet.figures}

    excerpts: list[str] = []
    for anchor in slide.evidence_anchors:
        if anchor in block_by_anchor:
            excerpts.append(block_by_anchor[anchor])
        elif anchor in section_by_anchor:
            excerpts.append(section_by_anchor[anchor])
    captions: list[str] = []
    for figure_id in slide.figure_ids:
        figure = figure_by_id.get(figure_id)
        if figure is not None:
            label = "図" if figure.kind == "figure" else "表"
            captions.append(f"{label}{figure.number}: {figure.caption}")
    return excerpts, captions


async def _call_svg(
    router: LLMRouter, *, user_text: str, job: Job
) -> LLMResponse:
    request = LLMRequest(
        model="",
        system=[ContentPart.from_text(SVG_SYSTEM_PROMPT, cache_hint=True)],
        messages=[Message(role="user", parts=[ContentPart.from_text(user_text)])],
        max_output_tokens=16384,
        effort="high",
        timeout_s=180.0,
        metadata={"task": "presentation"},
    )
    return await router.complete(
        "presentation",
        schema=SLIDE_SVG_SCHEMA_SPEC,
        mode="structured",
        request=request,
        user_id=str(job.user_id) if job.user_id else None,
        library_item_id=str(job.library_item_id) if job.library_item_id else None,
        job_id=str(job.id),
    )


async def author_slide_svgs(
    router: LLMRouter,
    *,
    plan: SlidePlanDocument,
    packet: SourcePacket,
    instruction: str | None,
    job: Job,
) -> list[SlideSvg]:
    """Generate one sanitized SVG per slide from that slide's material only.

    Slides are independent (each SVG call receives only its own slide's claims +
    cited excerpts/captions), so they are authored with bounded concurrency
    (:data:`SVG_AUTHOR_CONCURRENCY`) rather than strictly one-at-a-time; a full
    research_talk deck authored sequentially overran the BulkWorker
    ``job_timeout``. Results are reassembled in plan order so filenames stay
    deterministic regardless of completion order.
    """

    slides = sorted(plan.slides, key=lambda s: s.index)
    semaphore = asyncio.Semaphore(SVG_AUTHOR_CONCURRENCY)

    async def _author(position: int, slide: SlidePlan) -> SlideSvg:
        excerpts, captions = _slide_excerpts(slide, packet)
        user_text = build_svg_user_prompt(
            title=slide.title,
            claims=slide.claims,
            speaker_notes=slide.speaker_notes,
            excerpts=excerpts,
            figure_captions=captions,
            layout=slide.layout,
            instruction=instruction,
        )
        async with semaphore:
            resp = await _call_svg(router, user_text=user_text, job=job)
        raw_svg = str((resp.parsed or {}).get("svg", ""))
        # SVG safety gate BEFORE any downstream (ppt-master) processing.
        try:
            sanitized = sanitize_svg_document(raw_svg.encode("utf-8"))
        except FigureAssetError as exc:
            raise PresentationError(
                "authoring_slides",
                f"slide {slide.index}: 生成された SVG が安全検査で拒否されました ({exc.code})。",
            ) from exc
        # The safety sanitizer keeps <style>/class/<g opacity> (they are safe),
        # but ppt-master's quality gate hard-errors on all three and its
        # converter ignores CSS classes entirely. Flatten to inline presentation
        # attributes so the deck both passes the gate and keeps its styling.
        flattened = flatten_svg_for_ppt(sanitized)
        return SlideSvg(
            index=slide.index,
            filename=f"{position:02d}.svg",
            svg=flattened.decode("utf-8"),
        )

    # gather preserves input order, so the returned list stays in plan order.
    return list(
        await asyncio.gather(
            *(_author(position, slide) for position, slide in enumerate(slides, start=1))
        )
    )


def build_notes_markdown(plan: SlidePlanDocument) -> str:
    """Assemble the grounded speaker notes as ppt-master ``notes/total.md``."""

    slides = sorted(plan.slides, key=lambda s: s.index)
    chunks: list[str] = []
    for position, slide in enumerate(slides, start=1):
        heading = f"# {position:02d}"
        body = slide.speaker_notes.strip() or slide.title.strip()
        chunks.append(f"{heading}\n\n{body}")
    return "\n\n".join(chunks) + "\n"


# --------------------------------------------------------------------------- #
# PPTX independent validation
# --------------------------------------------------------------------------- #
def validate_pptx_bytes(
    data: bytes, *, expected_slides: int, work_dir: Path
) -> tuple[int, str]:
    """Validate uploaded-candidate PPTX bytes; return (slide_count, sha256 hex)."""

    if not data:
        raise PptxArtifactError("uploading", "PPTX は 0 バイトです。")
    if len(data) > MAX_PPTX_BYTES:
        raise PptxArtifactError(
            "uploading", f"PPTX が上限 {MAX_PPTX_BYTES} バイトを超えています。"
        )
    sha256 = hashlib.sha256(data).hexdigest()
    probe = work_dir / f"validate-{sha256[:12]}.pptx"
    probe.write_bytes(data)
    try:
        slide_count = validate_pptx_package(probe)
    except PptMasterError as exc:
        raise PptxArtifactError("uploading", f"PPTX 構造検証に失敗しました: {exc}") from exc
    finally:
        probe.unlink(missing_ok=True)
    if expected_slides and slide_count != expected_slides:
        raise PptxArtifactError(
            "uploading",
            f"PPTX のスライド数 {slide_count} が計画 {expected_slides} と一致しません。",
        )
    return slide_count, sha256


# --------------------------------------------------------------------------- #
# Atomic artifact replacement
# --------------------------------------------------------------------------- #
async def _existing_artifact(
    session: AsyncSession, library_item_id: str
) -> PresentationArtifact | None:
    return (
        await session.execute(
            select(PresentationArtifact).where(
                PresentationArtifact.library_item_id == library_item_id
            )
        )
    ).scalar_one_or_none()


async def replace_presentation_artifact(
    session: AsyncSession,
    storage: Any,
    *,
    job: Job,
    library_item_id: str,
    source_revision_id: str,
    preset: str,
    audience: str,
    instruction: str,
    model_provider: str,
    model_id: str,
    pptx_bytes: bytes,
    expected_slides: int,
    work_dir: Path,
) -> PresentationArtifact:
    """Atomically publish a new PPTX, preserving the old one on any failure.

    Order (each step's failure keeps the previously downloadable artifact intact):
      1. validate bytes (ZIP / slide count / size / sha-256),
      2. upload to the job-specific key (no overwrite of the old key),
      3. update/insert the artifact row and commit,
      4. delete the old key -- if only this fails, the new artifact is still a
         success and the stale key is queued for cleanup retry.
    """

    validate_pptx_bytes(pptx_bytes, expected_slides=expected_slides, work_dir=work_dir)

    new_key = StorageKeys.presentation_pptx(library_item_id, str(job.id))
    existing = await _existing_artifact(session, library_item_id)
    old_key = existing.pptx_storage_key if existing is not None else None

    # (2) Upload the new object first. If this fails the DB is untouched, so the
    # old artifact (if any) stays fully downloadable.
    await storage.put(
        storage.assets_bucket, new_key, pptx_bytes, content_type=_PPTX_CONTENT_TYPE
    )

    # (3) Point the DB at the new key and commit. A commit failure rolls back and
    # leaves the old row (and its key) authoritative; the new object is orphaned
    # but harmless (job-specific, unreferenced).
    if existing is None:
        artifact = PresentationArtifact(
            library_item_id=library_item_id,
            source_revision_id=source_revision_id,
            generation_job_id=str(job.id),
            preset=preset,
            audience=audience,
            instruction=instruction,
            model_provider=model_provider,
            model_id=model_id,
            ppt_master_revision=PPT_MASTER_REVISION,
            pptx_storage_key=new_key,
        )
        session.add(artifact)
    else:
        existing.source_revision_id = source_revision_id
        existing.generation_job_id = str(job.id)
        existing.preset = preset
        existing.audience = audience
        existing.instruction = instruction
        existing.model_provider = model_provider
        existing.model_id = model_id
        existing.ppt_master_revision = PPT_MASTER_REVISION
        existing.pptx_storage_key = new_key
        artifact = existing
    await session.commit()

    # (4) The DB now points at the new key: the replacement has succeeded. Delete
    # the stale key best-effort; a delete failure is recorded for cleanup retry
    # and must NOT fail the job (the new artifact is already downloadable).
    if old_key and old_key != new_key:
        try:
            await storage.delete_many(storage.assets_bucket, [old_key])
        except Exception as exc:  # best effort; see docstring
            store = JobStore(session)
            await store.record_partial_failure(
                str(job.id),
                "uploading",
                {"code": "stale_pptx_delete_failed", "key": old_key, "error": str(exc)},
            )
            log.warning(
                "presentation.stale_key_delete_failed",
                job_id=str(job.id),
                key=old_key,
                error=str(exc),
            )
    return artifact


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
class PresentationRunner:
    """Drive the paper->PPTX pipeline for one ``presentation`` job."""

    def __init__(
        self,
        ctx: dict[str, Any],
        store: JobStore,
        job: Job,
        *,
        adapter: PptMasterConverter | None = None,
        storage: Any | None = None,
    ) -> None:
        self.ctx = ctx
        self.store = store
        self.session = store.session
        self.job = job
        self._adapter = adapter or ctx.get("ppt_master_adapter")
        self._storage = storage or ctx.get("s3") or S3Storage()

    async def _checkpoint(
        self, stage: str, progress: int, data: dict[str, Any] | None = None
    ) -> None:
        await self.store.checkpoint(str(self.job.id), stage, data, progress=progress)
        await self._publish(stage)

    async def _publish(self, stage: str) -> None:
        publish = self.ctx.get("publish")
        if publish is None or self.job.user_id is None:
            return
        try:
            await publish(
                {
                    "type": "job.updated",
                    "job_id": str(self.job.id),
                    "user_id": str(self.job.user_id),
                    "library_item_id": (
                        str(self.job.library_item_id) if self.job.library_item_id else None
                    ),
                    "stage": stage,
                }
            )
        except Exception as exc:  # SSE wakeup is best effort
            log.warning("presentation.publish_failed", job_id=str(self.job.id), error=str(exc))

    async def _load_context(self) -> tuple[Paper, Any, LibraryItem]:
        payload = self.job.payload or {}
        library_item_id = str(payload["library_item_id"])
        item = await self.session.get(LibraryItem, library_item_id)
        if item is None:
            raise PresentationError("preparing_source", "library item not found")
        paper = await self.session.get(Paper, item.paper_id)
        if paper is None:
            raise PresentationError("preparing_source", "paper not found")
        source_revision_id = payload.get("source_revision_id")
        revision = None
        if source_revision_id:
            revision = await get_paper_revision(
                self.session, paper_id=paper.id, revision_id=source_revision_id
            )
        if revision is None:
            revision = await get_latest_paper_revision(self.session, paper)
        if revision is None:
            raise PresentationError("preparing_source", "取り込み済みの本文がありません")
        return paper, revision, item

    def _fetched_figure_keys(self, revision: Any) -> set[str]:
        from alinea_core.document.blocks import DocumentContent

        content = DocumentContent.model_validate(revision.content)
        return {
            block.asset_key
            for _section, block in content.iter_blocks()
            if block.type in ("figure", "table") and block.asset_key
        }

    async def run(self) -> PresentationArtifact:
        payload = self.job.payload or {}
        preset = str(payload.get("preset", "research_talk"))
        audience = str(payload.get("audience", "researcher"))
        instruction = payload.get("instruction")
        instruction = str(instruction)[:500] if instruction else None

        tmp_root = Path(tempfile.mkdtemp(prefix=f"presentation-{self.job.id}-"))
        try:
            # -- preparing_source ------------------------------------------- #
            await self._checkpoint("preparing_source", 5)
            paper, revision, _item = await self._load_context()
            packet = build_source_packet(
                paper=paper,
                revision=revision,
                fetched_figure_keys=self._fetched_figure_keys(revision),
            )

            router: LLMRouter = await self.ctx["user_router_factory"].for_job(
                user_id=str(self.job.user_id), task="presentation"
            )

            # -- planning --------------------------------------------------- #
            await self._checkpoint("planning", 20)
            plan, plan_resp = await plan_slides(
                router,
                packet=packet,
                preset=preset,
                audience=audience,
                instruction=instruction,
                job=self.job,
            )
            model_provider = plan_resp.provider
            model_id = plan_resp.model

            # -- authoring_slides ------------------------------------------- #
            await self._checkpoint("authoring_slides", 45)
            svgs = await author_slide_svgs(
                router, plan=plan, packet=packet, instruction=instruction, job=self.job
            )

            # -- validating (SVG contents already sanitized) ---------------- #
            await self._checkpoint("validating", 60)
            svg_dir = tmp_root / "svg_input"
            svg_dir.mkdir(parents=True, exist_ok=True)
            for slide in svgs:
                (svg_dir / slide.filename).write_text(slide.svg, encoding="utf-8")
            notes_dir = tmp_root / "notes_input"
            notes_dir.mkdir(parents=True, exist_ok=True)
            notes_path = notes_dir / "total.md"
            notes_path.write_text(build_notes_markdown(plan), encoding="utf-8")

            # -- exporting (pinned ppt-master; no LLM key, no network) ------ #
            await self._checkpoint("exporting", 75)
            adapter = self._adapter
            if adapter is None:
                raise PresentationError("exporting", "ppt-master adapter is not configured")
            work_dir = tmp_root / "work"
            conversion = adapter.convert(
                svg_source_dir=svg_dir, notes_path=notes_path, work_dir=work_dir
            )
            pptx_bytes = Path(conversion.pptx_path).read_bytes()

            # -- uploading (atomic replacement) ----------------------------- #
            await self._checkpoint("uploading", 90)
            artifact = await replace_presentation_artifact(
                self.session,
                self._storage,
                job=self.job,
                library_item_id=str(payload["library_item_id"]),
                source_revision_id=str(revision.id),
                preset=preset,
                audience=audience,
                instruction=instruction or "",
                model_provider=model_provider,
                model_id=model_id,
                pptx_bytes=pptx_bytes,
                expected_slides=len(svgs),
                work_dir=tmp_root,
            )
            await self.store.succeed(
                str(self.job.id),
                {
                    "presentation_artifact_id": str(artifact.id),
                    "slide_count": len(svgs),
                    "ppt_master_revision": PPT_MASTER_REVISION,
                },
            )
            return artifact
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)


__all__ = [
    "MAX_PPTX_BYTES",
    "STAGES",
    "PptMasterConverter",
    "PptxArtifactError",
    "PresentationError",
    "PresentationRunner",
    "SlidePlanValidationError",
    "author_slide_svgs",
    "build_notes_markdown",
    "plan_slides",
    "replace_presentation_artifact",
    "validate_pptx_bytes",
    "validate_slide_plan",
]
