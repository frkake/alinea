"""PF-01: healthz/readyz・RFC 9457 Problem Details・レート制限ヘッダ・OpenAPI エクスポート。"""

from __future__ import annotations

import threading

import pytest
from alinea_api.routers import health as health_router
from alinea_core.parsing.pdf_parser import PdfOcrReadiness
from httpx import AsyncClient


async def test_healthz_ok(client: AsyncClient) -> None:
    r = await client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_readyz_reports_dependencies(client: AsyncClient) -> None:
    r = await client.get("/api/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["redis"] == "ok"


async def test_readyz_reports_optional_ocr_unavailable_without_failing_service(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_thread = threading.current_thread()
    probe_threads: list[threading.Thread] = []

    def probe() -> PdfOcrReadiness:
        probe_threads.append(threading.current_thread())
        return PdfOcrReadiness(False, "ocr_engine_unavailable", "eng")

    monkeypatch.setattr(
        health_router,
        "check_pdf_ocr_readiness",
        probe,
        raising=False,
    )

    response = await client.get("/api/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["pdf_ocr"] == "unavailable"
    assert body["diagnostics"]["pdf_ocr"] == {
        "available": False,
        "code": "ocr_engine_unavailable",
        "language": "eng",
    }
    assert probe_threads and probe_threads[0] is not main_thread


async def test_readyz_contains_unexpected_optional_ocr_probe_failure(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_probe() -> PdfOcrReadiness:
        raise RuntimeError("synthetic OCR probe failure")

    monkeypatch.setattr(
        health_router,
        "check_pdf_ocr_readiness",
        fail_probe,
        raising=False,
    )

    response = await client.get("/api/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["redis"] == "ok"
    assert body["checks"]["pdf_ocr"] == "unavailable"
    assert body["diagnostics"]["pdf_ocr"] == {
        "available": False,
        "code": "ocr_readiness_failed",
        "language": "eng",
    }


async def test_not_found_is_problem_json(client: AsyncClient) -> None:
    r = await client.get("/api/nonexistent")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "not_found"
    assert body["type"] == "https://alinea.app/problems/not-found"
    assert body["status"] == 404


async def test_validation_error_has_errors_list(client: AsyncClient) -> None:
    r = await client.post("/api/auth/email/request", json={"email": "not-an-email"})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["errors"], list)
    assert len(body["errors"]) >= 1
    assert "field" in body["errors"][0] and "message" in body["errors"][0]


async def test_request_id_header_present(client: AsyncClient) -> None:
    r = await client.get("/api/healthz")
    assert r.headers.get("X-Request-Id")


async def test_rate_limit_headers_present(client: AsyncClient) -> None:
    r = await client.get("/api/healthz")
    assert r.headers.get("X-RateLimit-Limit")
    assert r.headers.get("X-RateLimit-Remaining") is not None
    assert r.headers.get("X-RateLimit-Reset")


async def test_openapi_export_starts_with_3(client: AsyncClient) -> None:
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200
    assert r.json()["openapi"].startswith("3.")
