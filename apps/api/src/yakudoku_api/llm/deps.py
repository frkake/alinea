"""ユーザー文脈での LLM ルータ構築とクォータ判定(plans/04 §9・§11・§15、plans/07 §9)。

- ``build_router_for_user``: タスクの解決済みチェーンを、BYOK 優先・運営キーフォールバック・
  未設定プロバイダ除外で ``LLMRouter`` に組み立てる(計測は ``DbMeterHook``)。
- ``check_quota``: 月次クォータ(``quota_limits`` と ``usage_records``、JST 暦月・operator 行のみ)
  を事前判定し、超過なら 429 ``quota_exceeded``(Problem Details)を送出。BYOK 設定済み
  プロバイダはスキップ(plans/07 §9.2)。
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path

import redis.asyncio as redis
import yakudoku_llm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_llm.protocols import LLMProvider
from yakudoku_llm.providers import build_provider
from yakudoku_llm.registry import ModelRegistry
from yakudoku_llm.router import LLMRouter

from yakudoku_api.errors import ProblemException
from yakudoku_api.llm.key_store import DbKeyStore
from yakudoku_api.llm.meter import DbMeterHook
from yakudoku_api.llm.route_store import DbRouteStore
from yakudoku_api.settings import ApiSettings, get_api_settings

# provider 名 + api_key → LLMProvider(既定は実アダプタ。テストは Fake を注入する)。
ProviderFactory = Callable[[str, str], LLMProvider]

# JST(Asia/Tokyo)暦月の開始インスタント(plans/07 §9.2)。
_JST_MONTH_START = (
    "created_at >= (date_trunc('month', now() AT TIME ZONE 'Asia/Tokyo') AT TIME ZONE 'Asia/Tokyo')"
)

# クォータカウンタ(plans/07 §9.2)。(集計式, 対象 usage_records の述語)。
_COUNTER_SQL: dict[str, tuple[str, str]] = {
    "translation_papers": ("count(*)", "task = 'translation'"),
    "chat_messages": ("count(*)", "(task = 'chat' OR (task = 'summary' AND job_id IS NULL))"),
    "images": ("COALESCE(sum(image_count), 0)", "task = 'explainer_image'"),
    "article_generations": ("count(*)", "(task = 'article' OR task = 'overview_figure_dsl')"),
    "vocab_generations": ("count(*)", "task = 'vocab'"),
}

# quota_limits が未シードのときのフォールバック(plans/07 §9.2 の既定値)。
_DEFAULT_LIMITS: dict[str, int] = {
    "translation_papers": 30,
    "chat_messages": 500,
    "images": 20,
    "article_generations": 30,
    "vocab_generations": 300,
}

# 429 を返す生成タスク → カウンタ(plans/07 §9.2)。translation は waiting_quota 扱い
# (取り込みパイプラインの管轄・plans/03 §17.4)なのでここでは 429 判定しない。
_TASK_TO_COUNTER: dict[str, str] = {
    "chat": "chat_messages",
    "summary": "chat_messages",
    "explainer_image": "images",
    "article": "article_generations",
    "overview_figure_dsl": "article_generations",
    "vocab": "vocab_generations",
}

# plans/07 §9.2 / plans/03 §1.4: BYOK 誘導文を必ず含める。
_QUOTA_DETAIL = (
    "今月の利用上限に達しました。設定画面で自分の API キー(BYOK)を登録すると制限なく利用できます。"
)


@functools.lru_cache(maxsize=1)
def _default_registry() -> ModelRegistry | None:
    """価格計算用の ModelRegistry(packages/llm/models.yaml シード)。失敗時は None。"""
    path = Path(yakudoku_llm.__file__).resolve().parents[2] / "models.yaml"
    try:
        return ModelRegistry.from_yaml(path)
    except (OSError, ValueError):
        return None


def _route_store(
    session: AsyncSession, cache: redis.Redis | None, settings: ApiSettings
) -> DbRouteStore:
    return DbRouteStore(session, cache, cache_ttl_s=settings.yakudoku_llm_route_cache_ttl_s)


async def build_router_for_user(
    session: AsyncSession,
    user_id: str | None,
    task: str,
    *,
    cache: redis.Redis | None = None,
    settings: ApiSettings | None = None,
    key_store: DbKeyStore | None = None,
    route_store: DbRouteStore | None = None,
    registry: ModelRegistry | None = None,
    provider_factory: ProviderFactory | None = None,
) -> LLMRouter:
    """タスクのモデルチェーンを解決し、キー解決済みの ``LLMRouter`` を返す(§9.2・§11.1)。

    チェーンは operator と BYOK が使えるプロバイダのモデルに絞り、各モデルのキーは BYOK 優先・
    運営キーフォールバックで解決する。どちらも無いプロバイダのモデルは除外される。
    """
    settings = settings or get_api_settings()
    key_store = key_store or DbKeyStore(session, settings)
    route_store = route_store or _route_store(session, cache, settings)
    factory: ProviderFactory = provider_factory or build_provider

    byok_providers = await key_store.active_providers(user_id)
    available = set(settings.operator_api_keys) | byok_providers
    entries = await route_store.resolve_chain(task, user_id, available_providers=available)

    chain: list[tuple[str, str, LLMProvider | None]] = []
    for model_id, provider in entries:
        resolved = await key_store.resolve_or_none(user_id, provider)
        instance = factory(provider, resolved.api_key) if resolved is not None else None
        chain.append((provider, model_id, instance))

    meter = DbMeterHook(session, byok_providers=byok_providers)
    return LLMRouter(
        chain,
        registry=registry if registry is not None else _default_registry(),
        meter=meter,
    )


async def _count_usage(session: AsyncSession, user_id: str, counter: str) -> int:
    agg, predicate = _COUNTER_SQL[counter]
    # agg / predicate は固定辞書由来(ユーザー入力なし)。user_id のみバインド。
    sql = text(
        f"SELECT {agg} FROM usage_records "  # noqa: S608
        f"WHERE user_id = CAST(:user_id AS uuid) AND key_source = 'operator' "
        f"AND status = 'ok' AND {predicate} AND {_JST_MONTH_START}"
    )
    value = (await session.execute(sql, {"user_id": user_id})).scalar_one()
    return int(value or 0)


async def _limit_for(session: AsyncSession, counter: str) -> int:
    value = (
        await session.execute(
            text("SELECT monthly_limit FROM quota_limits WHERE key = :key"),
            {"key": counter},
        )
    ).scalar_one_or_none()
    return int(value) if value is not None else _DEFAULT_LIMITS[counter]


async def check_quota(
    session: AsyncSession,
    user_id: str | None,
    task: str,
    *,
    cache: redis.Redis | None = None,
    settings: ApiSettings | None = None,
    key_store: DbKeyStore | None = None,
    route_store: DbRouteStore | None = None,
) -> None:
    """月次クォータ超過なら 429 ``quota_exceeded`` を送出(plans/07 §9.2 / plans/03 §17.4)。

    - 未ログイン / カウンタ非対象タスク(translation・retranslation_escalation など)は何もしない。
    - チェーン先頭プロバイダに有効な BYOK があれば判定をスキップ(BYOK は非消費)。
    """
    if not user_id:
        return
    counter = _TASK_TO_COUNTER.get(task)
    if counter is None:
        return

    settings = settings or get_api_settings()
    key_store = key_store or DbKeyStore(session, settings)
    route_store = route_store or _route_store(session, cache, settings)

    primary = await route_store.primary_provider(task, user_id)
    if primary is not None and primary in await key_store.active_providers(user_id):
        return

    used = await _count_usage(session, user_id, counter)
    limit = await _limit_for(session, counter)
    if used >= limit:
        raise ProblemException("quota_exceeded", detail=_QUOTA_DETAIL)


async def quota_usage(session: AsyncSession, user_id: str) -> dict[str, dict[str, int]]:
    """5 カウンタの当月使用量と上限(GET /api/settings/quota 用の集計・plans/03 §17.4)。"""
    result: dict[str, dict[str, int]] = {}
    for counter in _COUNTER_SQL:
        result[counter] = {
            "used": await _count_usage(session, user_id, counter),
            "limit": await _limit_for(session, counter),
        }
    return result


__all__ = [
    "ProviderFactory",
    "build_router_for_user",
    "check_quota",
    "quota_usage",
]
