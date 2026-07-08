"""arq ワーカーの実行時ブートストラップ(M0-12/17/18 の統合残件)。

``on_startup`` で arq の ``ctx`` を実部品(sessionmaker / LLMRouter / Redis / arq プール /
S3 / arXiv HTTP クライアント / publish コールバック)で構成し、``pnpm dev`` / E2E で
worker が実際に翻訳・取り込みジョブを回せる状態にする。

設計上の要点:

- **apps 間 import 禁止**(Global Constraints)。LLM ルートは ``apps/api`` の ``DbRouteStore`` を
  使わず、``llm_task_routes`` / ``llm_models`` を **raw SQL** で読む。SSE の発行形式は
  ``apps/api/services/events.py`` と ``apps/api/routers/jobs.py`` の**購読形式に一致**させる
  (import せず形式のみ複製。下記 :func:`_publish_event`)。
- **運営キーのみ**(BYOK なし): worker のルータは startup 時に 1 度だけ構築する全ジョブ共通の
  インスタンスで、per-user のセッションを保持できないため計測フック(``DbMeterHook``)は付けない
  (per-user BYOK / worker 側の usage 計測は followups)。
- **キー未設定は黙って FakeLLMProvider に落とさない**(P3): 全プロバイダ未設定なら
  ``ctx['router']=None`` とし、翻訳を要するジョブは実行時に可視的に失敗させる
  (:func:`yakudoku_worker.main.run_job` が「キー未設定」をジョブログへ記録して失敗)。
  ただし ``YAKUDOKU_FAKE_LLM=1`` のときのみ E2E/開発用に FakeLLMProvider を注入する。
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis
import structlog
from arq.connections import ArqRedis, create_pool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from yakudoku_core.arxiv.fetch import make_arxiv_client
from yakudoku_core.db.models import LibraryItem
from yakudoku_core.db.session import get_sessionmaker
from yakudoku_core.settings import CoreSettings, get_settings
from yakudoku_core.storage.s3 import S3Storage
from yakudoku_llm.providers import build_image_provider, build_provider
from yakudoku_llm.router import ChainEntry, ImageRouter, LLMRouter
from yakudoku_llm.testing.fake_provider import FakeImageProvider, FakeLLMProvider

from yakudoku_worker.settings import redis_settings

log = structlog.get_logger("yakudoku.worker")

# ワーカーが LLM ルータを構築する際の既定タスク。worker の主タスクは翻訳のため
# ``translation`` チェーンでルータを 1 本構築し、要約など従属タスクも同一ルータで実行する
# (LLMRouter は task をルーティングに使わず計測メタにのみ使う。plans/04 §9)。
DEFAULT_ROUTER_TASK = "translation"

# 運営キーの環境変数名(plans/04 §16・apps/api ApiSettings.operator_api_keys と同一マッピング)。
# provider 名 → env 変数名(先頭が優先)。空文字は「未設定」として除外する。
# google は plans/04 §16(GEMINI)と plans/01 §8.4 / .env.example(GOOGLE)の両方を受理。
_OPERATOR_KEY_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "xai": ("XAI_API_KEY",),
}

# --------------------------------------------------------------------------- #
# SSE 発行形式(apps/api/services/events.py と一致させる。import せず複製)。
# apps 間 import 禁止のため、チャネル/ストリームのキーと封筒(envelope)形式を複製する。
# 齟齬が出ると /api/events の購読側(apps/api/routers/jobs.py)が読めなくなるので変更注意。
# --------------------------------------------------------------------------- #
_STREAM_MAXLEN = 1000


def channel_key(user_id: str) -> str:
    """Redis Pub/Sub チャネル(events.py と一致)。"""
    return f"events:user:{user_id}"


def stream_key(user_id: str) -> str:
    """Redis Stream キー(events.py と一致。Last-Event-ID 再送用)。"""
    return f"events:log:{user_id}"


async def _publish_event(
    r: redis.Redis, user_id: str, event_type: str, data: dict[str, Any]
) -> str:
    """イベントを Stream に追記し Pub/Sub に発行する(events.py.publish_event と同形式)。"""
    fields: dict[Any, Any] = {"event": event_type, "data": json.dumps(data, ensure_ascii=False)}
    event_id = await r.xadd(stream_key(user_id), fields, maxlen=_STREAM_MAXLEN, approximate=True)
    envelope = json.dumps({"id": event_id, "event": event_type, "data": data}, ensure_ascii=False)
    await r.publish(channel_key(user_id), envelope)
    return str(event_id)


# --------------------------------------------------------------------------- #
# 運営キー / ルータ構築
# --------------------------------------------------------------------------- #


def operator_keys_from_env() -> dict[str, str]:
    """環境変数から運営 API キーを読む(provider 名 → キー。空は除外)。"""
    keys: dict[str, str] = {}
    for provider, env_names in _OPERATOR_KEY_ENV.items():
        for env_name in env_names:
            value = os.environ.get(env_name, "").strip()
            if value:
                keys[provider] = value
                break
    return keys


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


async def _resolve_chain(session: AsyncSession, task: str) -> list[tuple[str, str]]:
    """``llm_task_routes`` / ``llm_models`` を raw SQL で読み、(model_id, provider) を返す。

    ``llm_task_routes.chain`` の順序を保ちつつ ``llm_models.enabled=true`` のモデルだけに絞る
    (apps/api の ``DbRouteStore._base_chain`` と同じ規則。ただし import はしない)。
    """
    route = (
        await session.execute(
            text("SELECT chain FROM llm_task_routes WHERE task = :task"),
            {"task": task},
        )
    ).first()
    if route is None:
        return []
    chain: list[str] = list(route[0] or [])

    rows = (
        await session.execute(text("SELECT id, provider FROM llm_models WHERE enabled = true"))
    ).fetchall()
    provider_of: dict[str, str] = {row[0]: row[1] for row in rows}
    return [(model_id, provider_of[model_id]) for model_id in chain if model_id in provider_of]


def build_fake_router() -> LLMRouter:
    """YAKUDOKU_FAKE_LLM=1 用の決定的ルータ(E2E/開発)。DB を参照しない。"""
    provider = FakeLLMProvider()
    chain: list[ChainEntry] = [("fake", "fake-model", provider)]
    return LLMRouter(chain)


def build_fake_image_router() -> ImageRouter:
    """YAKUDOKU_FAKE_LLM=1 用の決定的画像ルータ(E2E/開発)。"""
    return ImageRouter([("fake", "fake-image-model", FakeImageProvider())])


async def build_image_router(
    session: AsyncSession,
    *,
    task: str = "explainer_image",
    operator_keys: dict[str, str] | None = None,
    provider_factory: Callable[[str, str], Any] = build_image_provider,
) -> ImageRouter | None:
    """運営キーが設定された画像プロバイダだけで ``ImageRouter`` を構築する(plans/07 §6)。

    どのプロバイダにも運営キーが無ければ ``None``(解説図・ラスターモードは P3 準拠で
    可視的に失敗する)。
    """
    keys = operator_keys if operator_keys is not None else operator_keys_from_env()
    if not keys:
        return None
    entries = await _resolve_chain(session, task)
    chain: list[tuple[str, str, Any]] = []
    for model_id, provider in entries:
        api_key = keys.get(provider)
        if not api_key:
            continue
        chain.append((provider, model_id, provider_factory(provider, api_key)))
    if not chain:
        return None
    return ImageRouter(chain)


async def build_router(
    session: AsyncSession,
    *,
    task: str = DEFAULT_ROUTER_TASK,
    operator_keys: dict[str, str] | None = None,
    provider_factory: Callable[[str, str], Any] = build_provider,
) -> LLMRouter | None:
    """運営キーが設定されたプロバイダのモデルだけで ``LLMRouter`` を構築する。

    どのプロバイダにも運営キーが無い(= 有効なチェーンが空)場合は ``None`` を返す。
    ``YAKUDOKU_*_BASE_URL`` 上書きは各プロバイダ実装が吸収する(plans/12 §15)。
    """
    keys = operator_keys if operator_keys is not None else operator_keys_from_env()
    if not keys:
        return None

    entries = await _resolve_chain(session, task)
    chain: list[ChainEntry] = []
    for model_id, provider in entries:
        api_key = keys.get(provider)
        if not api_key:
            continue  # 運営キー未設定プロバイダは除外(plans/04 §11.1-3・§15)。
        chain.append((provider, model_id, provider_factory(provider, api_key)))

    if not chain:
        return None
    return LLMRouter(chain)


# --------------------------------------------------------------------------- #
# publish コールバック(pipeline / translate_section が await する)
# --------------------------------------------------------------------------- #

PublishData = dict[str, Any]
Publish = Callable[[PublishData], Awaitable[None]]


def make_publish(maker: async_sessionmaker[AsyncSession], r: redis.Redis) -> Publish:
    """SSE 用の publish コールバックを作る。

    pipeline / ``translate_section`` は ``{"type": ..., "library_item_id": ..., ...}`` を渡す
    (:func:`yakudoku_core.translation.pipeline.translate_section`)。イベント名は ``data['type']``、
    宛先ユーザーは ``data['user_id']`` があればそれを、無ければ ``library_item_id`` から解決する。
    宛先が解決できない場合は best-effort でスキップ(ジョブ本体は止めない)。
    """
    user_cache: dict[str, str | None] = {}

    async def _user_for_item(library_item_id: str) -> str | None:
        if library_item_id in user_cache:
            return user_cache[library_item_id]
        async with maker() as session:
            item = await session.get(LibraryItem, library_item_id)
            user_id = str(item.user_id) if item is not None else None
        user_cache[library_item_id] = user_id
        return user_id

    async def publish(data: PublishData) -> None:
        try:
            event_type = str(data.get("type") or "message")
            user_id = data.get("user_id")
            if user_id is None:
                library_item_id = data.get("library_item_id")
                if library_item_id:
                    user_id = await _user_for_item(str(library_item_id))
            if not user_id:
                # structlog は第1引数(event)を予約するため、キー名を event ではなく
                # event_type にする(以前は `event=event_type` が
                # "got multiple values for argument 'event'" で毎回失敗し、この分岐に来た
                # publish が常に silently 失敗していた。M2-17 で発見。deviations 参照)。
                await log.adebug("publish_no_target", event_type=event_type)
                return
            await _publish_event(r, str(user_id), event_type, data)
        except Exception as exc:  # publish 失敗でジョブ本体を落とさない(SSE は best-effort)。
            await log.awarning("publish_failed", error=str(exc))

    return publish


# --------------------------------------------------------------------------- #
# arq on_startup / on_shutdown
# --------------------------------------------------------------------------- #


def _make_redis(settings: CoreSettings) -> redis.Redis:
    # decode_responses=True は apps/api の redis_client と一致させる(events.py が str 前提で
    # xadd の返り値を JSON 直列化するため)。arXiv スロットルは set() のみ使うので影響しない。
    client: redis.Redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return client


async def on_startup(ctx: dict[str, Any]) -> None:
    """arq ワーカー起動時に ctx を実部品で構成する(InteractiveWorker/BulkWorker 共通)。"""
    settings = get_settings()
    maker = get_sessionmaker()
    redis_client = _make_redis(settings)
    arq_pool: ArqRedis = await create_pool(redis_settings())

    fake_llm = _env_truthy("YAKUDOKU_FAKE_LLM")
    if fake_llm:
        router: LLMRouter | None = build_fake_router()
        image_router: ImageRouter | None = build_fake_image_router()
    else:
        async with maker() as session:
            router = await build_router(session)
            image_router = await build_image_router(session)

    ctx["settings"] = settings
    ctx["sessionmaker"] = maker
    ctx["router"] = router
    ctx["image_router"] = image_router
    ctx["redis"] = redis_client
    ctx["arq_pool"] = arq_pool
    ctx["s3"] = S3Storage(settings)
    ctx["arxiv_http"] = make_arxiv_client(settings)
    ctx["publish"] = make_publish(maker, redis_client)

    await log.ainfo(
        "worker_startup",
        router_configured=router is not None,
        fake_llm=fake_llm,
        operator_providers=sorted(operator_keys_from_env()),
        redis_url=settings.redis_url,
    )
    if router is None and not fake_llm:
        await log.awarning(
            "worker_router_unconfigured",
            message=(
                "LLM 運営キー未設定。翻訳を要するジョブは実行時に失敗します"
                "(BYOK/運営キー登録が必要)。"
            ),
        )


async def on_shutdown(ctx: dict[str, Any]) -> None:
    """arq ワーカー停止時に外部接続を閉じる。"""
    http = ctx.get("arxiv_http")
    if http is not None:
        await http.aclose()
    arq_pool = ctx.get("arq_pool")
    if arq_pool is not None:
        await arq_pool.aclose()
    redis_client = ctx.get("redis")
    if redis_client is not None:
        await redis_client.aclose()
    await log.ainfo("worker_shutdown")


__all__ = [
    "DEFAULT_ROUTER_TASK",
    "Publish",
    "build_fake_router",
    "build_router",
    "channel_key",
    "make_publish",
    "on_shutdown",
    "on_startup",
    "operator_keys_from_env",
    "stream_key",
]
