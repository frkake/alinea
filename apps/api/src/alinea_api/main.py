"""FastAPI アプリ生成。ルータ登録・ミドルウェア・例外ハンドラ・OpenAPI(/api/openapi.json)。

M0-10 API 共通基盤(plans/03 §1・plans/01 §9)。
"""

from __future__ import annotations

from fastapi import FastAPI

from alinea_api.errors import register_exception_handlers
from alinea_api.logging import configure_logging
from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
from alinea_api.ratelimit import RateLimitMiddleware
from alinea_api.redis_client import get_redis

# `annotations` / `settings` はそれぞれ ``from __future__ import annotations`` と
# stdlib settings 名に衝突するため別名で取り込む(mypy が __future__._Feature に
# 解決してしまうのを防ぐ)。
from alinea_api.routers import (
    annotations as annotations_router,
)
from alinea_api.routers import (
    articles,
    assets,
    auth,
    chat,
    code_analysis,
    collections,
    dashboard,
    export,
    figures,
    glossaries,
    health,
    ingest,
    jobs,
    library_items,
    llm_settings,
    notes,
    notifications,
    papers,
    presentations,
    publications,
    resources,
    search,
    share,
    translations,
    viewer,
    vocab,
    vocab_candidates,
)
from alinea_api.routers import (
    settings as settings_router,
)
from alinea_api.settings import get_api_settings


def create_app() -> FastAPI:
    settings = get_api_settings()
    configure_logging(json_logs=True)

    app = FastAPI(
        title="Alinea API",
        version="0.1.0",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    register_exception_handlers(app)

    # ミドルウェア(最後に add したものが最外周)。外→内: RequestId → RateLimit → OriginCsrf。
    app.add_middleware(OriginCsrfMiddleware, settings=settings)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(jobs.router)
    app.include_router(ingest.router)
    app.include_router(papers.router)
    app.include_router(assets.router)
    app.include_router(viewer.router)
    app.include_router(translations.router)
    app.include_router(chat.router)
    app.include_router(library_items.router)
    app.include_router(settings_router.router)
    app.include_router(llm_settings.router)
    app.include_router(annotations_router.router)
    app.include_router(notes.router)
    app.include_router(notifications.router)
    app.include_router(search.router)
    app.include_router(dashboard.router)
    app.include_router(glossaries.router)
    app.include_router(export.router)
    app.include_router(articles.router)
    app.include_router(figures.router)
    app.include_router(share.router)
    app.include_router(collections.router)
    app.include_router(vocab.router)
    app.include_router(vocab_candidates.router)
    app.include_router(resources.router)
    app.include_router(publications.router)
    app.include_router(presentations.router)
    app.include_router(code_analysis.router)

    return app


app = create_app()
