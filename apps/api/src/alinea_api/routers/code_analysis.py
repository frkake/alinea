"""code_analysis — GitHub コード対応解析の見積り・開始・結果 API(Task 21・設計 §10)。

エンドポイント:
- ``POST /api/library-items/{item_id}/code-analysis/estimate``: repo メタ + recursive tree から
  対象規模を求め、保守的に token/費用を見積もって 10 分有効の estimate を保存する。
- ``POST /api/library-items/{item_id}/code-analysis``: estimate の所有者・失効・commit・設定・
  残予算を再検証して job を enqueue する(予算不足なら waiting_budget、外部 API を呼ばない)。
- ``GET /api/library-items/{item_id}/code-analysis``: runs + 現在の結果 + 対応一覧 + stale。

権限: 他ユーザーの Resource、suggested/dismissed Resource、非 GitHub Resource、private/404 repo を
拒否する。同一 (user, revision, resource, commit, analysis_version) の成功結果は再利用し、
queued/running job を重複作成しない(冪等)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Annotated, Any

import httpx
from alinea_core.code_analysis import (
    ANALYSIS_VERSION,
    MAX_CLAIMS,
    ModelPricing,
    estimate_tokens_and_cost,
    extract_claims,
    idempotency_key,
)
from alinea_core.code_analysis.budget import budget_remaining_usd
from alinea_core.code_analysis.github import GitHubError, RepoMetadata, resolve_repo_metadata
from alinea_core.db.models import (
    CodeAnalysisEstimate,
    CodeAnalysisRun,
    CodeCorrespondence,
    DocumentRevision,
    LibraryItem,
    Notification,
    Paper,
)
from alinea_core.db.models import ResourceLink as ResourceLinkModel
from alinea_core.document.blocks import DocumentContent
from alinea_core.jobs.store import JobStore
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.deps import CurrentUser, DbDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.schemas.code_analysis import (
    CodeAnalysisEstimateResponse,
    CorrespondenceOut,
    EstimateRequest,
    RunOut,
    RunsResponse,
    StartRequest,
    StartResponse,
)
from alinea_api.schemas.settings import DEFAULTS, FullSettings, deep_merge

router = APIRouter(tags=["code_analysis"])

_GITHUB_PATH_RE = __import__("re").compile(r"^/([^/]+)/([^/]+)")
_BULK_QUEUE = "alinea:bulk"

# 見積りに使うモデルの pricing 既定(claude-sonnet-5)。実行時は routing/overrides が正だが、
# 見積りは保守側に倒すため既定モデルの pricing を使う(設計 §7)。
_DEFAULT_PRICING = ModelPricing(input_per_mtok=3.00, output_per_mtok=15.00, embedding_per_mtok=0.02)


# --------------------------------------------------------------------------- #
# GitHub メタ解決の依存(テストは override してネットワークを使わない)
# --------------------------------------------------------------------------- #
RepoResolver = Callable[[str, str], Coroutine[Any, Any, RepoMetadata]]


async def _default_repo_resolver(owner: str, repo: str) -> RepoMetadata:
    async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30.0, connect=10.0)) as c:
        return await resolve_repo_metadata(c, owner, repo)


def get_repo_resolver() -> RepoResolver:
    return _default_repo_resolver


RepoResolverDep = Annotated[RepoResolver, Depends(get_repo_resolver)]


# --------------------------------------------------------------------------- #
# job wakeup(テストは override して arq を呼ばない)
# --------------------------------------------------------------------------- #
JobWakeup = Callable[[str], Coroutine[Any, Any, None]]


async def _default_wakeup(redis_url: str, job_id: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        await pool.enqueue_job("run_job", job_id, _queue_name=_BULK_QUEUE)
    finally:
        await pool.aclose()


def get_job_wakeup(settings: SettingsDep) -> JobWakeup:
    async def _wakeup(job_id: str) -> None:
        try:
            await _default_wakeup(settings.redis_url, job_id)
        except Exception:
            # 起床失敗はジョブ本体の失敗ではない(worker の poll が拾う)。握りつぶす。
            return None

    return _wakeup


JobWakeupDep = Annotated[JobWakeup, Depends(get_job_wakeup)]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _owned_item(db: AsyncSession, user_id: str, item_id: str) -> LibraryItem:
    if not _valid_uuid(item_id):
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return item


def _parse_github(url: str) -> tuple[str, str] | None:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "github.com":
        return None
    m = _GITHUB_PATH_RE.match(parts.path)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return owner, repo


async def _validated_github_resource(
    db: AsyncSession, item: LibraryItem, resource_id: str
) -> tuple[ResourceLinkModel, tuple[str, str]]:
    """resource_id が当該 item の active GitHub Resource であることを検証し (link, (owner,repo))。

    - 他 item の Resource → not_found。
    - suggested / dismissed Resource → not_found(active のみ対象。設計 §6)。
    - 非 GitHub Resource → validation_error。
    """
    if not _valid_uuid(resource_id):
        raise ProblemException("not_found")
    link = await db.get(ResourceLinkModel, resource_id)
    if link is None or str(link.library_item_id) != str(item.id):
        raise ProblemException("not_found")
    if link.status != "active":
        # suggested / dismissed は対象にしない。
        raise ProblemException("not_found")
    if link.kind != "github":
        raise ProblemException(
            "validation_error", detail="コード対応解析は GitHub リポジトリのみ対象です"
        )
    gh = _parse_github(link.url)
    if gh is None:
        raise ProblemException("validation_error", detail="GitHub リポジトリ URL を解釈できません")
    return link, gh


async def _latest_revision(db: AsyncSession, paper: Paper) -> DocumentRevision | None:
    if paper.latest_revision_id:
        rev = await db.get(DocumentRevision, paper.latest_revision_id)
        if rev is not None:
            return rev
    row = (
        await db.execute(
            select(DocumentRevision)
            .where(DocumentRevision.paper_id == paper.id)
            .order_by(DocumentRevision.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


def _code_analysis_settings(user_settings: dict[str, Any]) -> FullSettings:
    return FullSettings.model_validate(deep_merge(DEFAULTS, user_settings or {}))


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------------------- #
# POST estimate
# --------------------------------------------------------------------------- #
@router.post(
    "/api/library-items/{item_id}/code-analysis/estimate",
    response_model=CodeAnalysisEstimateResponse,
    operation_id="code_analysis_estimate",
)
async def estimate(
    item_id: str,
    body: EstimateRequest,
    user: CurrentUser,
    db: DbDep,
    resolve_repo: RepoResolverDep,
) -> CodeAnalysisEstimateResponse:
    item = await _owned_item(db, str(user.id), item_id)
    settings = _code_analysis_settings(user.settings or {})
    if settings.code_analysis.mode == "off":
        raise ProblemException("conflict", detail="設定でコード解析が無効です")

    link, (owner, repo) = await _validated_github_resource(db, item, body.resource_id)
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")
    revision = await _latest_revision(db, paper)
    if revision is None:
        raise ProblemException("conflict", detail="論文本文がまだ準備できていません")

    try:
        meta = await resolve_repo(owner, repo)
    except GitHubError as exc:
        if exc.code == "not_public":
            raise ProblemException(
                "not_found", detail="公開リポジトリのみ対応しています"
            ) from exc
        if exc.code == "tree_truncated":
            raise ProblemException(
                "validation_error", detail="リポジトリが大きすぎます"
            ) from exc
        if exc.code == "rate_limited":
            raise ProblemException(
                "rate_limited", detail="GitHub のレート制限中です"
            ) from exc
        raise ProblemException("provider_error", detail="GitHub の取得に失敗しました") from exc

    content = DocumentContent.model_validate(revision.content)
    claim_set = extract_claims(content, str(revision.id), max_claims=MAX_CLAIMS)
    # tree のファイル数と概算コード量で chunk 数を保守的に見積もる(1 file ≈ 4 chunk 上限想定)。
    files = len(meta.tree_files)
    total_code_chars = max(meta.total_code_bytes, files * 200)
    chunk_count = max(files, total_code_chars // 1500)

    cost = estimate_tokens_and_cost(
        total_code_chars=total_code_chars,
        chunk_count=chunk_count,
        files=files,
        claim_count=len(claim_set.claims) or 1,
        pricing=_DEFAULT_PRICING,
    )

    remaining = await budget_remaining_usd(
        db, str(user.id), settings.code_analysis.monthly_budget_usd
    )

    est = CodeAnalysisEstimate(
        id=str(uuid.uuid4()),
        user_id=str(user.id),
        library_item_id=str(item.id),
        resource_id=str(link.id),
        revision_id=str(revision.id),
        commit_sha=meta.commit_sha,
        analysis_version=ANALYSIS_VERSION,
        files=files,
        estimated_input_tokens=cost.estimated_input_tokens,
        estimated_output_tokens=cost.estimated_output_tokens,
        estimated_embedding_tokens=cost.estimated_embedding_tokens,
        estimated_cost_usd=cost.estimated_cost_usd,
        model_id="claude-sonnet-5",
        section_ids=list(body.section_ids or []),
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=10),
    )
    db.add(est)
    await db.commit()

    return CodeAnalysisEstimateResponse(
        estimate_id=str(est.id),
        commit_sha=meta.commit_sha,
        files=files,
        estimated_input_tokens=cost.estimated_input_tokens,
        estimated_output_tokens=cost.estimated_output_tokens,
        estimated_embedding_tokens=cost.estimated_embedding_tokens,
        estimated_cost_usd=cost.estimated_cost_usd,
        budget_remaining_usd=remaining,
        expires_at=est.expires_at.isoformat(),
    )


# --------------------------------------------------------------------------- #
# POST start
# --------------------------------------------------------------------------- #
@router.post(
    "/api/library-items/{item_id}/code-analysis",
    response_model=StartResponse,
    status_code=202,
    operation_id="code_analysis_start",
)
async def start(
    item_id: str,
    body: StartRequest,
    user: CurrentUser,
    db: DbDep,
    wakeup: JobWakeupDep,
) -> StartResponse:
    item = await _owned_item(db, str(user.id), item_id)
    settings = _code_analysis_settings(user.settings or {})
    if settings.code_analysis.mode == "off":
        raise ProblemException("conflict", detail="設定でコード解析が無効です")

    link, _gh = await _validated_github_resource(db, item, body.resource_id)

    if not _valid_uuid(body.estimate_id):
        raise ProblemException("not_found")
    est = await db.get(CodeAnalysisEstimate, body.estimate_id)
    if est is None or str(est.user_id) != str(user.id) or str(est.library_item_id) != str(item.id):
        raise ProblemException("not_found")
    if str(est.resource_id) != str(link.id):
        raise ProblemException("conflict", detail="見積りとリソースが一致しません")
    now = dt.datetime.now(dt.UTC)
    expires_at = est.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.UTC)
    if expires_at < now:
        raise ProblemException(
            "conflict", detail="見積りの有効期限が切れています。再見積もりが必要です"
        )

    # 冪等: 同一 (user, revision, resource, commit, analysis_version) の成功結果を再利用する。
    existing = (
        await db.execute(
            select(CodeAnalysisRun).where(
                CodeAnalysisRun.user_id == str(user.id),
                CodeAnalysisRun.revision_id == est.revision_id,
                CodeAnalysisRun.resource_id == str(link.id),
                CodeAnalysisRun.commit_sha == est.commit_sha,
                CodeAnalysisRun.analysis_version == est.analysis_version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status == "succeeded" and not existing.stale:
            return StartResponse(
                job_id=str(existing.job_id or ""), run_id=str(existing.id), status="succeeded"
            )
        if existing.status in ("queued", "running"):
            # 重複 job を作らず既存を返す。
            return StartResponse(
                job_id=str(existing.job_id or ""), run_id=str(existing.id), status=existing.status
            )

    # 予算再検査(見積り費用を足して当月予算内か)。超過なら外部 API を呼ばず waiting_budget。
    remaining = await budget_remaining_usd(
        db, str(user.id), settings.code_analysis.monthly_budget_usd
    )
    over_budget = est.estimated_cost_usd > remaining

    store = JobStore(db)
    payload = {
        "resource_id": str(link.id),
        "revision_id": str(est.revision_id),
        "commit_sha": est.commit_sha,
        "estimate_id": str(est.id),
        "analysis_version": est.analysis_version,
        "section_ids": list(est.section_ids or []),
        "trigger": "on_demand",
    }
    key = idempotency_key(
        user_id=str(user.id),
        revision_id=str(est.revision_id),
        resource_id=str(link.id),
        commit_sha=est.commit_sha,
        analysis_version=est.analysis_version,
    )

    run = existing or CodeAnalysisRun(
        id=str(uuid.uuid4()),
        user_id=str(user.id),
        library_item_id=str(item.id),
        resource_id=str(link.id),
        revision_id=str(est.revision_id),
        commit_sha=est.commit_sha,
        analysis_version=est.analysis_version,
        trigger="on_demand",
        estimated_cost_usd=est.estimated_cost_usd,
    )
    run.stale = False
    run.error = None

    if over_budget:
        run.status = "waiting_budget"
        if existing is None:
            db.add(run)
        await db.flush()
        # 通知を作る(外部 API は呼ばない。設計 §7)。
        db.add(
            Notification(
                user_id=str(user.id),
                kind="code_analysis_waiting_budget",
                payload={"run_id": str(run.id), "library_item_id": str(item.id)},
            )
        )
        await db.commit()
        return StartResponse(job_id="", run_id=str(run.id), status="waiting_budget")

    run.status = "queued"
    if existing is None:
        db.add(run)
    await db.flush()

    job_id = await store.enqueue_uncommitted(
        kind="code_analysis",
        payload={**payload, "run_id": str(run.id)},
        idempotency_key=key,
        priority="bulk",
        user_id=str(user.id),
        paper_id=str(item.paper_id),
        library_item_id=str(item.id),
    )
    run.job_id = job_id
    await db.commit()
    await wakeup(job_id)
    return StartResponse(job_id=job_id, run_id=str(run.id), status="queued")


# --------------------------------------------------------------------------- #
# GET runs + result
# --------------------------------------------------------------------------- #
@router.get(
    "/api/library-items/{item_id}/code-analysis",
    response_model=RunsResponse,
    operation_id="code_analysis_list",
)
async def list_runs(
    item_id: str,
    user: CurrentUser,
    db: DbDep,
) -> RunsResponse:
    item = await _owned_item(db, str(user.id), item_id)
    rows = (
        await db.execute(
            select(CodeAnalysisRun)
            .where(
                CodeAnalysisRun.library_item_id == str(item.id),
                CodeAnalysisRun.user_id == str(user.id),
            )
            .order_by(CodeAnalysisRun.created_at.desc())
        )
    ).scalars().all()

    def _run_out(r: CodeAnalysisRun) -> RunOut:
        return RunOut(
            run_id=str(r.id),
            resource_id=str(r.resource_id),
            revision_id=str(r.revision_id),
            commit_sha=r.commit_sha,
            trigger=r.trigger,
            status=r.status,
            stale=r.stale,
            estimated_cost_usd=Decimal(str(r.estimated_cost_usd)),
            actual_cost_usd=Decimal(str(r.actual_cost_usd)),
            error=r.error,
            created_at=_iso(r.created_at),
            finished_at=_iso(r.finished_at),
        )

    runs = [_run_out(r) for r in rows]
    current = next((r for r in rows if r.status == "succeeded"), None)
    correspondences: list[CorrespondenceOut] = []
    if current is not None:
        corr_rows = (
            await db.execute(
                select(CodeCorrespondence)
                .where(CodeCorrespondence.run_id == str(current.id))
                .order_by(CodeCorrespondence.position)
            )
        ).scalars().all()
        correspondences = [
            CorrespondenceOut(
                paper_anchor=c.paper_anchor,
                claim_text=c.claim_text,
                path=c.path,
                symbol=c.symbol,
                start_line=c.start_line,
                end_line=c.end_line,
                code_excerpt=c.code_excerpt,
                explanation_ja=c.explanation_ja,
                confidence=c.confidence,
            )
            for c in corr_rows
        ]
    return RunsResponse(
        runs=runs,
        current_result=_run_out(current) if current is not None else None,
        correspondences=correspondences,
        stale=bool(current.stale) if current is not None else False,
    )
