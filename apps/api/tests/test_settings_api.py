"""設定 API テスト(M0-23 / plans/03 §17)。

- PY-SET-01: GET は既定値を含む完全形・PATCH は deep merge(指定キーのみ)・値域違反は 422。
- BYOK: PUT/GET/DELETE(平文再表示なし・マスク表示・不正 provider は 422)。
- quota: 5 カウンタと byok_active・当月 period。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email


def _build_app() -> FastAPI:
    """本タスク所有ルータのみをマウントしたアプリ(main.create_app と同じ共通基盤を使用)。

    並行タスクの WIP ルータに import を巻き込まれず、本タスクを独立に検証する。
    """
    from yakudoku_api.errors import register_exception_handlers
    from yakudoku_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from yakudoku_api.ratelimit import RateLimitMiddleware
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.routers import library_items, llm_settings
    from yakudoku_api.routers import settings as settings_router
    from yakudoku_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(library_items.router)
    app.include_router(settings_router.router)
    app.include_router(llm_settings.router)
    return app


@pytest_asyncio.fixture
async def auth(db_session: AsyncSession, redis_client: Any) -> AsyncIterator[AsyncClient]:
    email = f"set-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)  # rollback 後に ORM 属性へ触れないよう先に確定させる
    token = await create_session(redis_client, user.id)
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield ac
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


# ---------------------------------------------------------------------------
# PY-SET-01: GET 完全形・PATCH deep merge・値域
# ---------------------------------------------------------------------------
async def test_get_returns_full_defaults(auth: AsyncClient) -> None:
    r = await auth.get("/api/settings")
    assert r.status_code == 200
    s = r.json()
    assert s["display"]["theme"] == "system"
    assert s["display"]["accent"] == "#3E5C76"
    assert s["display"]["font_size_px"] == 16.5
    assert s["display"]["line_height"] == 2.15
    assert s["display"]["content_width_px"] == 720
    assert s["translation"]["default_style"] == "natural"
    assert s["reading"]["status_transition"] == "suggest"
    assert s["llm_routing"]["translation"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    assert s["llm_routing"]["figure_image"]["provider"] == "google"
    assert isinstance(s["available_models"], dict)


async def test_patch_deep_merge_preserves_siblings(auth: AsyncClient) -> None:
    r = await auth.patch("/api/settings", json={"display": {"theme": "dark"}})
    assert r.status_code == 200
    s = r.json()
    assert s["display"]["theme"] == "dark"
    # 兄弟キーは既定のまま残る(deep merge)。
    assert s["display"]["accent"] == "#3E5C76"
    assert s["display"]["font_size_px"] == 16.5

    # 永続化: 次の GET でも反映。
    g = await auth.get("/api/settings")
    assert g.json()["display"]["theme"] == "dark"


async def test_patch_nested_llm_routing_merge(auth: AsyncClient) -> None:
    r = await auth.patch("/api/settings", json={"llm_routing": {"chat": {"model": "gpt-5.5"}}})
    assert r.status_code == 200
    chat = r.json()["llm_routing"]["chat"]
    assert chat["model"] == "gpt-5.5"
    assert chat["provider"] == "anthropic"  # provider は既定のまま
    # 他タスクは無傷。
    assert r.json()["llm_routing"]["vocab"]["model"] == "claude-haiku-4-5"


async def test_patch_value_range_violations_are_422(auth: AsyncClient) -> None:
    for patch in (
        {"display": {"font_size_px": 13}},  # 14-20 外
        {"display": {"line_height": 3.0}},  # 1.6-2.4 外
        {"display": {"content_width_px": 705}},  # 20 刻み外
        {"display": {"theme": "pink"}},  # 列挙外
        {"display": {"accent": "#000000"}},  # 列挙外
        {"reading": {"status_transition": "always"}},  # 列挙外
        {"llm_routing": {"translation": {"provider": "unknown"}}},  # provider 列挙外
        {"unknown_section": {"x": 1}},  # 未知キー
    ):
        r = await auth.patch("/api/settings", json=patch)
        assert r.status_code == 422, patch
        assert r.json()["code"] == "validation_error"

    # 不正 PATCH の後も設定は既定のまま(部分適用されない)。
    g = await auth.get("/api/settings")
    assert g.json()["display"]["theme"] == "system"


async def test_patch_valid_stepped_values(auth: AsyncClient) -> None:
    r = await auth.patch(
        "/api/settings",
        json={"display": {"font_size_px": 18.5, "line_height": 1.75, "content_width_px": 800}},
    )
    assert r.status_code == 200
    d = r.json()["display"]
    assert d["font_size_px"] == 18.5
    assert d["line_height"] == 1.75
    assert d["content_width_px"] == 800


# ---------------------------------------------------------------------------
# BYOK(§17.3)
# ---------------------------------------------------------------------------
async def test_byok_put_get_delete_masked(auth: AsyncClient) -> None:
    put = await auth.put("/api/settings/api-keys/openai", json={"api_key": "sk-secret-1234"})
    assert put.status_code == 200
    body = put.json()
    assert body["provider"] == "openai"
    assert body["masked"].endswith("1234")
    assert "secret" not in body["masked"]  # 平文再表示なし
    assert body["masked"].startswith("sk-…")

    listing = await auth.get("/api/settings/api-keys")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["provider"] == "openai"
    assert items[0]["status"] == "untested"
    assert items[0]["masked"].endswith("1234")

    d = await auth.delete("/api/settings/api-keys/openai")
    assert d.status_code == 204
    assert (await auth.get("/api/settings/api-keys")).json()["items"] == []


async def test_byok_invalid_provider_is_422(auth: AsyncClient) -> None:
    r = await auth.put("/api/settings/api-keys/bogus", json={"api_key": "sk-x"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# quota(§17.4)
# ---------------------------------------------------------------------------
async def test_quota_shape_and_byok_active(auth: AsyncClient) -> None:
    r = await auth.get("/api/settings/quota")
    assert r.status_code == 200
    q = r.json()
    assert len(q["period"]) == 7 and q["period"][4] == "-"  # "YYYY-MM"
    assert q["byok_active"] == {"text": False, "image": False}
    for key in (
        "translation_papers",
        "chat_messages",
        "images",
        "article_generations",
        "vocab_generations",
    ):
        assert set(q["usage"][key]) == {"used", "limit"}
        assert q["usage"][key]["used"] == 0
        assert q["usage"][key]["limit"] > 0

    # BYOK(openai=text/image 両対応)を登録すると byok_active が立つ。
    await auth.put("/api/settings/api-keys/openai", json={"api_key": "sk-openai-key"})
    q2 = (await auth.get("/api/settings/quota")).json()
    assert q2["byok_active"]["text"] is True
    assert q2["byok_active"]["image"] is True
