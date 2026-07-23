"""arq ワーカーの実行時ブートストラップ(M0-12/17/18 の統合残件)。

``on_startup`` で arq の ``ctx`` を実部品(sessionmaker / LLMRouter / Redis / arq プール /
S3 / arXiv HTTP クライアント / publish コールバック)で構成し、``pnpm dev`` / E2E で
worker が実際に翻訳・取り込みジョブを回せる状態にする。

設計上の要点:

- **apps 間 import 禁止**(Global Constraints)。DB ルート解決 / BYOK 解決は apps/api ではなく
  共有層 ``alinea_core.llm``(:func:`alinea_core.llm.build_user_router`)を使う(worker と api の
  両方が同じ実装を共用する)。SSE の発行形式は ``apps/api/services/events.py`` と
  ``apps/api/routers/jobs.py`` の**購読形式に一致**させる(import せず形式のみ複製。
  下記 :func:`_publish_event`)。
- **共有 ``ctx['router']`` は移行期間のみ**(Task 13): startup 時に 1 度だけ構築する全ジョブ共通の
  運営キールータで、per-user BYOK / モデル上書きを解決できない。新規コードは
  ``ctx['user_router_factory']``(:class:`alinea_worker.user_router.UserRouterFactory`)を使い、
  ジョブごとにユーザー別ルータを構築する(秘密鍵・ルータはジョブ終了後に保持しない。60 秒
  キャッシュは route chain metadata のみ)。ジョブ本体の移行は Task 14+。worker 側の
  per-user usage 計測は followup(``for_job`` は ``attach_meter=False``)。
- **キー未設定は黙って FakeLLMProvider に落とさない**(P3): 全プロバイダ未設定なら
  ``ctx['router']=None`` とし、翻訳を要するジョブは実行時に可視的に失敗させる
  (:func:`alinea_worker.main.run_job` が「キー未設定」をジョブログへ記録して失敗)。
  ただし ``ALINEA_FAKE_LLM=1`` のときのみ E2E/開発用に FakeLLMProvider を注入する。
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import redis.asyncio as redis
import structlog
from alinea_core.arxiv.fetch import make_arxiv_client
from alinea_core.db.models import LibraryItem
from alinea_core.db.session import get_sessionmaker
from alinea_core.llm import LLMRuntimeConfig
from alinea_core.parsing.pdf_parser import PdfOcrReadiness, check_pdf_ocr_readiness
from alinea_core.settings import CoreSettings, get_settings
from alinea_core.storage.s3 import S3Storage
from alinea_llm.protocols import EmbeddingProvider
from alinea_llm.providers import build_embedding_provider as build_embedding_provider_impl
from alinea_llm.providers import build_image_provider, build_provider
from alinea_llm.providers.openai_embeddings import DEFAULT_EMBEDDING_DIM, DEFAULT_EMBEDDING_MODEL
from alinea_llm.router import ChainEntry, ImageRouter, LLMRouter
from alinea_llm.testing.fake_provider import (
    FakeEmbeddingProvider,
    FakeImageProvider,
    FakeLLMProvider,
)
from arq.connections import ArqRedis, create_pool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alinea_worker.settings import redis_settings
from alinea_worker.user_router import TaskAwareLLMRouter, UserRouterFactory

log = structlog.get_logger("alinea.worker")

# ワーカーが未知タスクのルータを参照した時の診断用既定タスク。
DEFAULT_ROUTER_TASK = "translation"

# worker が text LLM に投げるタスク。explainer_image は ImageRouter 側で扱う。
TEXT_ROUTER_TASKS: tuple[str, ...] = (
    "translation",
    "retranslation_escalation",
    "summary",
    "article",
    "overview_figure_dsl",
    "vocab",
)

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
    """運営 API キーを読む(provider 名 → キー。空は除外)。

    CoreSettings(.env を上方向探索して読む)を基底とし、os.environ の明示値で
    上書きする。pnpm dev はシェルへ export しないため、環境変数のみを見ると
    .env に書いたキーが worker から見えず api 側とだけ挙動が食い違っていた。
    """
    keys: dict[str, str] = dict(get_settings().operator_api_keys)
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
    """ALINEA_FAKE_LLM=1 用の決定的ルータ(E2E/開発)。DB を参照しない。"""
    provider = FakeLLMProvider()
    chain: list[ChainEntry] = [("fake", "fake-model", provider)]
    return LLMRouter(chain)


def build_fake_image_router() -> ImageRouter:
    """ALINEA_FAKE_LLM=1 用の決定的画像ルータ(E2E/開発)。"""
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


# 埋め込みプロバイダ名(apps/api search_semantic.EMBEDDING_PROVIDER_NAME と一致。import せず複製)。
# 埋め込みは llm_task_routes のルーティング対象外(API 側も provider を "openai" 固定で解決する)。
EMBEDDING_PROVIDER_NAME = "openai"


def build_embedding_provider(
    *,
    operator_keys: dict[str, str] | None = None,
    provider_factory: Callable[[str, str], EmbeddingProvider] = build_embedding_provider_impl,
) -> EmbeddingProvider | None:
    """運営キーで埋め込みプロバイダを構築する(index_embeddings / code_analysis 用)。

    埋め込みは翻訳/画像チェーンと違いルーティングしない(apps/api も ``EMBEDDING_PROVIDER_NAME``
    固定)。運営キーが無ければ ``None``(呼び出し側 = handler は ``no_embedding_provider`` で
    可視 skip する)。BYOK は worker では followup(:func:`resolve_embedding_provider`)で扱う。

    これが無いと ``on_startup`` が ``ctx['embedding_provider']`` を張れず、セマンティック検索
    (paper/block 埋め込みの生成)とコード解析の検索インデックスが常に skip され、実運用で
    ``paper_embeddings`` / ``block_embeddings`` が永久に空のままになる(単体テストは ctx へ
    直接 Fake を注入するため緑になり見逃されていた)。
    """
    keys = operator_keys if operator_keys is not None else operator_keys_from_env()
    api_key = keys.get(EMBEDDING_PROVIDER_NAME)
    if not api_key:
        return None
    return provider_factory(EMBEDDING_PROVIDER_NAME, api_key)


def build_fake_embedding_provider() -> EmbeddingProvider:
    """ALINEA_FAKE_LLM=1 用の決定的埋め込みプロバイダ(E2E/開発)。

    次元は pgvector 列(``paper_embeddings.embedding vector(1536)``)と一致させる。既定 dim=8 の
    ままだと upsert が次元不一致で失敗する。
    """
    return FakeEmbeddingProvider(dim=DEFAULT_EMBEDDING_DIM)


async def build_router(
    session: AsyncSession,
    *,
    task: str = DEFAULT_ROUTER_TASK,
    operator_keys: dict[str, str] | None = None,
    provider_factory: Callable[[str, str], Any] = build_provider,
) -> LLMRouter | None:
    """運営キーが設定されたプロバイダのモデルだけで ``LLMRouter`` を構築する。

    どのプロバイダにも運営キーが無い(= 有効なチェーンが空)場合は ``None`` を返す。
    ``ALINEA_*_BASE_URL`` 上書きは各プロバイダ実装が吸収する(plans/12 §15)。
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


async def build_task_router(
    session: AsyncSession,
    *,
    tasks: Sequence[str] = TEXT_ROUTER_TASKS,
    operator_keys: dict[str, str] | None = None,
    provider_factory: Callable[[str, str], Any] = build_provider,
) -> TaskAwareLLMRouter | None:
    """task ごとの DB chain を保持する worker 用 LLM ルータを構築する。"""
    keys = operator_keys if operator_keys is not None else operator_keys_from_env()
    if not keys:
        return None

    routers: dict[str, LLMRouter] = {}
    for task in tasks:
        router = await build_router(
            session,
            task=task,
            operator_keys=keys,
            provider_factory=provider_factory,
        )
        if router is not None:
            routers[task] = router

    if not routers:
        return None
    return TaskAwareLLMRouter(routers)


def build_user_router_factory(
    maker: async_sessionmaker[AsyncSession],
    redis_client: redis.Redis,
    settings: CoreSettings,
    *,
    fake_llm: bool = False,
) -> UserRouterFactory:
    """ジョブ単位のユーザー別 LLM ルーターファクトリを構築する(Task 13)。

    運営キー(``operator_keys_from_env`` = .env + 環境変数)と ``ALINEA_KEY_ENCRYPTION_SECRET``
    (BYOK 復号)を ``LLMRuntimeConfig`` に束ね、共有層の ``build_user_router`` を per-user・
    per-job で呼べるようにする。60 秒キャッシュ(Redis)は route chain metadata のみ。
    秘密鍵はジョブ終了後に保持しない(ファクトリは不変の依存だけを持つ)。

    ``fake_llm=True``(``ALINEA_FAKE_LLM=1`` の E2E/開発)のときは、共有 ``build_fake_router``
    と同じく実プロバイダを構築せず ``FakeLLMProvider`` を注入する(``provider_factory``)。
    これがないと factory 経由(Task 14 以降の article/vocab/overview_figure/presentation/
    code_analysis など)のジョブが運営キーで実 API を叩いてしまい、E2E の決定性と
    「テストで実外部通信を行わない」制約(plans §4)を破る。DB のルートチェーンはそのまま
    解決されるが、各エントリの provider インスタンスだけが Fake に差し替わる。
    """
    config = LLMRuntimeConfig(
        operator_api_keys=operator_keys_from_env(),
        key_encryption_secret=settings.alinea_key_encryption_secret,
        route_cache_ttl_s=60,
    )
    provider_factory: Callable[[str, str], Any] | None = None
    if fake_llm:
        _fake_provider = FakeLLMProvider()
        # provider 名 / api_key を問わず決定的 Fake を返す(実 API は絶対に叩かない)。
        provider_factory = lambda _provider, _api_key: _fake_provider  # noqa: E731
    return UserRouterFactory(
        sessionmaker=maker,
        redis=redis_client,
        config=config,
        provider_factory=provider_factory,
    )


# --------------------------------------------------------------------------- #
# publish コールバック(pipeline / translate_section が await する)
# --------------------------------------------------------------------------- #

PublishData = dict[str, Any]
Publish = Callable[[PublishData], Awaitable[None]]


def make_publish(maker: async_sessionmaker[AsyncSession], r: redis.Redis) -> Publish:
    """SSE 用の publish コールバックを作る。

    pipeline / ``translate_section`` は ``{"type": ..., "library_item_id": ..., ...}`` を渡す
    (:func:`alinea_core.translation.pipeline.translate_section`)。イベント名は ``data['type']``、
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

    fake_llm = _env_truthy("ALINEA_FAKE_LLM")
    if fake_llm:
        router: Any | None = build_fake_router()
        image_router: ImageRouter | None = build_fake_image_router()
        embedding_provider: EmbeddingProvider | None = build_fake_embedding_provider()
    else:
        async with maker() as session:
            router = await build_task_router(session)
            image_router = await build_image_router(session)
        embedding_provider = build_embedding_provider()
    try:
        pdf_ocr_readiness = check_pdf_ocr_readiness()
    except Exception:
        pdf_ocr_readiness = PdfOcrReadiness(False, "ocr_readiness_failed", "eng")

    ctx["settings"] = settings
    ctx["sessionmaker"] = maker
    # 移行期間: 共有 ``router``(全ジョブ共通・運営キーのみ)は残しつつ、新規コードは per-user・
    # per-job の ``user_router_factory`` を使う(Task 13)。ジョブ本体の移行は Task 14+。
    ctx["router"] = router
    ctx["user_router_factory"] = build_user_router_factory(
        maker, redis_client, settings, fake_llm=fake_llm
    )
    ctx["image_router"] = image_router
    # 埋め込みプロバイダ(index_embeddings / code_analysis のセマンティック検索・検索インデックス)。
    # 運営キー未設定なら None → handler は no_embedding_provider で可視 skip する(P3)。
    # 埋め込みモデル/次元は既定(text-embedding-3-small / 1536)を ctx に載せ、handler が
    # DEFAULT にフォールバックできるようにしておく(pgvector 列 dim と一致)。
    ctx["embedding_provider"] = embedding_provider
    ctx["embedding_model"] = DEFAULT_EMBEDDING_MODEL
    ctx["embedding_dim"] = DEFAULT_EMBEDDING_DIM
    ctx["redis"] = redis_client
    ctx["arq_pool"] = arq_pool
    ctx["s3"] = S3Storage(settings)
    ctx["arxiv_http"] = make_arxiv_client(settings)
    ctx["publish"] = make_publish(maker, redis_client)
    ctx["pdf_ocr_readiness"] = pdf_ocr_readiness.as_dict()

    await log.ainfo(
        "worker_startup",
        router_configured=router is not None,
        router_tasks=getattr(router, "tasks", (DEFAULT_ROUTER_TASK,)) if router is not None else (),
        image_router_configured=image_router is not None,
        embedding_configured=embedding_provider is not None,
        fake_llm=fake_llm,
        operator_providers=sorted(operator_keys_from_env()),
        pdf_ocr=pdf_ocr_readiness.as_dict(),
        redis_url=settings.redis_url,
    )
    if not pdf_ocr_readiness.available:
        await log.awarning(
            "worker_pdf_ocr_unavailable",
            **pdf_ocr_readiness.as_dict(),
            message=(
                "PDF OCR is unavailable; text-layer PDFs continue to work, but scanned PDFs "
                "cannot use the final OCR fallback"
            ),
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
    "EMBEDDING_PROVIDER_NAME",
    "Publish",
    "TaskAwareLLMRouter",
    "build_embedding_provider",
    "build_fake_embedding_provider",
    "build_fake_router",
    "build_router",
    "build_task_router",
    "build_user_router_factory",
    "channel_key",
    "make_publish",
    "on_shutdown",
    "on_startup",
    "operator_keys_from_env",
    "stream_key",
]
