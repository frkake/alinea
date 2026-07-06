"""OAuth 2.0(Google / GitHub、Authorization Code)。plans/01 §6.1・plans/03 §2.1-2.2。

authlib は型スタブ(py.typed)を持たず mypy strict を通せないため、httpx で手実装する。
httpx は企業プロキシを避けるため trust_env=False で呼ぶ。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from yakudoku_api.settings import ApiSettings

SUPPORTED_PROVIDERS = ("google", "github")


@dataclass(slots=True)
class OAuthProvider:
    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    client_id: str
    client_secret: str


@dataclass(slots=True)
class OAuthProfile:
    subject: str
    email: str
    display_name: str
    avatar_url: str | None


def get_provider(settings: ApiSettings, name: str) -> OAuthProvider | None:
    if name == "google":
        provider = OAuthProvider(
            name="google",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",  # noqa: S106 — URL, not a secret
            userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            scope="openid email profile",
            client_id=settings.oauth_google_client_id,
            client_secret=settings.oauth_google_client_secret,
        )
    elif name == "github":
        provider = OAuthProvider(
            name="github",
            authorize_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",  # noqa: S106 — URL, not a secret
            userinfo_url="https://api.github.com/user",
            scope="read:user user:email",
            client_id=settings.oauth_github_client_id,
            client_secret=settings.oauth_github_client_secret,
        )
    else:
        return None
    if not provider.client_id or not provider.client_secret:
        return None
    return provider


def redirect_uri(settings: ApiSettings, name: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/api/auth/oauth/{name}/callback"


def build_authorize_url(provider: OAuthProvider, redirect: str, state: str) -> str:
    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
    }
    return f"{provider.authorize_url}?{urlencode(params)}"


async def exchange_and_fetch_profile(
    provider: OAuthProvider, code: str, redirect: str
) -> OAuthProfile:
    """認可コードをトークンに交換し、プロフィールを取得する。失敗時は例外。"""
    async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
        token_resp = await client.post(
            provider.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "redirect_uri": redirect,
            },
            headers={"Accept": "application/json"},
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        profile_resp = await client.get(provider.userinfo_url, headers=headers)
        profile_resp.raise_for_status()
        data = profile_resp.json()

        if provider.name == "google":
            return OAuthProfile(
                subject=str(data["sub"]),
                email=str(data["email"]),
                display_name=str(data.get("name") or data["email"].split("@", 1)[0]),
                avatar_url=data.get("picture"),
            )
        # github: email は別エンドポイント(非公開設定の場合)
        email = data.get("email")
        if not email:
            emails_resp = await client.get("https://api.github.com/user/emails", headers=headers)
            emails_resp.raise_for_status()
            primary = next(
                (e for e in emails_resp.json() if e.get("primary")),
                None,
            )
            email = primary["email"] if primary else None
        if not email:
            raise ValueError("github account has no accessible email")
        return OAuthProfile(
            subject=str(data["id"]),
            email=str(email),
            display_name=str(data.get("name") or data.get("login") or email.split("@", 1)[0]),
            avatar_url=data.get("avatar_url"),
        )
