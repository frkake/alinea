"""共有設定(pydantic-settings)。os.environ の直接参照はここに集約する(plans/00 §5)。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str:
    """cwd から上方向に .env を探す(apps/api 等のサブディレクトリ実行でも同じ .env を読む)。

    見つからなければ従来どおり cwd 相対 ".env"(= 実質未読)。本番は環境変数で渡すため
    .env が無くても動く。
    """
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return str(candidate)
    return ".env"


class CoreSettings(BaseSettings):
    """api / worker が共用する型付き設定。秘匿値のみを環境変数から読む。"""

    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_base_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"
    api_internal_url: str = "http://localhost:8000"

    # DB / Redis / S3
    database_url: str = "postgresql+asyncpg://yakudoku:yakudoku@localhost:5432/yakudoku"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = "yakudoku"
    s3_secret_access_key: str = "yakudoku-dev-secret"  # noqa: S105
    s3_bucket_sources: str = "yakudoku-sources"
    s3_bucket_assets: str = "yakudoku-assets"

    # 認証・暗号化(既定値は開発用プレースホルダ。本番は .env で必ず上書きする)
    session_secret: str = "change-me-64-hex"  # noqa: S105
    yakudoku_key_encryption_secret: str = ""
    extension_allowed_origins: str = ""

    # 外部
    arxiv_user_agent: str = "yakudoku/1.0 (contact: admin@yakudoku.app)"

    # LLM 運営キー(plans/04 §11.1-2・§16。未設定プロバイダはチェーンから除外)。
    # api と worker の両方が使うため CoreSettings に置く(worker は os.environ だけでなく
    # .env からも読めることが dev 起動の前提 — pnpm dev はシェルへ export しない)。
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

    # LLM ベース URL 上書き(E2E/CI モック差し替え)
    yakudoku_openai_base_url: str = ""
    yakudoku_anthropic_base_url: str = ""
    yakudoku_google_base_url: str = ""
    yakudoku_deepseek_base_url: str = ""
    yakudoku_xai_base_url: str = ""
    yakudoku_arxiv_base_url: str = ""

    @property
    def sync_database_url(self) -> str:
        """Alembic 等の同期ドライバ向け URL(+asyncpg を外す)。"""
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> CoreSettings:
    return CoreSettings()
