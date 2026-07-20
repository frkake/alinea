"""presentations ルータ — 論文→PPTX プレゼンテーション生成(Task 28)。

- ``POST /api/library-items/{item_id}/presentation``: ready revision を固定し、進行中 job が
  あれば再利用(二重生成防止)、無ければ ``jobs(kind='presentation')`` を ``alinea:bulk`` へ投入
  (実処理は worker Task 29)。使える API キーが 1 つも無ければ job を作る前に Problem を返す。
- ``GET  /api/library-items/{item_id}/presentation``: 最新成果物の metadata + 進行中 job。
- ``GET  /api/library-items/{item_id}/presentation/download``: 所有者確認後に PPTX を stream。

成果物は library_item ごとに最新版のみ(``presentation_artifacts.library_item_id`` UNIQUE)。
再生成は DB がコミットで新 storage key を指すまで旧 key(旧成功)を保つ(no-overwrite key)。
LLM 呼び出しは worker 側(本ルータは route/key の可用性判定のみ)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from alinea_core.db.models import Job, LibraryItem, Paper, PresentationArtifact
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.jobs.store import JobStore
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.deps import CurrentUser, DbDep, RedisDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.llm.key_store import DbKeyStore
from alinea_api.llm.route_store import DbRouteStore
from alinea_api.routers.papers import StorageDep
from alinea_api.routers.viewer import resolve_owned_library_item
from alinea_api.schemas.jobs import job_to_out
from alinea_api.schemas.presentations import (
    PRESET_DEFAULT_AUDIENCE,
    PresentationArtifactOut,
    PresentationGenerateRequest,
    PresentationJobResponse,
    PresentationStatusResponse,
)

router = APIRouter(tags=["presentations"])
log = structlog.get_logger("alinea.api.presentations")

# plans/01 §4.3(apps/worker/settings.BULK_QUEUE と同値。apps 間 import 禁止のため定数で持つ)。
_BULK_QUEUE = "alinea:bulk"
_PRESENTATION_TASK = "presentation"
_PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
# 生成中とみなす job status(active job の再利用判定に使う)。
_ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_quota", "waiting_input")


# ---------------------------------------------------------------------------
# 起床通知(テストで差し替え可能。articles/export と同方針)
# ---------------------------------------------------------------------------
JobWakeup = Callable[[str], Awaitable[None]]


async def _default_wakeup(redis_url: str, job_id: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        await pool.enqueue_job("run_job", job_id, _queue_name=_BULK_QUEUE)
    finally:
        await pool.aclose()


def get_presentation_job_wakeup(settings: SettingsDep) -> JobWakeup:
    """arq への起床通知を返す。失敗しても job 作成自体は成功させる(DB が真実)。"""

    async def wakeup(job_id: str) -> None:
        try:
            await _default_wakeup(settings.redis_url, job_id)
        except Exception:
            await log.awarning("presentation_wakeup_failed", job_id=job_id)

    return wakeup


PresentationJobWakeupDep = Annotated[JobWakeup, Depends(get_presentation_job_wakeup)]


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------
def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _artifact_for_item(
    db: AsyncSession, library_item_id: str
) -> PresentationArtifact | None:
    return (
        await db.execute(
            select(PresentationArtifact).where(
                PresentationArtifact.library_item_id == library_item_id
            )
        )
    ).scalar_one_or_none()


async def _active_job_for_item(db: AsyncSession, library_item_id: str) -> Job | None:
    """当該 library_item の進行中 presentation job(あれば最新を 1 件)。"""
    return (
        await db.execute(
            select(Job)
            .where(
                Job.library_item_id == library_item_id,
                Job.kind == _PRESENTATION_TASK,
                Job.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _ready_revision_id(db: AsyncSession, item: LibraryItem) -> str:
    """論文の取り込み済み最新リビジョンを固定する。無ければ 404(素材が無い)。"""
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")
    revision = await get_latest_paper_revision(db, paper)
    if revision is None:
        raise ProblemException("not_found", detail="取り込み済みの本文がありません")
    return str(revision.id)


async def _require_usable_provider(
    db: AsyncSession, r: RedisDep, settings: SettingsDep, user_id: str
) -> None:
    """presentation ルートに使える API キー(運営 or BYOK)が無ければ job 作成前に Problem。

    Task 13 の共有ルート解決(DbRouteStore)を再利用し、``resolve_chain`` を「運営キー +
    有効な BYOK があるプロバイダ」に絞る。結果が空 = 使えるキーが 1 つも無い。
    """
    key_store = DbKeyStore(db, settings)
    route_store = DbRouteStore(db, r, cache_ttl_s=settings.alinea_llm_route_cache_ttl_s)
    available = set(settings.operator_api_keys) | await key_store.active_providers(user_id)
    entries = await route_store.resolve_chain(
        _PRESENTATION_TASK, user_id, available_providers=available
    )
    if not entries:
        raise ProblemException(
            "provider_error",
            detail=(
                "プレゼンテーション生成に使える API キーがありません。"
                "設定画面で API キー(BYOK)を登録してください。"
            ),
        )


def _artifact_out(artifact: PresentationArtifact) -> PresentationArtifactOut:
    return PresentationArtifactOut(
        id=str(artifact.id),
        library_item_id=str(artifact.library_item_id),
        source_revision_id=str(artifact.source_revision_id),
        generation_job_id=(
            str(artifact.generation_job_id) if artifact.generation_job_id else None
        ),
        preset=artifact.preset,
        audience=artifact.audience,
        instruction=artifact.instruction,
        model_provider=artifact.model_provider,
        model_id=artifact.model_id,
        ppt_master_revision=artifact.ppt_master_revision,
        generated_at=artifact.generated_at.isoformat(),
        updated_at=artifact.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# POST — 生成/再生成
# ---------------------------------------------------------------------------
@router.post(
    "/api/library-items/{item_id}/presentation",
    response_model=PresentationJobResponse,
    status_code=202,
    operation_id="presentations_generate",
)
async def generate_presentation(
    item_id: str,
    body: PresentationGenerateRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    wakeup: PresentationJobWakeupDep,
) -> PresentationJobResponse:
    item = await resolve_owned_library_item(db, item_id, user)

    # 進行中 job があれば再利用する(二重生成防止)。ready revision 判定より前に返す。
    active = await _active_job_for_item(db, str(item.id))
    if active is not None:
        return PresentationJobResponse(job_id=str(active.id))

    # ready revision を固定(素材が無ければ 404)。
    source_revision_id = await _ready_revision_id(db, item)

    # 使える API キーが無ければ job を作る前に Problem を返す。
    await _require_usable_provider(db, r, settings, str(user.id))

    audience = body.audience or PRESET_DEFAULT_AUDIENCE[body.preset]
    payload: dict[str, object] = {
        "library_item_id": str(item.id),
        "source_revision_id": source_revision_id,
        "preset": body.preset,
        "audience": audience,
    }
    if body.instruction:
        payload["instruction"] = body.instruction

    store = JobStore(db)
    job_id = await store.enqueue(
        kind=_PRESENTATION_TASK,
        priority="bulk",
        user_id=str(user.id),
        paper_id=str(item.paper_id),
        library_item_id=str(item.id),
        payload=payload,
    )
    await wakeup(job_id)
    return PresentationJobResponse(job_id=job_id)


# ---------------------------------------------------------------------------
# GET — 最新 metadata + 進行中 job
# ---------------------------------------------------------------------------
@router.get(
    "/api/library-items/{item_id}/presentation",
    response_model=PresentationStatusResponse,
    operation_id="presentations_get",
)
async def get_presentation(
    item_id: str, user: CurrentUser, db: DbDep
) -> PresentationStatusResponse:
    item = await resolve_owned_library_item(db, item_id, user)
    artifact = await _artifact_for_item(db, str(item.id))
    active = await _active_job_for_item(db, str(item.id))
    return PresentationStatusResponse(
        artifact=_artifact_out(artifact) if artifact is not None else None,
        job=job_to_out(active) if active is not None else None,
    )


# ---------------------------------------------------------------------------
# download — 所有者確認後に PPTX を stream
# ---------------------------------------------------------------------------
@router.get(
    "/api/library-items/{item_id}/presentation/download",
    operation_id="presentations_download",
)
async def download_presentation(
    item_id: str, user: CurrentUser, db: DbDep, storage: StorageDep
) -> Response:
    item = await resolve_owned_library_item(db, item_id, user)
    artifact = await _artifact_for_item(db, str(item.id))
    if artifact is None or not artifact.pptx_storage_key:
        raise ProblemException("not_found")
    try:
        data = await storage.get(storage.assets_bucket, artifact.pptx_storage_key)
    except Exception as exc:  # S3 に実体が無い(生成途中・欠落)場合(P3)
        raise ProblemException("not_found") from exc
    return Response(
        content=data,
        media_type=_PPTX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="presentation.pptx"'},
    )


__all__ = ["router"]
