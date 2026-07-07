"""BYOK キーストア(plans/04 §11)。

byok_api_keys(plans/02 §4.2)に Fernet 暗号化して保存する。マスタキーは環境変数
``YAKUDOKU_KEY_ENCRYPTION_SECRET``(Fernet 標準 44 文字 urlsafe base64)。カンマ区切りで
複数指定でき、``MultiFernet`` で復号(先頭キーで暗号化)= ローテーション対応(§11.2)。

- 平文キーは DB・ログ・例外メッセージに残さない。表示は ``key_hint``(末尾 4 文字)のみで、
  再表示不可・再入力のみ(§11.2)。
- キー解決順(§11.1): ユーザーキー(status != 'invalid')→ 運営キー(環境変数)→ 未設定は
  ``ProviderError(kind=AUTH)``。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_llm.errors import ErrorKind, ProviderError
from yakudoku_llm.protocols import ResolvedKey

from yakudoku_api.settings import ApiSettings, get_api_settings

# masked 表示の接頭辞(plans/04 §11.3 / plans/03 §17.3 の逐語 "sk-…" + key_hint)。
_MASK_PREFIX = "sk-…"


def _build_fernet(secret: str) -> MultiFernet:
    """カンマ区切りマスタキー → MultiFernet(先頭で暗号化・全鍵で復号)。"""
    keys = [part.strip() for part in secret.split(",") if part.strip()]
    if not keys:
        raise RuntimeError(
            "YAKUDOKU_KEY_ENCRYPTION_SECRET が未設定です(BYOK 暗号化に必須。plans/04 §11.2)"
        )
    return MultiFernet([Fernet(k.encode("ascii")) for k in keys])


def _hint(plaintext: str) -> str:
    return plaintext[-4:]


class DbKeyStore:
    """byok_api_keys を読み書きする KeyStore 実装(plans/04 §11)。"""

    def __init__(self, session: AsyncSession, settings: ApiSettings | None = None) -> None:
        self._session = session
        self._settings = settings or get_api_settings()
        self._fernet = _build_fernet(self._settings.yakudoku_key_encryption_secret)

    # -- BYOK 書き込み/表示(設定 4f「アカウント」・plans/04 §11.3) -----------------

    async def put(self, user_id: str, provider: str, plaintext: str) -> None:
        """暗号化して upsert(再入力=上書き)。status は 'untested' に戻す(§11.3 PUT)。"""
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        await self._session.execute(
            text(
                "INSERT INTO byok_api_keys "
                "(user_id, provider, encrypted_key, key_hint, status) "
                "VALUES (CAST(:user_id AS uuid), :provider, :encrypted_key, :key_hint, "
                "'untested') "
                "ON CONFLICT (user_id, provider) DO UPDATE SET "
                "encrypted_key = EXCLUDED.encrypted_key, "
                "key_hint = EXCLUDED.key_hint, "
                "status = 'untested', "
                "last_tested_at = NULL"
            ),
            {
                "user_id": user_id,
                "provider": provider,
                "encrypted_key": token,
                "key_hint": _hint(plaintext),
            },
        )

    async def get(self, user_id: str, provider: str) -> str | None:
        """復号して平文を返す(内部用。API では再表示しない)。未登録は None。"""
        row = (
            await self._session.execute(
                text(
                    "SELECT encrypted_key FROM byok_api_keys "
                    "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider"
                ),
                {"user_id": user_id, "provider": provider},
            )
        ).first()
        if row is None:
            return None
        token = bytes(row[0])
        return self._fernet.decrypt(token).decode("utf-8")

    async def mask(self, user_id: str, provider: str) -> str | None:
        """ "sk-…"+末尾4文字のマスク表示(平文は返さない。§11.3)。未登録は None。"""
        row = (
            await self._session.execute(
                text(
                    "SELECT key_hint FROM byok_api_keys "
                    "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider"
                ),
                {"user_id": user_id, "provider": provider},
            )
        ).first()
        if row is None:
            return None
        return f"{_MASK_PREFIX}{row[0]}"

    async def delete(self, user_id: str, provider: str) -> None:
        """キー削除(以後は運営キー+クォータ消費に戻る。§11.3 DELETE)。"""
        await self._session.execute(
            text(
                "DELETE FROM byok_api_keys "
                "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider"
            ),
            {"user_id": user_id, "provider": provider},
        )

    async def active_providers(self, user_id: str | None) -> set[str]:
        """有効(status != 'invalid')な BYOK を持つ provider 集合。クォータ判定に使う。"""
        if not user_id:
            return set()
        rows = (
            await self._session.execute(
                text(
                    "SELECT provider FROM byok_api_keys "
                    "WHERE user_id = CAST(:user_id AS uuid) AND status <> 'invalid'"
                ),
                {"user_id": user_id},
            )
        ).scalars()
        return set(rows.all())

    # -- KeyStore プロトコル(実行時のキー解決・§11.1 / §11.4) --------------------

    async def resolve(self, user_id: str | None, provider: str) -> ResolvedKey:
        """§11.1 のキー解決順。どちらも無ければ ProviderError(kind=AUTH)。"""
        resolved = await self.resolve_or_none(user_id, provider)
        if resolved is None:
            raise ProviderError(
                ErrorKind.AUTH, provider, "-", f"no api key configured for provider={provider}"
            )
        return resolved

    async def resolve_or_none(self, user_id: str | None, provider: str) -> ResolvedKey | None:
        """§11.1。ユーザーキー(有効)→ 運営キー → どちらも無ければ None(チェーン除外用)。"""
        if user_id:
            row = (
                await self._session.execute(
                    text(
                        "SELECT encrypted_key FROM byok_api_keys "
                        "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider "
                        "AND status <> 'invalid'"
                    ),
                    {"user_id": user_id, "provider": provider},
                )
            ).first()
            if row is not None:
                api_key = self._fernet.decrypt(bytes(row[0])).decode("utf-8")
                return ResolvedKey(provider=provider, api_key=api_key, source="user")
        operator = self._settings.operator_api_keys.get(provider)
        if operator:
            return ResolvedKey(provider=provider, api_key=operator, source="operator")
        return None

    async def mark_invalid(self, user_id: str, provider: str) -> None:
        """実行中のユーザーキー失効(§11.4)。設定画面のキー行に「無効」を出すため status を更新。"""
        await self._session.execute(
            text(
                "UPDATE byok_api_keys SET status = 'invalid' "
                "WHERE user_id = CAST(:user_id AS uuid) AND provider = :provider"
            ),
            {"user_id": user_id, "provider": provider},
        )


__all__ = ["DbKeyStore"]
