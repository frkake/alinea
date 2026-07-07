"""API 固有設定(pydantic-settings)。

共有設定(DB / Redis / S3 / セッション秘密鍵など)は `yakudoku_core.settings.CoreSettings`
を継承して受け取り、API だけが必要とする SMTP・OAuth の値をここに追加する。os.environ の
直接参照はここ(と py-core の settings)に集約する(plans/00 §5・plans/01 §8.4)。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from yakudoku_core.settings import CoreSettings


class ApiSettings(CoreSettings):
    """FastAPI プロセスが読む型付き設定。CoreSettings の全項目を含む。"""

    # メール送信(dev は Mailpit: SMTP localhost:1025)
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "login@yakudoku.app"

    # OAuth クライアント資格情報(未設定時は該当プロバイダを無効扱いにする)
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""

    # LLM 運営キー(plans/04 §11.1-2・§16。未設定プロバイダはチェーンから除外)。
    # google は plans/04 §16(GEMINI_API_KEY)と plans/01 §8.4 / .env.example
    # (GOOGLE_API_KEY)が食い違うため両方受理する(GEMINI が優先)。
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY", "gemini_api_key"),
    )
    deepseek_api_key: str = ""
    xai_api_key: str = ""

    # ルート解決の Redis キャッシュ TTL(秒。plans/04 §15・§16。既定 60)。
    yakudoku_llm_route_cache_ttl_s: int = 60

    @property
    def operator_api_keys(self) -> dict[str, str]:
        """provider 名 → 設定済み運営キー(空文字は除外。plans/04 §11.1-2)。"""
        raw = {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "google": self.gemini_api_key,
            "deepseek": self.deepseek_api_key,
            "xai": self.xai_api_key,
        }
        return {provider: key for provider, key in raw.items() if key}

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def allowed_origins(self) -> list[str]:
        """CSRF Origin 検証で許可するオリジンのリスト(plans/03 §1.3・plans/01 §6.2)。

        - 常に app_base_url(dev: http://localhost:3000 / prod: https://yakudoku.app)。
        - EXTENSION_ALLOWED_ORIGINS(カンマ区切り)を追加。コメント/空要素は無視する。
        """
        origins = [self.app_base_url.rstrip("/")]
        raw = self.extension_allowed_origins or ""
        for part in raw.split(","):
            candidate = part.strip()
            if candidate and not candidate.startswith("#"):
                origins.append(candidate.rstrip("/"))
        return origins


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
