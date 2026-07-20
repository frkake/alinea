"""プレゼンテーション(論文→PPTX)API テスト(Task 28)。

- POST /api/library-items/{id}/presentation: 3 preset + audience 既定・任意指示 500 文字上限・
  active job 再利用(二重生成防止)・API キー無しは job 作成前に Problem。
- GET /api/library-items/{id}/presentation: 最新 artifact の metadata + 進行中 job。
- GET /api/library-items/{id}/presentation/download: 所有者確認後に PPTX を stream。
- 再生成は DB が新 storage key を指すまで旧 key(既存成果物)を消さない(no-overwrite key)。

DB は実 PostgreSQL、S3 は実 MinIO(apps/api conftest / test_figures_api と同じ規約)。
LLM ネットワークは叩かない(ルート解決とキー有無判定のみ)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import factories
import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import Job, PresentationArtifact
from alinea_core.storage.s3 import S3Storage, StorageKeys
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app(*, blank_operator_keys: bool = False) -> FastAPI:
    """本タスク所有ルータのみをマウントしたアプリ(main.create_app と同じ共通基盤)。"""
    from alinea_api.deps import get_settings_dep
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import library_items, presentations
    from alinea_api.settings import ApiSettings, get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(library_items.router)
    app.include_router(presentations.router)

    # arq への起床通知はテストでは no-op(DB が真実。arq 到達性に依存しない)。
    async def _noop(job_id: str) -> None:
        return None

    app.dependency_overrides[presentations.get_presentation_job_wakeup] = lambda: _noop

    if blank_operator_keys:
        # 運営キーを全て空にした設定(BYOK も無い → 使えるキーが 1 つも無い状態)。
        def _blank_settings() -> ApiSettings:
            return ApiSettings(
                openai_api_key="",
                anthropic_api_key="",
                gemini_api_key="",
                deepseek_api_key="",
                xai_api_key="",
            )

        app.dependency_overrides[get_settings_dep] = _blank_settings
    return app


async def _client_for(app: FastAPI, token: str) -> AsyncClient:
    transport = ASGITransport(app=app)
    ac = AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    )
    ac.cookies.set("yk_session", token)
    return ac


@pytest_asyncio.fixture
async def owner_ctx(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[dict[str, Any]]:
    """所有者ユーザー + ready revision を持つ library item を用意する。"""
    user = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    revision = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    uid = str(user.id)
    item_id = str(item.id)
    revision_id = str(revision.id)
    await db_session.commit()
    token = await create_session(redis_client, uid)
    try:
        yield {
            "user_id": uid,
            "item_id": item_id,
            "revision_id": revision_id,
            "token": token,
        }
    finally:
        await db_session.rollback()
        await purge_user(db_session, uid)


# ---------------------------------------------------------------------------
# POST: 3 preset + audience 既定・指示上限・二重生成防止
# ---------------------------------------------------------------------------
async def test_post_creates_presentation_job_with_preset_and_default_audience(
    owner_ctx: dict[str, Any], db_session: AsyncSession
) -> None:
    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        for preset, expected_audience in (
            ("reading_group", "students"),
            ("research_talk", "researchers"),
            ("implementation", "practitioners"),
        ):
            r = await ac.post(
                f"/api/library-items/{owner_ctx['item_id']}/presentation",
                json={"preset": preset},
            )
            assert r.status_code == 202, (preset, r.text)
            job_id = r.json()["job_id"]

            await db_session.rollback()
            job = await db_session.get(Job, job_id)
            assert job is not None
            assert job.kind == "presentation"
            assert job.payload["preset"] == preset
            assert job.payload["audience"] == expected_audience
            assert job.payload["library_item_id"] == owner_ctx["item_id"]
            assert job.payload["source_revision_id"] == owner_ctx["revision_id"]
            assert "instruction" not in job.payload or len(job.payload["instruction"]) <= 500

            # 次の preset の active job 再利用を避けるため終端化する。
            job.status = "succeeded"
            await db_session.commit()


async def test_post_reuses_active_job_for_same_paper(
    owner_ctx: dict[str, Any]
) -> None:
    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        first = await ac.post(
            f"/api/library-items/{owner_ctx['item_id']}/presentation",
            json={"preset": "reading_group"},
        )
        assert first.status_code == 202
        second = await ac.post(
            f"/api/library-items/{owner_ctx['item_id']}/presentation",
            json={"preset": "research_talk"},
        )
        assert second.status_code == 202
        # 進行中 job があれば新規生成せず同じ job を返す(二重生成防止)。
        assert second.json()["job_id"] == first.json()["job_id"]


async def test_post_rejects_instruction_over_500_chars(
    owner_ctx: dict[str, Any]
) -> None:
    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        r = await ac.post(
            f"/api/library-items/{owner_ctx['item_id']}/presentation",
            json={"preset": "reading_group", "instruction": "あ" * 501},
        )
        assert r.status_code == 422
        r_ok = await ac.post(
            f"/api/library-items/{owner_ctx['item_id']}/presentation",
            json={"preset": "reading_group", "instruction": "あ" * 500},
        )
        assert r_ok.status_code == 202


async def test_post_invalid_preset_is_422(owner_ctx: dict[str, Any]) -> None:
    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        r = await ac.post(
            f"/api/library-items/{owner_ctx['item_id']}/presentation",
            json={"preset": "keynote"},
        )
        assert r.status_code == 422


async def test_post_other_user_item_is_404(
    owner_ctx: dict[str, Any], db_session: AsyncSession, redis_client: Any
) -> None:
    other = await factories.make_user(db_session)
    other_id = str(other.id)
    await db_session.commit()
    token = await create_session(redis_client, other_id)
    app = _build_app()
    try:
        async with await _client_for(app, token) as ac:
            r = await ac.post(
                f"/api/library-items/{owner_ctx['item_id']}/presentation",
                json={"preset": "reading_group"},
            )
            assert r.status_code == 404
    finally:
        await db_session.rollback()
        await purge_user(db_session, other_id)


async def test_post_no_usable_api_key_is_problem_before_job(
    owner_ctx: dict[str, Any], db_session: AsyncSession
) -> None:
    app = _build_app(blank_operator_keys=True)
    async with await _client_for(app, owner_ctx["token"]) as ac:
        r = await ac.post(
            f"/api/library-items/{owner_ctx['item_id']}/presentation",
            json={"preset": "reading_group"},
        )
        assert r.status_code >= 400
        assert r.headers["content-type"].startswith("application/problem+json")
        # job は作られていない(Problem を job 作成前に返す)。
        await db_session.rollback()
        jobs = (
            await db_session.execute(
                select(Job).where(
                    Job.library_item_id == owner_ctx["item_id"], Job.kind == "presentation"
                )
            )
        ).scalars().all()
        assert jobs == []


# ---------------------------------------------------------------------------
# GET: 最新 metadata + 進行中 job
# ---------------------------------------------------------------------------
async def test_get_returns_in_progress_job(owner_ctx: dict[str, Any]) -> None:
    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        post = await ac.post(
            f"/api/library-items/{owner_ctx['item_id']}/presentation",
            json={"preset": "reading_group"},
        )
        assert post.status_code == 202
        r = await ac.get(f"/api/library-items/{owner_ctx['item_id']}/presentation")
        assert r.status_code == 200
        body = r.json()
        assert body["artifact"] is None
        assert body["job"] is not None
        assert body["job"]["id"] == post.json()["job_id"]


async def test_get_returns_latest_artifact(
    owner_ctx: dict[str, Any], db_session: AsyncSession
) -> None:
    key = StorageKeys.presentation_pptx(owner_ctx["item_id"], str(uuid.uuid4()))
    db_session.add(
        PresentationArtifact(
            id=str(uuid.uuid4()),
            library_item_id=owner_ctx["item_id"],
            source_revision_id=owner_ctx["revision_id"],
            preset="reading_group",
            audience="students",
            instruction="要点だけ",
            model_provider="openai",
            model_id="gpt-5.5",
            ppt_master_revision="0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f",
            pptx_storage_key=key,
        )
    )
    await db_session.commit()

    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        r = await ac.get(f"/api/library-items/{owner_ctx['item_id']}/presentation")
        assert r.status_code == 200
        artifact = r.json()["artifact"]
        assert artifact is not None
        assert artifact["preset"] == "reading_group"
        assert artifact["audience"] == "students"
        assert artifact["model_id"] == "gpt-5.5"
        # 平文 storage key を露出しない(download エンドポイント経由で取得)。
        assert "pptx_storage_key" not in artifact


async def test_get_other_user_is_404(
    owner_ctx: dict[str, Any], db_session: AsyncSession, redis_client: Any
) -> None:
    other = await factories.make_user(db_session)
    other_id = str(other.id)
    await db_session.commit()
    token = await create_session(redis_client, other_id)
    app = _build_app()
    try:
        async with await _client_for(app, token) as ac:
            r = await ac.get(f"/api/library-items/{owner_ctx['item_id']}/presentation")
            assert r.status_code == 404
    finally:
        await db_session.rollback()
        await purge_user(db_session, other_id)


# ---------------------------------------------------------------------------
# download: 所有者確認 + PPTX stream
# ---------------------------------------------------------------------------
async def test_download_ungenerated_is_404(owner_ctx: dict[str, Any]) -> None:
    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        r = await ac.get(
            f"/api/library-items/{owner_ctx['item_id']}/presentation/download"
        )
        assert r.status_code == 404


async def test_download_streams_pptx(
    owner_ctx: dict[str, Any], db_session: AsyncSession
) -> None:
    storage = S3Storage()
    key = StorageKeys.presentation_pptx(owner_ctx["item_id"], str(uuid.uuid4()))
    payload = b"PK\x03\x04 fake pptx bytes"
    await storage.put(
        storage.assets_bucket,
        key,
        payload,
        content_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    )
    db_session.add(
        PresentationArtifact(
            id=str(uuid.uuid4()),
            library_item_id=owner_ctx["item_id"],
            source_revision_id=owner_ctx["revision_id"],
            preset="reading_group",
            audience="students",
            model_provider="openai",
            model_id="gpt-5.5",
            ppt_master_revision="rev",
            pptx_storage_key=key,
        )
    )
    await db_session.commit()

    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        r = await ac.get(
            f"/api/library-items/{owner_ctx['item_id']}/presentation/download"
        )
        assert r.status_code == 200
        assert r.content == payload
        assert "presentationml" in r.headers["content-type"]


async def test_download_other_user_is_404(
    owner_ctx: dict[str, Any], db_session: AsyncSession, redis_client: Any
) -> None:
    key = StorageKeys.presentation_pptx(owner_ctx["item_id"], str(uuid.uuid4()))
    db_session.add(
        PresentationArtifact(
            id=str(uuid.uuid4()),
            library_item_id=owner_ctx["item_id"],
            source_revision_id=owner_ctx["revision_id"],
            preset="reading_group",
            audience="students",
            model_provider="openai",
            model_id="gpt-5.5",
            ppt_master_revision="rev",
            pptx_storage_key=key,
        )
    )
    await db_session.commit()

    other = await factories.make_user(db_session)
    other_id = str(other.id)
    await db_session.commit()
    token = await create_session(redis_client, other_id)
    app = _build_app()
    try:
        async with await _client_for(app, token) as ac:
            r = await ac.get(
                f"/api/library-items/{owner_ctx['item_id']}/presentation/download"
            )
            assert r.status_code == 404
    finally:
        await db_session.rollback()
        await purge_user(db_session, other_id)


# ---------------------------------------------------------------------------
# 再生成は DB が新 key を指すまで旧 key(既存成果物)を消さない(no-overwrite)
# ---------------------------------------------------------------------------
async def test_regen_keeps_existing_artifact_and_uses_new_key(
    owner_ctx: dict[str, Any], db_session: AsyncSession
) -> None:
    old_job_id = str(uuid.uuid4())
    old_key = StorageKeys.presentation_pptx(owner_ctx["item_id"], old_job_id)
    generated_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    artifact_id = str(uuid.uuid4())
    db_session.add(
        PresentationArtifact(
            id=artifact_id,
            library_item_id=owner_ctx["item_id"],
            source_revision_id=owner_ctx["revision_id"],
            generation_job_id=old_job_id,
            preset="reading_group",
            audience="students",
            model_provider="openai",
            model_id="gpt-5.5",
            ppt_master_revision="rev",
            pptx_storage_key=old_key,
            generated_at=generated_at,
        )
    )
    await db_session.commit()

    app = _build_app()
    async with await _client_for(app, owner_ctx["token"]) as ac:
        r = await ac.post(
            f"/api/library-items/{owner_ctx['item_id']}/presentation",
            json={"preset": "research_talk"},
        )
        assert r.status_code == 202
        new_job_id = r.json()["job_id"]

    # 既存成果物は POST では一切変更されない(旧 key を指したまま = 旧成功が生き残る)。
    await db_session.rollback()
    artifact = await db_session.get(PresentationArtifact, artifact_id)
    assert artifact is not None
    assert artifact.pptx_storage_key == old_key
    assert str(artifact.generation_job_id) == old_job_id
    # 新 job の書き込み先 key は job id 別で旧 key を上書きしない。
    new_key = StorageKeys.presentation_pptx(owner_ctx["item_id"], new_job_id)
    assert new_key != old_key


# ---------------------------------------------------------------------------
# storage key は job 別で上書きしない(no-overwrite key scheme)
# ---------------------------------------------------------------------------
def test_presentation_pptx_key_is_job_scoped() -> None:
    item = str(uuid.uuid4())
    j1 = str(uuid.uuid4())
    j2 = str(uuid.uuid4())
    assert StorageKeys.presentation_pptx(item, j1) == f"presentations/{item}/{j1}.pptx"
    assert StorageKeys.presentation_pptx(item, j1) != StorageKeys.presentation_pptx(item, j2)
