"""ジョブ単位のユーザー別 LLM ルーターファクトリ(Task 13)。

同一 Worker プロセスで複数ユーザーのジョブが動くため、``ctx["router"]``(startup 時に 1 度だけ
構築する全ジョブ共通の運営キールータ)では per-user の BYOK / モデル上書きを解決できず、
ユーザー間でルート解決が混ざる恐れがある。:class:`UserRouterFactory` はジョブごとに

1. 短命なセッションを開き、
2. 共有層 :func:`alinea_core.llm.build_user_router` でそのユーザーのキー解決済み
   ``LLMRouter`` を構築し、
3. セッションを閉じて返す(**秘密鍵もルータ自体もジョブ終了後に保持しない**)。

60 秒キャッシュ(Redis)は route chain metadata(= (model_id, provider) の順序列)だけを保持し、
BYOK 平文キーは決してキャッシュ・ログ・ジョブペイロードに載せない。ルート解決の
DB/暗号化ロジックは apps 間 import を避けるため共有層に置く(worker から apps/api を import
しない)。
"""

from __future__ import annotations

import redis.asyncio as redis
from alinea_core.llm import LLMRuntimeConfig, ProviderFactory, build_user_router
from alinea_llm.router import LLMRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class UserRouterFactory:
    """ジョブ単位でユーザー別 ``LLMRouter`` を作るファクトリ。

    ``sessionmaker`` は worker 共通のもの。``redis`` は route chain metadata の 60 秒キャッシュ用
    (``None`` なら常に DB 直参照)。``config`` は運営キー・キー暗号化秘密・キャッシュ TTL の束。
    ``provider_factory`` は provider 実アダプタ構築関数(テストは Fake を注入する)。

    ファクトリ自身は秘密鍵もルータも保持しない。保持するのは不変の依存(sessionmaker /
    redis / config)だけ。
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: redis.Redis | None,
        config: LLMRuntimeConfig,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.redis = redis
        self.runtime_config = config
        self._provider_factory = provider_factory

    async def for_job(self, *, user_id: str, task: str) -> LLMRouter:
        """ユーザー ``user_id`` の ``task`` 用ルータをジョブ単位で構築して返す。

        セッションはこの呼び出しの間だけ開く。返り値の ``LLMRouter`` は構築時に解決済みの
        provider インスタンスを保持するのみで、セッションや BYOK 平文キーへの参照は残さない
        (worker 側の usage 計測は followup のため ``attach_meter=False``)。
        """
        async with self.sessionmaker() as session:
            return await build_user_router(
                session=session,
                cache=self.redis,
                config=self.runtime_config,
                user_id=str(user_id),
                task=task,
                provider_factory=self._provider_factory,
                attach_meter=False,
            )

    async def invalidate(self, *, task: str, user_id: str | None = None) -> None:
        """route chain metadata のキャッシュを破棄する(設定変更後などに次ジョブへ反映)。"""
        if self.redis is None:
            return
        from alinea_core.llm import LLMRouteStore

        async with self.sessionmaker() as session:
            store = LLMRouteStore(
                session, self.redis, cache_ttl_s=self.runtime_config.route_cache_ttl_s
            )
            await store.invalidate(task, user_id)


__all__ = ["UserRouterFactory"]
