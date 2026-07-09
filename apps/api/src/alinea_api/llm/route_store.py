"""DB ベースのタスクルート解決(plans/04 §15)。

YAML(models.yaml / routing.yaml)はシード、**DB が実行時の正**とする。
``llm_task_routes`` の既定チェーンに ``user_task_model_overrides`` を先頭挿入し、
``llm_models.enabled=false`` と(呼び出し側が渡した)利用不可プロバイダのモデルを除外する。
結果は Redis に 60 秒キャッシュする(キー ``llm:route:{task}`` / ``llm:route:{task}:{user_id}``)。
"""

from __future__ import annotations

import json

import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# (model_id, provider) の順序付きチェーン。
ChainEntry = tuple[str, str]


class DbRouteStore:
    """llm_models / llm_task_routes / user_task_model_overrides を読むルート解決器。"""

    def __init__(
        self,
        session: AsyncSession,
        cache: redis.Redis | None = None,
        *,
        cache_ttl_s: int = 60,
    ) -> None:
        self._session = session
        self._cache = cache
        self._cache_ttl_s = cache_ttl_s

    def _cache_key(self, task: str, user_id: str | None) -> str:
        return f"llm:route:{task}:{user_id}" if user_id else f"llm:route:{task}"

    async def _base_chain(self, task: str, user_id: str | None) -> list[ChainEntry]:
        """ユーザー上書き適用 + enabled 絞り込み済みの (model, provider) チェーン。

        利用可能プロバイダの絞り込みは呼び出し側(resolve_chain)で行う(BYOK 有無に依存し
        キャッシュを汚さないため)。この段までを Redis に 60 秒キャッシュする。
        """
        cached = await self._cache_get(task, user_id)
        if cached is not None:
            return cached

        route = (
            await self._session.execute(
                text("SELECT chain FROM llm_task_routes WHERE task = :task"),
                {"task": task},
            )
        ).first()
        if route is None:
            return []
        chain: list[str] = list(route[0])

        if user_id:
            override = (
                await self._session.execute(
                    text(
                        "SELECT model_id FROM user_task_model_overrides "
                        "WHERE user_id = CAST(:user_id AS uuid) AND task = :task"
                    ),
                    {"user_id": user_id, "task": task},
                )
            ).scalar_one_or_none()
            if override:
                # §15: ユーザー選択モデルを先頭へ(既定チェーンにあれば移動)。
                chain = [override, *(m for m in chain if m != override)]

        # enabled=true のモデルのみ残し、provider を引く(不明 ID は除外)。
        rows = (
            await self._session.execute(
                text("SELECT id, provider FROM llm_models WHERE enabled = true")
            )
        ).fetchall()
        provider_of = {row[0]: row[1] for row in rows}
        entries: list[ChainEntry] = [(m, provider_of[m]) for m in chain if m in provider_of]

        await self._cache_set(task, user_id, entries)
        return entries

    async def _cache_get(self, task: str, user_id: str | None) -> list[ChainEntry] | None:
        if self._cache is None:
            return None
        raw = await self._cache.get(self._cache_key(task, user_id))
        if raw is None:
            return None
        data: list[list[str]] = json.loads(raw)
        return [(m, p) for m, p in data]

    async def _cache_set(self, task: str, user_id: str | None, entries: list[ChainEntry]) -> None:
        if self._cache is None:
            return
        payload = json.dumps([[m, p] for m, p in entries])
        await self._cache.set(self._cache_key(task, user_id), payload, ex=self._cache_ttl_s)

    async def resolve_chain(
        self,
        task: str,
        user_id: str | None = None,
        *,
        available_providers: set[str] | None = None,
    ) -> list[ChainEntry]:
        """解決済みの (model_id, provider) チェーン。

        available_providers を与えると、そのプロバイダのモデルだけに絞る(§15 の
        「運営キー未設定プロバイダのモデルを除外」= 呼び出し側が operator と BYOK を渡す)。
        """
        entries = await self._base_chain(task, user_id)
        if available_providers is None:
            return entries
        return [(m, p) for m, p in entries if p in available_providers]

    async def chain_for(
        self,
        task: str,
        user_id: str | None = None,
        *,
        available_providers: set[str] | None = None,
    ) -> list[str]:
        """モデル ID のみのチェーン(plans/04 §9.2 の RouteResolver.chain_for 相当)。"""
        entries = await self.resolve_chain(task, user_id, available_providers=available_providers)
        return [m for m, _ in entries]

    async def primary_provider(self, task: str, user_id: str | None = None) -> str | None:
        """チェーン先頭モデルの provider(クォータの BYOK スキップ判定に使う・plans/07 §9.2)。"""
        entries = await self._base_chain(task, user_id)
        return entries[0][1] if entries else None

    async def model_provider(self, model_id: str) -> str | None:
        """モデル ID → provider(llm_models 参照)。不明なら None。"""
        return (
            await self._session.execute(
                text("SELECT provider FROM llm_models WHERE id = :id"),
                {"id": model_id},
            )
        ).scalar_one_or_none()

    async def invalidate(self, task: str, user_id: str | None = None) -> None:
        """設定変更後などにキャッシュを破棄する。"""
        if self._cache is not None:
            await self._cache.delete(self._cache_key(task, user_id))


__all__ = ["ChainEntry", "DbRouteStore"]
