"""settings — ユーザー設定(plans/03 §17.1・§17.2)。

- ``GET /api/settings``: 既定値を含む完全形 + 付帯フィールド ``available_models``(§17.1)。
- ``PATCH /api/settings``: deep merge(指定キーのみ)+ 値域検証(不正値は 422 ``validation_error``)。

保存は ``users.settings``(JSONB)へ検証済み完全形を格納する。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError
from sqlalchemy import text

from alinea_api.deps import CurrentUser, DbDep
from alinea_api.errors import ProblemError, ProblemException
from alinea_api.schemas.settings import DEFAULTS, FullSettings, deep_merge

router = APIRouter(prefix="/api/settings", tags=["settings"])


async def _available_models(db: DbDep) -> dict[str, list[dict[str, str]]]:
    """有効モデルを provider 別にまとめる(§17.1 付帯フィールド。実行時の正は DB llm_models)。"""
    rows = await db.execute(
        text(
            "SELECT provider, id, display_name FROM llm_models "
            "WHERE enabled = true ORDER BY provider, id"
        )
    )
    out: dict[str, list[dict[str, str]]] = {}
    for provider, model_id, label in rows.all():
        out.setdefault(provider, []).append({"model": model_id, "label": label})
    return out


def _validate(merged: dict[str, Any]) -> FullSettings:
    try:
        return FullSettings.model_validate(merged)
    except ValidationError as exc:
        errors = [
            ProblemError(
                field="body." + ".".join(str(p) for p in err.get("loc", ())),
                message=str(err.get("msg", "")),
            )
            for err in exc.errors()
        ]
        raise ProblemException("validation_error", errors=errors) from exc


async def _respond(user_settings: dict[str, Any], db: DbDep) -> dict[str, Any]:
    effective = _validate(deep_merge(DEFAULTS, user_settings))
    payload = effective.model_dump()
    payload["available_models"] = await _available_models(db)
    return payload


@router.get("", operation_id="settings_get")
async def get_settings(user: CurrentUser, db: DbDep) -> dict[str, Any]:
    return await _respond(user.settings or {}, db)


@router.patch("", operation_id="settings_update")
async def update_settings(
    user: CurrentUser, db: DbDep, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    current = deep_merge(DEFAULTS, user.settings or {})
    merged = deep_merge(current, body or {})
    validated = _validate(merged)  # 値域違反はここで 422。
    user.settings = validated.model_dump()
    await db.commit()
    payload = validated.model_dump()
    payload["available_models"] = await _available_models(db)
    return payload
