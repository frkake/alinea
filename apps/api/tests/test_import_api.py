"""インポート API テスト(完全データ移行 Task 5)。

- POST /api/import/full: multipart zip → S3 保存 → import Job 202
- GET  /api/import/full/{job_id}: ジョブ状態・サマリ取得

DB は実 PostgreSQL・S3 は実 MinIO。
ジョブの実処理(zip 展開・冪等マージ復元)は apps/worker/tests/test_import_bulk.py で検証済み。
本ファイルは API 層(受け取り・S3 保存・ジョブ作成・状態返却)のみを検証する。
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.routers import export as export_router
from alinea_api.routers.export import get_export_job_wakeup, get_import_job_wakeup
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import Job
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    """import ルータを含むテスト専用アプリ(export ルータを共有)。"""
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import annotations, export
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(export.router)
    app.include_router(annotations.router)
    return app


async def _noop_wakeup(_job_id: str) -> None:
    """arq 接続を行わない no-op 起床通知。"""


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    email = f"imp-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
    token = await create_session(redis_client, user.id)
    app = _build_app()
    app.dependency_overrides[get_export_job_wakeup] = lambda: _noop_wakeup
    app.dependency_overrides[get_import_job_wakeup] = lambda: _noop_wakeup
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield ac, uid
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


def _make_fake_zip() -> bytes:
    """最小限の有効 zip ファイル(テスト用)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 2, "assets": []}))
        zf.writestr("data.json", json.dumps({"library": [], "user": {}}))
    return buf.getvalue()


async def test_import_full_rejects_upload_larger_than_limit(
    auth: tuple[AsyncClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """上限超過はS3保存・ジョブ作成前に 413 で拒否する。"""
    monkeypatch.setattr(export_router, "_MAX_IMPORT_ARCHIVE_BYTES", 8)
    client, _ = auth

    response = await client.post(
        "/api/import/full",
        files={"file": ("too-large.zip", b"012345678", "application/zip")},
    )

    assert response.status_code == 413


# ---------------------------------------------------------------------------
# POST /api/import/full: 202 + job_id
# ---------------------------------------------------------------------------

async def test_import_full_creates_job(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth

    files = {"file": ("backup.zip", _make_fake_zip(), "application/zip")}
    res = await client.post("/api/import/full", files=files)
    assert res.status_code == 202, res.text
    body = res.json()
    assert "job_id" in body
    job_id = body["job_id"]

    job = await db_session.get(Job, job_id)
    assert job is not None
    assert job.kind == "import"
    assert str(job.user_id) == uid
    assert "upload_key" in job.payload


# ---------------------------------------------------------------------------
# GET /api/import/full/{job_id}: ステータス取得
# ---------------------------------------------------------------------------

async def test_import_full_status_returns_job(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth

    files = {"file": ("backup.zip", _make_fake_zip(), "application/zip")}
    start = await client.post("/api/import/full", files=files)
    assert start.status_code == 202, start.text
    job_id = start.json()["job_id"]

    status_res = await client.get(f"/api/import/full/{job_id}")
    assert status_res.status_code == 200, status_res.text
    body = status_res.json()
    assert "job" in body
    assert body["job"]["id"] == job_id
    assert body["job"]["status"] == "queued"
    assert body["summary"] is None

    # 完了状態を直接 DB に書く(worker の責務のため)
    job = await db_session.get(Job, job_id, populate_existing=True)
    assert job is not None
    job.status = "succeeded"
    job.result = {"summary": {"created": {"library": 3}, "skipped": {}, "failed": []}}
    await db_session.commit()

    done = await client.get(f"/api/import/full/{job_id}")
    assert done.status_code == 200, done.text
    done_body = done.json()
    assert done_body["job"]["status"] == "succeeded"
    assert done_body["summary"]["created"]["library"] == 3


# ---------------------------------------------------------------------------
# 他ユーザーの job は 404
# ---------------------------------------------------------------------------

async def test_import_full_status_other_users_job_is_404(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, _uid = auth
    other_user = await factories.make_user(db_session)
    other_job = await factories.make_job(db_session, kind="import", user=other_user)
    await db_session.commit()
    try:
        resp = await client.get(f"/api/import/full/{other_job.id}")
        assert resp.status_code == 404, resp.text
    finally:
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()
