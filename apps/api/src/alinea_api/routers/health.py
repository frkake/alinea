"""ヘルスチェック。healthz=生存、readyz=依存(DB/Redis)到達性。"""

from __future__ import annotations

import asyncio
from typing import Any

from alinea_core.parsing.pdf_parser import PdfOcrReadiness, check_pdf_ocr_readiness
from fastapi import APIRouter
from sqlalchemy import text

from alinea_api.deps import DbDep, RedisDep
from alinea_api.errors import ProblemException

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/healthz", operation_id="health_livez")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", operation_id="health_readyz")
async def readyz(db: DbDep, r: RedisDep) -> dict[str, Any]:
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
    try:
        await r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"
    try:
        ocr_readiness = await asyncio.to_thread(check_pdf_ocr_readiness)
    except Exception:
        ocr_readiness = PdfOcrReadiness(False, "ocr_readiness_failed", "eng")
    checks["pdf_ocr"] = "ok" if ocr_readiness.available else "unavailable"
    if any(checks[name] != "ok" for name in ("db", "redis")):
        raise ProblemException(
            "service_unavailable", detail="依存サービスに到達できません", errors=None
        )
    return {
        "status": "ready",
        "checks": checks,
        "diagnostics": {"pdf_ocr": ocr_readiness.as_dict()},
    }
