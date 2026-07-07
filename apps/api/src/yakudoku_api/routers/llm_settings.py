"""settings(BYOK・クォータ) — plans/03 §17.3・§17.4、plans/04 §11。

- ``GET /api/settings/api-keys``: 登録済みキー一覧(平文なし・マスク表示)。
- ``PUT/DELETE /api/settings/api-keys/{provider}``: 暗号化 upsert / 削除(§11.3)。
- ``GET /api/settings/quota``: 5 カウンタの当月使用量と上限(§17.4)。

パスは plans/03 §17.3 を正とする(``/api/settings/api-keys``。plans/04 §11.3 参照)。
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response
from sqlalchemy import text

from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.llm.deps import quota_usage
from yakudoku_api.llm.key_store import DbKeyStore
from yakudoku_api.schemas.settings import (
    ApiKeyItem,
    ApiKeyListResponse,
    ApiKeyPutBody,
    ApiKeyPutResponse,
    ByokActive,
    QuotaCounter,
    QuotaResponse,
    QuotaUsage,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_KEY_PROVIDERS = ("openai", "anthropic", "google", "deepseek", "xai")
_IMAGE_PROVIDERS = frozenset({"openai", "google", "xai"})
_TEXT_PROVIDERS = frozenset({"openai", "anthropic", "google", "deepseek", "xai"})
_MASK_PREFIX = "sk-…"


def _check_provider(provider: str) -> None:
    if provider not in _KEY_PROVIDERS:
        raise ProblemException("validation_error", detail="未対応のプロバイダです")


@router.get("/api-keys", response_model=ApiKeyListResponse, operation_id="settings_list_api_keys")
async def list_api_keys(user: CurrentUser, db: DbDep) -> ApiKeyListResponse:
    rows = await db.execute(
        text(
            "SELECT provider, key_hint, status, last_tested_at, created_at "
            "FROM byok_api_keys WHERE user_id = CAST(:u AS uuid) ORDER BY provider"
        ),
        {"u": user.id},
    )
    items = [
        ApiKeyItem(
            provider=provider,
            masked=f"{_MASK_PREFIX}{key_hint}",
            status=status,
            last_tested_at=last_tested_at.isoformat() if last_tested_at else None,
            created_at=created_at.isoformat(),
        )
        for provider, key_hint, status, last_tested_at, created_at in rows.all()
    ]
    return ApiKeyListResponse(items=items)


@router.put(
    "/api-keys/{provider}",
    response_model=ApiKeyPutResponse,
    operation_id="settings_put_api_key",
)
async def put_api_key(
    provider: str, body: ApiKeyPutBody, user: CurrentUser, db: DbDep
) -> ApiKeyPutResponse:
    _check_provider(provider)
    store = DbKeyStore(db)
    await store.put(user_id=user.id, provider=provider, plaintext=body.api_key)
    await db.commit()
    row = (
        await db.execute(
            text(
                "SELECT key_hint, created_at FROM byok_api_keys "
                "WHERE user_id = CAST(:u AS uuid) AND provider = :p"
            ),
            {"u": user.id, "p": provider},
        )
    ).first()
    assert row is not None  # put 済み
    return ApiKeyPutResponse(
        provider=provider,
        masked=f"{_MASK_PREFIX}{row[0]}",
        created_at=row[1].isoformat(),
    )


@router.delete("/api-keys/{provider}", status_code=204, operation_id="settings_delete_api_key")
async def delete_api_key(provider: str, user: CurrentUser, db: DbDep) -> Response:
    _check_provider(provider)
    store = DbKeyStore(db)
    await store.delete(user_id=user.id, provider=provider)
    await db.commit()
    return Response(status_code=204)


@router.get("/quota", response_model=QuotaResponse, operation_id="settings_get_quota")
async def get_quota(user: CurrentUser, db: DbDep) -> QuotaResponse:
    active = await DbKeyStore(db).active_providers(user.id)
    usage = await quota_usage(db, user.id)
    period = dt.datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m")
    return QuotaResponse(
        period=period,
        byok_active=ByokActive(
            text=bool(active & _TEXT_PROVIDERS),
            image=bool(active & _IMAGE_PROVIDERS),
        ),
        usage=QuotaUsage(
            translation_papers=QuotaCounter(**usage["translation_papers"]),
            chat_messages=QuotaCounter(**usage["chat_messages"]),
            images=QuotaCounter(**usage["images"]),
            article_generations=QuotaCounter(**usage["article_generations"]),
            vocab_generations=QuotaCounter(**usage["vocab_generations"]),
        ),
    )
