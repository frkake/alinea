"""auth — 認証(plans/03 §2)。メールリンク・OAuth・セッション・拡張トークン・アカウント削除。"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Query, Response
from fastapi.responses import RedirectResponse
from yakudoku_core.jobs.store import JobStore

from yakudoku_api.deps import (
    CurrentUserOrExt,
    DbDep,
    RedisDep,
    SessionContext,
    SettingsDep,
)
from yakudoku_api.errors import ProblemException
from yakudoku_api.ids import new_token
from yakudoku_api.schemas.auth import (
    AccountDeleteBody,
    EmailRequestBody,
    EmailRequestResponse,
    ExtensionTokenResponse,
    MeResponse,
    MeUser,
)
from yakudoku_api.services import session_service, user_service
from yakudoku_api.services.email import send_login_link
from yakudoku_api.services.oauth import (
    SUPPORTED_PROVIDERS,
    build_authorize_url,
    exchange_and_fetch_profile,
    get_provider,
    redirect_uri,
)
from yakudoku_api.settings import ApiSettings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _safe_next(value: str | None) -> str:
    """オープンリダイレクト防止。`/` 始まり(`//` 除く)のみ許可、既定は /dashboard(M1-10)。"""
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/dashboard"


def _set_session_cookie(response: Response, token: str, settings: ApiSettings) -> None:
    response.set_cookie(
        key=session_service.COOKIE_NAME,
        value=token,
        max_age=session_service.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


def _clear_session_cookie(response: Response, settings: ApiSettings) -> None:
    response.delete_cookie(
        key=session_service.COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )


@router.get("/oauth/{provider}/start", operation_id="auth_oauth_start")
async def oauth_start(
    provider: str,
    r: RedisDep,
    settings: SettingsDep,
    next: str = Query(default="/dashboard"),
) -> RedirectResponse:
    if provider not in SUPPORTED_PROVIDERS:
        raise ProblemException("validation_error", detail="未対応のプロバイダです")
    login_url = f"{settings.app_base_url.rstrip('/')}/login"
    prov = get_provider(settings, provider)
    if prov is None:
        return RedirectResponse(f"{login_url}?error=oauth_unavailable", status_code=302)
    state = new_token(16)
    await session_service.store_oauth_state(
        r, state, {"next": _safe_next(next), "provider": provider}
    )
    url = build_authorize_url(prov, redirect_uri(settings, provider), state)
    return RedirectResponse(url, status_code=302)


@router.get("/oauth/{provider}/callback", operation_id="auth_oauth_callback")
async def oauth_callback(
    provider: str,
    r: RedisDep,
    db: DbDep,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    login_url = f"{settings.app_base_url.rstrip('/')}/login"
    fail = RedirectResponse(f"{login_url}?error=oauth_failed", status_code=302)
    if provider not in SUPPORTED_PROVIDERS or not code or not state:
        return fail
    stored = await session_service.consume_oauth_state(r, state)
    prov = get_provider(settings, provider)
    if stored is None or prov is None:
        return fail
    try:
        profile = await exchange_and_fetch_profile(prov, code, redirect_uri(settings, provider))
    except (httpx.HTTPError, KeyError, ValueError):
        return fail
    user = await user_service.upsert_user_by_email(
        db,
        profile.email,
        provider=provider,
        provider_subject=profile.subject,
        display_name=profile.display_name,
        avatar_url=profile.avatar_url,
    )
    session_token = await session_service.create_session(r, user.id)
    next_path = _safe_next(stored.get("next"))
    response = RedirectResponse(f"{settings.app_base_url.rstrip('/')}{next_path}", status_code=302)
    _set_session_cookie(response, session_token, settings)
    return response


@router.post(
    "/email/request",
    status_code=202,
    response_model=EmailRequestResponse,
    operation_id="auth_request_email_link",
)
async def request_email_link(
    body: EmailRequestBody, r: RedisDep, settings: SettingsDep
) -> EmailRequestResponse:
    # アカウント有無に関わらず同一応答(列挙攻撃対策)。
    next_path = _safe_next(body.next)
    token = await session_service.create_email_link_token(r, body.email, next_path)
    link = f"{settings.app_base_url.rstrip('/')}/api/auth/email/verify?token={token}"
    await send_login_link(settings, to=body.email, link=link)
    return EmailRequestResponse(sent=True)


@router.get("/email/verify", operation_id="auth_verify_email_link")
async def verify_email_link(
    token: str, r: RedisDep, db: DbDep, settings: SettingsDep
) -> RedirectResponse:
    login_url = f"{settings.app_base_url.rstrip('/')}/login"
    payload = await session_service.consume_email_link_token(r, token)
    if payload is None:
        return RedirectResponse(f"{login_url}?error=link_expired", status_code=302)
    user = await user_service.upsert_user_by_email(db, payload["email"], provider="email")
    session_token = await session_service.create_session(r, user.id)
    next_path = _safe_next(payload.get("next"))
    response = RedirectResponse(f"{settings.app_base_url.rstrip('/')}{next_path}", status_code=302)
    _set_session_cookie(response, session_token, settings)
    return response


@router.post("/logout", status_code=204, operation_id="auth_logout")
async def logout(
    ctx: SessionContext, r: RedisDep, settings: SettingsDep, response: Response
) -> Response:
    if ctx.session_token:
        await session_service.destroy_session(r, ctx.session_token)
    result = Response(status_code=204)
    _clear_session_cookie(result, settings)
    return result


@router.get("/me", response_model=MeResponse, operation_id="auth_me")
async def me(user: CurrentUserOrExt, db: DbDep) -> MeResponse:
    providers = await user_service.list_providers(db, user.id)
    unread = await user_service.count_unread_notifications(db, user.id)
    return MeResponse(
        user=MeUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            providers=providers,
            created_at=user.created_at.isoformat(),
        ),
        unread_notifications=unread,
    )


@router.post(
    "/extension-token",
    status_code=201,
    response_model=ExtensionTokenResponse,
    operation_id="auth_create_extension_token",
)
async def create_extension_token(ctx: SessionContext, r: RedisDep) -> ExtensionTokenResponse:
    token, expires_at = await session_service.create_extension_token(r, ctx.user.id)
    return ExtensionTokenResponse(token=token, expires_at=expires_at.isoformat())


@router.delete("/account", status_code=202, operation_id="auth_delete_account")
async def delete_account(
    body: AccountDeleteBody,
    ctx: SessionContext,
    db: DbDep,
    r: RedisDep,
    settings: SettingsDep,
    response: Response,
) -> dict[str, str]:
    if body.confirm != "delete":
        raise ProblemException("validation_error", detail="confirm は 'delete' を指定してください")
    store = JobStore(db)
    job_id = await store.enqueue(
        kind="account_delete",
        user_id=ctx.user.id,
        idempotency_key=f"account_delete:{ctx.user.id}",
        payload={"user_id": ctx.user.id},
    )
    # 全セッション即時失効(データ本体の削除は account_delete ジョブが実行する)。
    await session_service.destroy_all_sessions(r, ctx.user.id)
    _clear_session_cookie(response, settings)
    return {"job_id": job_id}
