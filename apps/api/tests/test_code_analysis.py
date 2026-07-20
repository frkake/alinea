"""code_analysis API テスト(Task 21・設計 §6-§10)。

実 GitHub / embedding / LLM へは接続しない。GitHub メタ解決 (RepoResolver) と job 起床
(JobWakeup) を dependency override で差し替える。DB は実 PostgreSQL(factories 経由)、認証は
セッション直発行 + cookie(test_resources.py と同方針)。

検証:
- 設定 off/on_demand/automatic と月額予算 0.00-100.00 の値域。
- 他ユーザーの Resource / suggested / dismissed / 非 GitHub Resource / private repo を拒否。
- 見積り(commit・files・token・費用・残予算・失効)。
- 同一対象の成功再利用・queued/running 重複作成防止(冪等)。
- 予算超過で waiting_budget + 通知、外部 API を呼ばない。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user
from alinea_core.code_analysis.github import GitHubError, RepoMetadata
from alinea_core.db.models import CodeAnalysisRun, Job, Notification
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app(repo_meta: RepoMetadata | Exception | None = None) -> tuple[FastAPI, dict]:
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import code_analysis as ca
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(ca.router)

    state: dict[str, Any] = {"resolver_calls": 0, "wakeups": []}

    async def _fake_resolver(owner: str, repo: str) -> RepoMetadata:
        state["resolver_calls"] += 1
        if isinstance(repo_meta, Exception):
            raise repo_meta
        if repo_meta is not None:
            return repo_meta
        return RepoMetadata(
            owner=owner,
            repo=repo,
            default_branch="main",
            commit_sha="a" * 40,
            tree_files=[f"src/mod{i}.py" for i in range(12)],
            total_code_bytes=48_000,
        )

    async def _fake_wakeup(job_id: str) -> None:
        state["wakeups"].append(job_id)

    app.dependency_overrides[ca.get_repo_resolver] = lambda: _fake_resolver
    app.dependency_overrides[ca.get_job_wakeup] = lambda: _fake_wakeup
    return app, state


@pytest_asyncio.fixture
async def env(
    db_session: AsyncSession, redis_client: Any, factories: Any
) -> AsyncIterator[dict[str, Any]]:
    user = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    revision = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    gh = await factories.make_resource_link(
        db_session,
        library_item=item,
        kind="github",
        url="https://github.com/gnobitab/RectifiedFlow",
        status="active",
    )
    uid = str(user.id)
    await db_session.commit()

    app, state = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        token = await create_session(redis_client, uid)
        ac.cookies.set("yk_session", token)
        try:
            yield {
                "client": ac,
                "app": app,
                "state": state,
                "item": item,
                "paper": paper,
                "revision": revision,
                "gh": gh,
                "uid": uid,
                "user": user,
                "db": db_session,
                "factories": factories,
            }
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


async def _set_mode(db: AsyncSession, user: Any, *, mode: str, budget: str = "5.00") -> None:
    user.settings = {"code_analysis": {"mode": mode, "monthly_budget_usd": budget}}
    await db.commit()


# --------------------------------------------------------------------------- #
# 設定値域(unit)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["off", "on_demand", "automatic"])
def test_settings_mode_accepts_three_values(mode: str) -> None:
    from alinea_api.schemas.settings import CodeAnalysisSettings

    s = CodeAnalysisSettings(mode=mode)
    assert s.mode == mode


def test_settings_default_is_on_demand_and_5usd() -> None:
    from alinea_api.schemas.settings import CodeAnalysisSettings

    s = CodeAnalysisSettings()
    assert s.mode == "on_demand"
    assert s.monthly_budget_usd == Decimal("5.00")


@pytest.mark.parametrize("bad", [Decimal("-0.01"), Decimal("100.01"), Decimal("250")])
def test_settings_budget_out_of_range_rejected(bad: Decimal) -> None:
    from alinea_api.schemas.settings import CodeAnalysisSettings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CodeAnalysisSettings(monthly_budget_usd=bad)


@pytest.mark.parametrize("ok", [Decimal("0.00"), Decimal("100.00"), Decimal("42.50")])
def test_settings_budget_in_range_ok(ok: Decimal) -> None:
    from alinea_api.schemas.settings import CodeAnalysisSettings

    assert CodeAnalysisSettings(monthly_budget_usd=ok).monthly_budget_usd == ok


# --------------------------------------------------------------------------- #
# 見積り
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_estimate_returns_commit_files_tokens_and_budget(env: dict[str, Any]) -> None:
    ac = env["client"]
    resp = await ac.post(
        f"/api/library-items/{env['item'].id}/code-analysis/estimate",
        json={"resource_id": str(env["gh"].id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["commit_sha"] == "a" * 40
    assert body["files"] == 12
    assert body["estimated_input_tokens"] > 0
    assert body["estimated_embedding_tokens"] > 0
    assert Decimal(str(body["estimated_cost_usd"])) > Decimal("0")
    assert "budget_remaining_usd" in body
    assert body["expires_at"]


@pytest.mark.asyncio
async def test_estimate_rejected_when_mode_off(env: dict[str, Any]) -> None:
    await _set_mode(env["db"], env["user"], mode="off")
    ac = env["client"]
    resp = await ac.post(
        f"/api/library-items/{env['item'].id}/code-analysis/estimate",
        json={"resource_id": str(env["gh"].id)},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_estimate_rejects_non_github_resource(env: dict[str, Any]) -> None:
    factories = env["factories"]
    db = env["db"]
    article = await factories.make_resource_link(
        db,
        library_item=env["item"],
        kind="article",
        url="https://example.com/post",
        url_normalized="https://example.com/post",
        status="active",
    )
    await db.commit()
    resp = await env["client"].post(
        f"/api/library-items/{env['item'].id}/code-analysis/estimate",
        json={"resource_id": str(article.id)},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_estimate_rejects_suggested_and_dismissed_resource(env: dict[str, Any]) -> None:
    factories = env["factories"]
    db = env["db"]
    for status in ("suggested", "dismissed"):
        link = await factories.make_resource_link(
            db,
            library_item=env["item"],
            kind="github",
            url=f"https://github.com/x/{status}",
            url_normalized=f"https://github.com/x/{status}",
            status=status,
        )
        await db.commit()
        resp = await env["client"].post(
            f"/api/library-items/{env['item'].id}/code-analysis/estimate",
            json={"resource_id": str(link.id)},
        )
        assert resp.status_code == 404, status


@pytest.mark.asyncio
async def test_estimate_rejects_other_users_resource(env: dict[str, Any]) -> None:
    factories = env["factories"]
    db = env["db"]
    other = await factories.make_user(db)
    other_paper = await factories.make_paper(db, owner=other, visibility="private")
    await factories.make_revision(db, paper=other_paper)
    other_item = await factories.make_library_item(db, user=other, paper=other_paper)
    other_gh = await factories.make_resource_link(
        db, library_item=other_item, kind="github", status="active"
    )
    await db.commit()
    try:
        # 自分の item に、他ユーザーの resource_id を渡す → 404。
        resp = await env["client"].post(
            f"/api/library-items/{env['item'].id}/code-analysis/estimate",
            json={"resource_id": str(other_gh.id)},
        )
        assert resp.status_code == 404
    finally:
        await db.rollback()
        await purge_user(db, str(other.id))


@pytest.mark.asyncio
async def test_estimate_truncated_tree_rejected(
    db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    user = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    gh = await factories.make_resource_link(
        db_session, library_item=item, kind="github", status="active"
    )
    uid = str(user.id)
    await db_session.commit()

    app, _state = _build_app(GitHubError("tree_truncated"))
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"}, trust_env=False,
    ) as ac:
        token = await create_session(redis_client, uid)
        ac.cookies.set("yk_session", token)
        try:
            resp = await ac.post(
                f"/api/library-items/{item.id}/code-analysis/estimate",
                json={"resource_id": str(gh.id)},
            )
            assert resp.status_code == 422
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


@pytest.mark.asyncio
async def test_estimate_private_repo_404(
    db_session: AsyncSession, redis_client: Any, factories: Any
) -> None:
    user = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    gh = await factories.make_resource_link(
        db_session, library_item=item, kind="github", status="active"
    )
    uid = str(user.id)
    await db_session.commit()

    app, _state = _build_app(GitHubError("not_public"))
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"}, trust_env=False,
    ) as ac:
        token = await create_session(redis_client, uid)
        ac.cookies.set("yk_session", token)
        try:
            resp = await ac.post(
                f"/api/library-items/{item.id}/code-analysis/estimate",
                json={"resource_id": str(gh.id)},
            )
            assert resp.status_code == 404
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


# --------------------------------------------------------------------------- #
# 開始 + 冪等 + 予算
# --------------------------------------------------------------------------- #
async def _estimate_id(ac: AsyncClient, item_id: str, resource_id: str) -> str:
    resp = await ac.post(
        f"/api/library-items/{item_id}/code-analysis/estimate",
        json={"resource_id": resource_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["estimate_id"]


@pytest.mark.asyncio
async def test_start_enqueues_job_and_creates_run(env: dict[str, Any]) -> None:
    ac = env["client"]
    est_id = await _estimate_id(ac, str(env["item"].id), str(env["gh"].id))
    resp = await ac.post(
        f"/api/library-items/{env['item'].id}/code-analysis",
        json={"resource_id": str(env["gh"].id), "estimate_id": est_id},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    assert body["job_id"] in env["state"]["wakeups"]

    db = env["db"]
    job = await db.get(Job, body["job_id"])
    assert job is not None
    assert job.kind == "code_analysis"
    assert job.status == "queued"
    run = await db.get(CodeAnalysisRun, body["run_id"])
    assert run is not None
    assert run.status == "queued"
    assert run.commit_sha == "a" * 40


@pytest.mark.asyncio
async def test_start_is_idempotent_for_queued(env: dict[str, Any]) -> None:
    ac = env["client"]
    est_id = await _estimate_id(ac, str(env["item"].id), str(env["gh"].id))
    r1 = await ac.post(
        f"/api/library-items/{env['item'].id}/code-analysis",
        json={"resource_id": str(env["gh"].id), "estimate_id": est_id},
    )
    # 2 回目は新しい見積りでも同じ対象 → 既存 queued run を返し、重複 job を作らない。
    est_id2 = await _estimate_id(ac, str(env["item"].id), str(env["gh"].id))
    r2 = await ac.post(
        f"/api/library-items/{env['item'].id}/code-analysis",
        json={"resource_id": str(env["gh"].id), "estimate_id": est_id2},
    )
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["run_id"] == r2.json()["run_id"]

    db = env["db"]
    jobs = (
        await db.execute(
            select(Job).where(Job.kind == "code_analysis", Job.library_item_id == str(env["item"].id))
        )
    ).scalars().all()
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_start_reuses_successful_run(env: dict[str, Any]) -> None:
    ac = env["client"]
    db = env["db"]
    # 既存の成功 run を作る(同一対象)。
    est_id = await _estimate_id(ac, str(env["item"].id), str(env["gh"].id))
    from alinea_core.db.models import CodeAnalysisEstimate

    est = await db.get(CodeAnalysisEstimate, est_id)
    run = CodeAnalysisRun(
        id=str(uuid.uuid4()),
        user_id=env["uid"],
        library_item_id=str(env["item"].id),
        resource_id=str(env["gh"].id),
        revision_id=str(est.revision_id),
        commit_sha=est.commit_sha,
        analysis_version=est.analysis_version,
        status="succeeded",
        job_id=str(uuid.uuid4()),
    )
    db.add(run)
    await db.commit()

    resp = await ac.post(
        f"/api/library-items/{env['item'].id}/code-analysis",
        json={"resource_id": str(env["gh"].id), "estimate_id": est_id},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["run_id"] == str(run.id)
    # 新しい job は作られない。
    assert env["state"]["wakeups"] == []


@pytest.mark.asyncio
async def test_start_over_budget_waiting_budget_and_notifies(env: dict[str, Any]) -> None:
    # 予算を 0 にして必ず超過させる。
    await _set_mode(env["db"], env["user"], mode="on_demand", budget="0.00")
    ac = env["client"]
    est_id = await _estimate_id(ac, str(env["item"].id), str(env["gh"].id))
    resp = await ac.post(
        f"/api/library-items/{env['item'].id}/code-analysis",
        json={"resource_id": str(env["gh"].id), "estimate_id": est_id},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "waiting_budget"
    assert body["job_id"] == ""
    # 外部 API(=job wakeup)は呼ばれない。
    assert env["state"]["wakeups"] == []

    db = env["db"]
    run = await db.get(CodeAnalysisRun, body["run_id"])
    assert run.status == "waiting_budget"
    notes = (
        await db.execute(
            select(Notification).where(
                Notification.user_id == env["uid"],
                Notification.kind == "code_analysis_waiting_budget",
            )
        )
    ).scalars().all()
    assert len(notes) == 1


@pytest.mark.asyncio
async def test_start_expired_estimate_conflict(env: dict[str, Any]) -> None:
    import datetime as dt

    from alinea_core.db.models import CodeAnalysisEstimate

    ac = env["client"]
    db = env["db"]
    est_id = await _estimate_id(ac, str(env["item"].id), str(env["gh"].id))
    est = await db.get(CodeAnalysisEstimate, est_id)
    est.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await db.commit()
    resp = await ac.post(
        f"/api/library-items/{env['item'].id}/code-analysis",
        json={"resource_id": str(env["gh"].id), "estimate_id": est_id},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_runs_returns_current_and_correspondences(env: dict[str, Any]) -> None:
    from alinea_core.db.models import CodeCorrespondence

    ac = env["client"]
    db = env["db"]
    est_id = await _estimate_id(ac, str(env["item"].id), str(env["gh"].id))
    from alinea_core.db.models import CodeAnalysisEstimate

    est = await db.get(CodeAnalysisEstimate, est_id)
    run = CodeAnalysisRun(
        id=str(uuid.uuid4()),
        user_id=env["uid"],
        library_item_id=str(env["item"].id),
        resource_id=str(env["gh"].id),
        revision_id=str(est.revision_id),
        commit_sha=est.commit_sha,
        analysis_version=est.analysis_version,
        status="succeeded",
    )
    db.add(run)
    await db.flush()
    db.add(
        CodeCorrespondence(
            id=str(uuid.uuid4()),
            run_id=str(run.id),
            position=0,
            paper_anchor={"block_id": "b1"},
            claim_text="claim",
            path="src/mod0.py",
            symbol="train",
            start_line=1,
            end_line=3,
            code_excerpt="def train():",
            explanation_ja="説明",
            confidence="high",
        )
    )
    await db.commit()

    resp = await ac.get(f"/api/library-items/{env['item'].id}/code-analysis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_result"]["run_id"] == str(run.id)
    assert len(body["correspondences"]) == 1
    assert body["correspondences"][0]["path"] == "src/mod0.py"
