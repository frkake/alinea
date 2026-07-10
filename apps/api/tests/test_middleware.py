from __future__ import annotations

from alinea_api.middleware import OriginCsrfMiddleware
from alinea_api.settings import ApiSettings
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/write")
    async def write() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        OriginCsrfMiddleware,
        settings=ApiSettings(app_env="development", app_base_url="http://localhost:3000"),
    )
    return app


async def test_csrf_accepts_actual_request_origin() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://192.168.1.20:3000") as client:
        response = await client.post("/write", headers={"Origin": "http://192.168.1.20:3000"})

    assert response.status_code == 200


async def test_csrf_accepts_forwarded_request_origin() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://api:8000") as client:
        response = await client.post(
            "/write",
            headers={
                "Origin": "https://papers.example",
                "X-Forwarded-Host": "papers.example",
                "X-Forwarded-Proto": "https",
            },
        )

    assert response.status_code == 200


async def test_csrf_rejects_unrelated_origin() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://192.168.1.20:3000") as client:
        response = await client.post("/write", headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.json()["code"] == "origin_mismatch"
