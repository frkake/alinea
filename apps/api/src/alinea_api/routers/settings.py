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

from alinea_api.deps import CurrentUser, DbDep, RedisDep
from alinea_api.errors import ProblemError, ProblemException
from alinea_api.llm.route_store import DbRouteStore
from alinea_api.schemas.settings import DEFAULTS, FullSettings, deep_merge

router = APIRouter(prefix="/api/settings", tags=["settings"])

# settings.llm_routing のキー → llm_task_routes.task / user_task_model_overrides.task。
# UI のタスク名(§17.1)と実行時ルートの task 名は一部異なる(plans/04 §15)。
# figure_image は画像ルート(worker の image_router 経由・user_id なし)のためブリッジ対象外。
_ROUTING_KEY_TO_TASK: dict[str, str] = {
    "translation": "translation",
    "retranslation": "retranslation_escalation",
    "chat": "chat",
    "summary": "summary",
    "article": "article",
    "vocab": "vocab",
    "figure_dsl": "overview_figure_dsl",
    "presentation": "presentation",
}


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


async def _validate_model(db: DbDep, provider: str, model_id: str) -> None:
    """model_id が llm_models に実在・enabled・provider 一致であることを検証する。

    user_task_model_overrides.model_id は llm_models(id) への FK。不正値を挿入すると
    FK 違反(500)になるため、既存の値域違反と同じ 422 validation_error に変換する。
    """
    row = (
        await db.execute(
            text("SELECT provider FROM llm_models WHERE id = :id AND enabled = true"),
            {"id": model_id},
        )
    ).scalar_one_or_none()
    if row is None or row != provider:
        raise ProblemException(
            "validation_error",
            errors=[
                ProblemError(
                    field="body.llm_routing",
                    message=f"モデル {model_id} は provider {provider} で利用できません",
                )
            ],
        )


async def _bridge_routing_overrides(
    db: DbDep, r: RedisDep, user_id: str, patch: dict[str, Any], effective: FullSettings
) -> None:
    """PATCH に含まれる llm_routing.<task> を user_task_model_overrides へ upsert する(S1 #1)。

    実行時のルート解決(DbRouteStore)は overrides テーブルを正とするため、settings への
    保存だけでは provider/model 選択が効かない。ここで両者を橋渡しする。検証は commit 前に
    行い(不正 model は 422)、成功時のみ Redis ルートキャッシュを無効化する。
    """
    routing_patch = patch.get("llm_routing")
    if not isinstance(routing_patch, dict):
        return
    routing = effective.llm_routing
    upserts: list[tuple[str, str]] = []  # (route_task, model_id)
    for key, task in _ROUTING_KEY_TO_TASK.items():
        if key not in routing_patch:
            continue
        entry = getattr(routing, key)
        await _validate_model(db, entry.provider, entry.model)  # 不正なら 422(commit 前)
        upserts.append((task, entry.model))

    for task, model_id in upserts:
        await db.execute(
            text(
                "INSERT INTO user_task_model_overrides (user_id, task, model_id) "
                "VALUES (CAST(:u AS uuid), :t, :m) "
                "ON CONFLICT (user_id, task) DO UPDATE "
                "SET model_id = EXCLUDED.model_id, updated_at = now()"
            ),
            {"u": user_id, "t": task, "m": model_id},
        )

    if upserts:
        route_store = DbRouteStore(db, r)
        for task, _ in upserts:
            await route_store.invalidate(task, user_id)


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
    user: CurrentUser, db: DbDep, r: RedisDep, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    current = deep_merge(DEFAULTS, user.settings or {})
    patch = body or {}
    merged = deep_merge(current, patch)
    validated = _validate(merged)  # 値域違反はここで 422。
    user.settings = validated.model_dump()
    # llm_routing の provider/model 選択を実行時ルート(overrides テーブル)へ反映する(S1 #1)。
    # 不正 model はここで 422(commit 前)なので、設定本体も部分適用されない。
    await _bridge_routing_overrides(db, r, str(user.id), patch, validated)
    await db.commit()
    payload = validated.model_dump()
    payload["available_models"] = await _available_models(db)
    return payload
