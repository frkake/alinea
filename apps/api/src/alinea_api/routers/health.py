"""ヘルスチェック。healthz=生存、readyz=依存(DB/Redis)到達性。"""

from __future__ import annotations

from typing import Any

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
    if any(value != "ok" for value in checks.values()):
        raise ProblemException(
            "service_unavailable", detail="依存サービスに到達できません", errors=None
        )
    return {"status": "ready", "checks": checks}
