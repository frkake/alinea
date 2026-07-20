"""共有 LLM ランタイム層(plans/04 §9・§10・§11・§15、plans/07 §9)。

apps 間 import を避けるため、DB ルート解決・BYOK キー解決・使用量計測という
「DB と暗号化に依存する実装」をここ(py-core)へ集約する。apps/api と apps/worker の
両方がここを import して使う:

- :class:`LLMRuntimeConfig`: 運営キー・キー暗号化秘密・ルートキャッシュ TTL の束(設定型に
  非依存)。apps/api は ``ApiSettings`` から、apps/worker は ``CoreSettings`` + env から詰める。
- :class:`LLMRouteStore`: ``llm_task_routes`` / ``llm_models`` / ``user_task_model_overrides`` を
  読み、ユーザー上書きを先頭挿入し enabled 絞り込みした (model_id, provider) チェーンを返す。
  結果(= route chain metadata のみ。**秘密鍵は含めない**)を Redis に TTL 秒キャッシュする。
- :class:`LLMKeyStore`: BYOK を Fernet 暗号化して ``byok_api_keys`` に読み書きし、
  実行時のキー解決(ユーザー → 運営 → None)を行う。平文キーは DB・ログ・例外に残さない。
- :class:`LLMMeterHook`: ``usage_records`` に 1 試行 1 行 INSERT する MeterHook 実装。
- :func:`build_user_router`: 上記を組み合わせ、ユーザー文脈のキー解決済み ``LLMRouter`` を返す。

以前は apps/api の ``alinea_api.llm.{route_store,key_store,meter,deps}`` にあったものを移設した。
apps/api 側の同名モジュールは互換 re-export に縮小し、既存 import(DbRouteStore/DbKeyStore/
DbMeterHook)を壊さない。
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import alinea_llm
import redis.asyncio as redis
from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.protocols import LLMProvider, ResolvedKey, UsageDraft
from alinea_llm.providers import build_provider
from alinea_llm.registry import ModelRegistry
from alinea_llm.router import LLMRouter
from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# provider 名 + api_key → LLMProvider(既定は実アダプタ。テストは Fake を注入する)。
ProviderFactory = Callable[[str, str], LLMProvider]

# (model_id, provider) の順序付きチェーン。route chain metadata の唯一の表現(秘密鍵を含まない)。
ChainEntry = tuple[str, str]

# masked 表示の接頭辞(plans/04 §11.3 / plans/03 §17.3 の逐語 "sk-…" + key_hint)。
_MASK_PREFIX = "sk-…"


@dataclass(frozen=True)
class LLMRuntimeConfig:
    """LLM ランタイムが必要とする設定の束(pydantic-settings 型に非依存)。

    apps/api は ``ApiSettings`` から、apps/worker は ``CoreSettings`` + 環境変数から詰める。
    ``operator_api_keys`` は provider 名 → 運営キー(空文字は含めない)。
    """

    operator_api_keys: Mapping[str, str] = field(default_factory=dict)
    key_encryption_secret: str = ""
    route_cache_ttl_s: int = 60


# --------------------------------------------------------------------------- #
# 価格計算レジストリ(packages/llm/models.yaml シード。apps 非依存)。
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def default_registry() -> ModelRegistry | None:
    """価格計算用の ModelRegistry(packages/llm/models.yaml シード)。失敗時は None。"""
    path = Path(alinea_llm.__file__).resolve().parents[2] / "models.yaml"
    try:
        return ModelRegistry.from_yaml(path)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# ルート解決(旧 apps/api DbRouteStore)
# --------------------------------------------------------------------------- #
def route_cache_key(task: str, user_id: str | None) -> str:
    """ルート chain metadata の Redis キャッシュキー(唯一の生成元)。"""
    return f"llm:route:{task}:{user_id}" if user_id else f"llm:route:{task}"


class LLMRouteStore:
    """llm_models / llm_task_routes / user_task_model_overrides を読むルート解決器。

    YAML(models.yaml / routing.yaml)はシード、**DB が実行時の正**とする。
    ``llm_task_routes`` の既定チェーンに ``user_task_model_overrides`` を先頭挿入し、
    ``llm_models.enabled=false`` と(呼び出し側が渡した)利用不可プロバイダのモデルを除外する。
    結果(route chain metadata のみ)を Redis に TTL 秒キャッシュする。
    """

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
        return route_cache_key(task, user_id)

    async def _base_chain(self, task: str, user_id: str | None) -> list[ChainEntry]:
        """ユーザー上書き適用 + enabled 絞り込み済みの (model, provider) チェーン。

        利用可能プロバイダの絞り込みは呼び出し側(resolve_chain)で行う(BYOK 有無に依存し
        キャッシュを汚さないため)。この段までを Redis に TTL 秒キャッシュする。
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


# --------------------------------------------------------------------------- #
# BYOK キー解決(旧 apps/api DbKeyStore)
# --------------------------------------------------------------------------- #
def _build_fernet(secret: str) -> MultiFernet:
    """カンマ区切りマスタキー → MultiFernet(先頭で暗号化・全鍵で復号)。"""
    keys = [part.strip() for part in secret.split(",") if part.strip()]
    if not keys:
        raise RuntimeError(
            "ALINEA_KEY_ENCRYPTION_SECRET が未設定です(BYOK 暗号化に必須。plans/04 §11.2)"
        )
    try:
        return MultiFernet([Fernet(k.encode("ascii")) for k in keys])
    except (UnicodeEncodeError, ValueError) as exc:
        raise RuntimeError(
            "ALINEA_KEY_ENCRYPTION_SECRET は Fernet.generate_key() で生成した "
            "32 url-safe base64 bytes の鍵を指定してください"
        ) from exc


def _hint(plaintext: str) -> str:
    return plaintext[-4:]


class LLMKeyStore:
    """byok_api_keys を読み書きする KeyStore 実装(plans/04 §11)。

    byok_api_keys(plans/02 §4.2)に Fernet 暗号化して保存する。マスタキーは
    ``LLMRuntimeConfig.key_encryption_secret``(Fernet 標準 44 文字 urlsafe base64)。
    カンマ区切りで複数指定でき、``MultiFernet`` で復号(先頭キーで暗号化)= ローテーション対応。

    - 平文キーは DB・ログ・例外メッセージに残さない。表示は ``key_hint``(末尾 4 文字)のみ。
    - キー解決順(§11.1): ユーザーキー(status != 'invalid')→ 運営キー → 未設定は
      ``ProviderError(kind=AUTH)``。
    - Fernet は遅延構築する(BYOK を使わない経路では key_encryption_secret 未設定でも動く)。
    """

    def __init__(self, session: AsyncSession, config: LLMRuntimeConfig) -> None:
        self._session = session
        self._config = config
        self._fernet: MultiFernet | None = None

    @property
    def _cipher(self) -> MultiFernet:
        if self._fernet is None:
            self._fernet = _build_fernet(self._config.key_encryption_secret)
        return self._fernet

    # -- BYOK 書き込み/表示(設定 4f「アカウント」・plans/04 §11.3) -----------------

    async def put(self, user_id: str, provider: str, plaintext: str) -> None:
        """暗号化して upsert(再入力=上書き)。status は 'untested' に戻す(§11.3 PUT)。"""
        token = self._cipher.encrypt(plaintext.encode("utf-8"))
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
        return self._cipher.decrypt(token).decode("utf-8")

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
                api_key = self._cipher.decrypt(bytes(row[0])).decode("utf-8")
                return ResolvedKey(provider=provider, api_key=api_key, source="user")
        operator = self._config.operator_api_keys.get(provider)
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


# --------------------------------------------------------------------------- #
# 使用量計測(旧 apps/api DbMeterHook)
# --------------------------------------------------------------------------- #
_METER_INSERT_SQL = text(
    "INSERT INTO usage_records ("
    "  user_id, library_item_id, job_id, task, provider, model, key_source,"
    "  input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens,"
    "  image_count, cost_usd, status, attempt, fallback_rank, error_kind,"
    "  latency_ms, request_id"
    ") VALUES ("
    "  CAST(:user_id AS uuid), CAST(:library_item_id AS uuid), CAST(:job_id AS uuid),"
    "  :task, :provider, :model, :key_source,"
    "  :input_tokens, :cached_input_tokens, :cache_write_input_tokens, :output_tokens,"
    "  :image_count, :cost_usd, :status, :attempt, :fallback_rank, :error_kind,"
    "  :latency_ms, :request_id"
    ")"
)


class LLMMeterHook:
    """usage_records へ 1 試行 1 行を記録する MeterHook 実装(plans/04 §10)。

    ``key_source`` の確定(§10.2・§11): LLMRouter は draft を常に ``key_source='operator'`` で
    作るため、ここで「その provider にユーザーの有効な BYOK があるか」で ``user`` に補正する。
    これによりクォータ集計(operator 行のみ)が正しく BYOK を除外できる(plans/07 §9.2)。
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        byok_providers: set[str] | None = None,
    ) -> None:
        self._session = session
        self._byok_providers = byok_providers or set()

    def _key_source(self, draft: UsageDraft) -> str:
        # provider にユーザーの有効な BYOK がある場合は 'user'(クォータ非消費)。
        if draft.user_id and draft.provider in self._byok_providers:
            return "user"
        return draft.key_source

    async def record(self, record: UsageDraft) -> None:
        usage = record.usage
        await self._session.execute(
            _METER_INSERT_SQL,
            {
                "user_id": record.user_id,
                "library_item_id": record.library_item_id,
                "job_id": record.job_id,
                "task": record.task,
                "provider": record.provider,
                "model": record.model,
                "key_source": self._key_source(record),
                "input_tokens": usage.input_tokens if usage else 0,
                "cached_input_tokens": usage.cached_input_tokens if usage else 0,
                "cache_write_input_tokens": usage.cache_write_input_tokens if usage else 0,
                "output_tokens": usage.output_tokens if usage else 0,
                "image_count": record.image_count,
                "cost_usd": record.cost_usd,
                "status": record.status,
                "attempt": record.attempt,
                "fallback_rank": record.fallback_rank,
                "error_kind": record.error_kind,
                "latency_ms": record.latency_ms,
                "request_id": record.request_id,
            },
        )


# --------------------------------------------------------------------------- #
# ユーザー文脈のルータ構築(旧 apps/api build_router_for_user の共有本体)
# --------------------------------------------------------------------------- #
async def build_user_router(
    *,
    session: AsyncSession,
    cache: redis.Redis | None,
    config: LLMRuntimeConfig,
    user_id: str | None,
    task: str,
    provider_factory: ProviderFactory | None = None,
    registry: ModelRegistry | None = None,
    key_store: LLMKeyStore | None = None,
    route_store: LLMRouteStore | None = None,
    attach_meter: bool = True,
) -> LLMRouter:
    """タスクのモデルチェーンを解決し、キー解決済みの ``LLMRouter`` を返す(§9.2・§11.1)。

    チェーンは operator と BYOK が使えるプロバイダのモデルに絞り、各モデルのキーは BYOK 優先・
    運営キーフォールバックで解決する。どちらも無いプロバイダのモデルは除外される。

    ``attach_meter=False`` のとき MeterHook を付けない。worker のジョブ単位ファクトリは
    ルータ構築後に解決用セッションを閉じるため、セッション束縛の MeterHook を持たせない
    (worker 側の usage 計測は Task 14+ の followup。秘密鍵・ルータをジョブ終了後に保持しない)。
    """
    key_store = key_store or LLMKeyStore(session, config)
    route_store = route_store or LLMRouteStore(
        session, cache, cache_ttl_s=config.route_cache_ttl_s
    )
    factory: ProviderFactory = provider_factory or build_provider

    byok_providers = await key_store.active_providers(user_id)
    available = set(config.operator_api_keys) | byok_providers
    entries = await route_store.resolve_chain(task, user_id, available_providers=available)

    chain: list[tuple[str, str, LLMProvider | None]] = []
    for model_id, provider in entries:
        resolved = await key_store.resolve_or_none(user_id, provider)
        instance = factory(provider, resolved.api_key) if resolved is not None else None
        chain.append((provider, model_id, instance))

    meter = LLMMeterHook(session, byok_providers=byok_providers) if attach_meter else None
    return LLMRouter(
        chain,
        registry=registry if registry is not None else default_registry(),
        meter=meter,
    )


__all__ = [
    "ChainEntry",
    "LLMKeyStore",
    "LLMMeterHook",
    "LLMRouteStore",
    "LLMRuntimeConfig",
    "ProviderFactory",
    "build_user_router",
    "default_registry",
    "route_cache_key",
]
