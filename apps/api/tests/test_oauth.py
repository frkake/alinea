"""OAuth サービス単体テスト(plans/01 §6.1・plans/03 §2.1-2.2)。

- ``get_provider``: 資格情報未設定/未対応プロバイダは None(auth.py の oauth_unavailable 分岐)。
- ``build_authorize_url``/``redirect_uri``: URL 組み立て。
- ``exchange_and_fetch_profile``: google/github の双方でトークン交換→プロフィール取得。
  github は公開メール優先、非公開時は ``/user/emails`` から primary を取る。実 HTTP は
  発行せず ``httpx.AsyncClient`` をスタブに差し替える(外部プロバイダへ一切接続しない)。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from alinea_api.services.oauth import (
    OAuthProvider,
    build_authorize_url,
    exchange_and_fetch_profile,
    get_provider,
    redirect_uri,
)
from alinea_api.settings import get_api_settings


def _google_provider() -> OAuthProvider:
    return OAuthProvider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
        client_id="gid",
        client_secret="gsecret",
    )


def _github_provider() -> OAuthProvider:
    return OAuthProvider(
        name="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        scope="read:user user:email",
        client_id="hid",
        client_secret="hsecret",
    )


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    """oauth.exchange_and_fetch_profile 用のスタブ(実 HTTP を一切発行しない)。"""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._responses[url])

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._responses[url])


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: dict[str, Any]) -> None:
    # oauth.py は `import httpx; httpx.AsyncClient(...)` で呼ぶため、httpx モジュール
    # (単一インスタンス)の AsyncClient を差し替えれば oauth.py 側からもスタブが見える。
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))


# ---------------------------------------------------------------------------
# get_provider / redirect_uri / build_authorize_url
# ---------------------------------------------------------------------------
def test_get_provider_returns_none_when_credentials_missing() -> None:
    settings = get_api_settings().model_copy(
        update={"oauth_google_client_id": "", "oauth_google_client_secret": ""}
    )
    assert get_provider(settings, "google") is None


def test_get_provider_returns_none_for_unsupported_name() -> None:
    settings = get_api_settings()
    assert get_provider(settings, "twitter") is None


def test_get_provider_builds_google_and_github_when_configured() -> None:
    settings = get_api_settings().model_copy(
        update={
            "oauth_google_client_id": "gid",
            "oauth_google_client_secret": "gsecret",
            "oauth_github_client_id": "hid",
            "oauth_github_client_secret": "hsecret",
        }
    )
    google = get_provider(settings, "google")
    assert google is not None
    assert google.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert google.client_id == "gid"

    github = get_provider(settings, "github")
    assert github is not None
    assert github.userinfo_url == "https://api.github.com/user"
    assert github.client_secret == "hsecret"


def test_redirect_uri_strips_trailing_slash_and_appends_callback_path() -> None:
    settings = get_api_settings().model_copy(update={"app_base_url": "http://localhost:3000/"})
    assert (
        redirect_uri(settings, "google") == "http://localhost:3000/api/auth/oauth/google/callback"
    )


def test_build_authorize_url_encodes_all_params() -> None:
    provider = _google_provider()
    url = build_authorize_url(
        provider, "http://localhost:3000/api/auth/oauth/google/callback", "state123"
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=gid" in url
    assert "state=state123" in url
    assert "response_type=code" in url
    assert "scope=openid+email+profile" in url


# ---------------------------------------------------------------------------
# exchange_and_fetch_profile — google
# ---------------------------------------------------------------------------
async def test_exchange_and_fetch_profile_google_success(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _google_provider()
    _patch_client(
        monkeypatch,
        {
            provider.token_url: {"access_token": "tok"},
            provider.userinfo_url: {
                "sub": "123",
                "email": "alice@example.com",
                "name": "Alice",
                "picture": "http://pic.example/a.png",
            },
        },
    )
    profile = await exchange_and_fetch_profile(provider, "code", "http://localhost:3000/cb")
    assert profile.subject == "123"
    assert profile.email == "alice@example.com"
    assert profile.display_name == "Alice"
    assert profile.avatar_url == "http://pic.example/a.png"


async def test_exchange_and_fetch_profile_google_defaults_name_from_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _google_provider()
    _patch_client(
        monkeypatch,
        {
            provider.token_url: {"access_token": "tok"},
            provider.userinfo_url: {"sub": "123", "email": "bob@example.com"},
        },
    )
    profile = await exchange_and_fetch_profile(provider, "code", "http://localhost:3000/cb")
    assert profile.display_name == "bob"
    assert profile.avatar_url is None


# ---------------------------------------------------------------------------
# exchange_and_fetch_profile — github
# ---------------------------------------------------------------------------
async def test_exchange_and_fetch_profile_github_uses_public_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _github_provider()
    _patch_client(
        monkeypatch,
        {
            provider.token_url: {"access_token": "tok"},
            provider.userinfo_url: {
                "id": 42,
                "email": "pub@example.com",
                "login": "octocat",
                "avatar_url": "http://a.example/octocat.png",
            },
        },
    )
    profile = await exchange_and_fetch_profile(provider, "code", "http://localhost:3000/cb")
    assert profile.subject == "42"
    assert profile.email == "pub@example.com"
    assert profile.display_name == "octocat"
    assert profile.avatar_url == "http://a.example/octocat.png"


async def test_exchange_and_fetch_profile_github_falls_back_to_emails_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _github_provider()
    _patch_client(
        monkeypatch,
        {
            provider.token_url: {"access_token": "tok"},
            provider.userinfo_url: {"id": 42, "login": "octocat"},
            "https://api.github.com/user/emails": [
                {"email": "old@example.com", "primary": False},
                {"email": "primary@example.com", "primary": True},
            ],
        },
    )
    profile = await exchange_and_fetch_profile(provider, "code", "http://localhost:3000/cb")
    assert profile.email == "primary@example.com"
    assert profile.display_name == "octocat"


async def test_exchange_and_fetch_profile_github_raises_without_any_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _github_provider()
    _patch_client(
        monkeypatch,
        {
            provider.token_url: {"access_token": "tok"},
            provider.userinfo_url: {"id": 42, "login": "octocat"},
            "https://api.github.com/user/emails": [{"email": "old@example.com", "primary": False}],
        },
    )
    with pytest.raises(ValueError, match="no accessible email"):
        await exchange_and_fetch_profile(provider, "code", "http://localhost:3000/cb")
