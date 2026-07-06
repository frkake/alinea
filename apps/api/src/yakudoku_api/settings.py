"""API 固有設定(pydantic-settings)。

共有設定(DB / Redis / S3 / セッション秘密鍵など)は `yakudoku_core.settings.CoreSettings`
を継承して受け取り、API だけが必要とする SMTP・OAuth の値をここに追加する。os.environ の
直接参照はここ(と py-core の settings)に集約する(plans/00 §5・plans/01 §8.4)。
"""

from __future__ import annotations

from functools import lru_cache

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
