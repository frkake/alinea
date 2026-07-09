"""figures API テスト(M2-05/M2-06。plans/03 §20)。

- 全体概要図: GET(現行版+版一覧)・rewrite(202 job_id)・restore(is_current 付替え・新行なし)・
  SVG 配信/ダウンロード(``?download=true`` で Content-Disposition)。
- 解説図: regenerate(202 job_id)、現行版以外は 409。
- 所有権チェック(他ユーザーの記事は 404)。

本タスク所有ルータ(figures)のみをマウントした専用アプリで検証する(test_dashboard.py と同方針。
main.py への ``app.include_router(figures.router)`` 登録は followups 参照)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import Article, ExplainerFigure, Job, OverviewFigure, User
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    """本タスク所有ルータ(figures)のみをマウントしたアプリ(test_dashboard.py と同方針)。"""
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import figures
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(figures.router)
    return app


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    email = f"fig-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
    token = await create_session(redis_client, user.id)
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield ac, uid
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


def _overview_dsl(
    *, body2: str = "reflow と蒸留で FID 4.85 を1ステップで達成した"
) -> dict[str, Any]:
    return {
        "layout": "flow-3",
        "cards": [
            {
                "role": "problem",
                "label": "課題",
                "heading": "多数のサンプリングステップが必要",
                "body": "拡散モデルは多数のサンプリングステップを要する。",
                "tone": "neutral",
            },
            {
                "role": "proposal",
                "label": "提案 — RECTIFIED FLOW",
                "heading": "直線輸送を学習する",
                "body": "始点と終点を直線で結ぶ輸送を学習する。",
                "tone": "accent",
            },
            {
                "role": "result",
                "label": "結果",
                "heading": "少ないステップで高品質",
                "body": body2,
                "tone": "green",
            },
        ],
        "connectors": [{"from": 0, "to": 1}, {"from": 1, "to": 2}],
        "footer": {"generated_by": "✦ AI 生成 · Alinea", "date": "2026-07-06"},
    }


class _Seeded:
    __slots__ = ("article", "arxiv_id", "revision_id", "rows")

    def __init__(
        self, article: Article, rows: list[OverviewFigure], revision_id: str, arxiv_id: str
    ) -> None:
        self.article = article
        self.rows = rows
        self.revision_id = revision_id
        self.arxiv_id = arxiv_id


async def _mk_article_with_overview(
    db: AsyncSession, factories: Any, user: User, *, versions: int = 1
) -> _Seeded:
    arxiv_id = f"2209.{uuid.uuid4().hex[:5]}"
    paper = await factories.make_paper(db, owner=user, visibility="private", arxiv_id=arxiv_id)
    await factories.make_revision(db, paper=paper)
    item = await factories.make_library_item(db, user=user, paper=paper, status="reading")
    article = await factories.make_article(db, library_item=item, with_overview_figure=False)

    rows: list[OverviewFigure] = []
    for v in range(1, versions + 1):
        row = OverviewFigure(
            article_id=str(article.id),
            version=v,
            is_current=(v == versions),
            render_mode="svg",
            dsl=_overview_dsl(body2=f"reflow と蒸留で FID 4.85 を1ステップで達成(版{v})"),
            svg_storage_key=f"renders/overview/{article.id}/v{v}.svg",
            evidence_anchors=[
                {
                    "ref": 1,
                    "display": "§1",
                    "anchor": {
                        "revision_id": str(paper.latest_revision_id),
                        "block_id": "blk-p1",
                        "start": None,
                        "end": None,
                        "quote": None,
                        "side": "source",
                        "display": "§1",
                    },
                }
            ],
        )
        db.add(row)
        rows.append(row)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return _Seeded(article, rows, str(paper.latest_revision_id), arxiv_id)


async def _put_svg(storage: Any, key: str, content: bytes = b"<svg></svg>\n") -> None:
    await storage.put(storage.assets_bucket, key, content, content_type="image/svg+xml")


# --------------------------------------------------------------------------- #
# GET /api/articles/{article_id}/overview-figure
# --------------------------------------------------------------------------- #
async def test_get_overview_figure_returns_current_version_and_versions_list(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    seeded = await _mk_article_with_overview(db_session, factories, user, versions=2)
    article, rows = seeded.article, seeded.rows

    resp = await client.get(f"/api/articles/{article.id}/overview-figure")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["id"] == str(rows[1].id)
    assert body["svg_url"] == f"/api/overview-figures/{rows[1].id}/versions/2/svg"
    assert body["raster_url"] is None
    assert body["dsl"]["layout"] == "flow-3"
    assert [c["role"] for c in body["dsl"]["cards"]] == ["problem", "proposal", "result"]
    assert body["dsl"]["footer"]["generated_by"] == "✦ AI 生成 · Alinea"
    assert body["evidence"] == [
        {
            "display": "§1",
            "anchor": {
                "revision_id": seeded.revision_id,
                "block_id": "blk-p1",
                "start": None,
                "end": None,
                "quote": None,
                "side": "source",
                "display": "§1",
            },
        }
    ]
    versions = sorted(v["version"] for v in body["versions"])
    assert versions == [1, 2]


async def test_get_overview_figure_404_when_not_generated(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    article = await factories.make_article(
        db_session, library_item=item, with_overview_figure=False
    )
    await db_session.commit()

    resp = await client.get(f"/api/articles/{article.id}/overview-figure")
    assert resp.status_code == 404


async def test_get_overview_figure_404_for_other_users_article(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, _uid = auth
    other = await factories.make_user(db_session)
    seeded = await _mk_article_with_overview(db_session, factories, other)

    resp = await client.get(f"/api/articles/{seeded.article.id}/overview-figure")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST rewrite
# --------------------------------------------------------------------------- #
async def test_rewrite_overview_figure_enqueues_figure_job(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    seeded = await _mk_article_with_overview(db_session, factories, user)
    article = seeded.article

    resp = await client.post(
        f"/api/articles/{article.id}/overview-figure/rewrite",
        json={"instruction": "もっと簡潔に"},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    job = await db_session.get(Job, job_id)
    assert job is not None
    assert job.kind == "figure"
    assert job.payload["figure_kind"] == "overview"
    assert job.payload["article_id"] == str(article.id)
    assert job.payload["instruction"] == "もっと簡潔に"
    assert job.user_id == uid


async def test_rewrite_overview_figure_404_when_not_generated(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    article = await factories.make_article(
        db_session, library_item=item, with_overview_figure=False
    )
    await db_session.commit()

    resp = await client.post(f"/api/articles/{article.id}/overview-figure/rewrite", json={})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST restore(新行を作らない)。PY-FIG-03: 旧版への restore 復帰(API 経路)。
# --------------------------------------------------------------------------- #
async def test_restore_version_flips_is_current_without_new_row(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    seeded = await _mk_article_with_overview(db_session, factories, user, versions=2)
    article = seeded.article
    v1, v2 = seeded.rows[0], seeded.rows[1]

    count_before = (
        (
            await db_session.execute(
                select(OverviewFigure).where(OverviewFigure.article_id == article.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(count_before) == 2

    resp = await client.post(f"/api/articles/{article.id}/overview-figure/versions/1/restore")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(v1.id)
    assert body["version"] == 1

    await db_session.refresh(v1)
    await db_session.refresh(v2)
    assert v1.is_current is True
    assert v2.is_current is False

    count_after = (
        (
            await db_session.execute(
                select(OverviewFigure).where(OverviewFigure.article_id == article.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(count_after) == 2  # 新行は作らない(plans/07 §5.3)


async def test_restore_version_404_when_missing(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    seeded = await _mk_article_with_overview(db_session, factories, user)

    resp = await client.post(
        f"/api/articles/{seeded.article.id}/overview-figure/versions/99/restore"
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# GET SVG 配信・ダウンロード
# --------------------------------------------------------------------------- #
async def test_get_overview_figure_svg_serves_bytes_and_download_header(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    from alinea_core.storage.s3 import S3Storage

    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    seeded = await _mk_article_with_overview(db_session, factories, user)
    row = seeded.rows[0]
    assert row.svg_storage_key is not None
    storage = S3Storage()
    svg_bytes = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>\n"
    await _put_svg(storage, row.svg_storage_key, svg_bytes)

    resp = await client.get(f"/api/overview-figures/{row.id}/versions/{row.version}/svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert resp.content == svg_bytes
    assert "content-disposition" not in resp.headers

    resp2 = await client.get(
        f"/api/overview-figures/{row.id}/versions/{row.version}/svg", params={"download": "true"}
    )
    assert resp2.status_code == 200
    disposition = resp2.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert seeded.arxiv_id in disposition
    assert f"v{row.version}" in disposition


async def test_get_overview_figure_svg_404_for_wrong_version(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    seeded = await _mk_article_with_overview(db_session, factories, user)
    row = seeded.rows[0]

    resp = await client.get(f"/api/overview-figures/{row.id}/versions/99/svg")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST explainer regenerate
# --------------------------------------------------------------------------- #
async def _mk_explainer(
    db: AsyncSession, factories: Any, user: User, *, is_current: bool = True
) -> tuple[Article, ExplainerFigure]:
    paper = await factories.make_paper(db, owner=user, visibility="private")
    await factories.make_revision(db, paper=paper)
    item = await factories.make_library_item(db, user=user, paper=paper)
    article = await factories.make_article(db, library_item=item, with_overview_figure=False)
    figure = ExplainerFigure(
        article_id=str(article.id),
        slot=0,
        version=1,
        is_current=is_current,
        provider="google",
        model="gemini-3.1-flash-image",
        prompt="Flat editorial illustration ... Concept to illustrate: a straight path",
        image_storage_key="renders/explainer/placeholder/v1.png",
        caption="軌道の直線化",
    )
    db.add(figure)
    await db.commit()
    await db.refresh(figure)
    return article, figure


async def test_regenerate_explainer_figure_enqueues_figure_job(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    _article, figure = await _mk_explainer(db_session, factories, user)

    resp = await client.post(
        f"/api/explainer-figures/{figure.id}/regenerate", json={"instruction": "もっと明るく"}
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = await db_session.get(Job, job_id)
    assert job is not None
    assert job.kind == "figure"
    assert job.payload["figure_kind"] == "explainer"
    assert job.payload["figure_id"] == str(figure.id)
    assert job.payload["instruction"] == "もっと明るく"


async def test_regenerate_explainer_figure_conflict_when_not_current(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    _article, figure = await _mk_explainer(db_session, factories, user, is_current=False)

    resp = await client.post(f"/api/explainer-figures/{figure.id}/regenerate", json={})
    assert resp.status_code == 409


async def test_regenerate_explainer_figure_404_for_other_users_figure(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, _uid = auth
    other = await factories.make_user(db_session)
    _article, figure = await _mk_explainer(db_session, factories, other)

    resp = await client.post(f"/api/explainer-figures/{figure.id}/regenerate", json={})
    assert resp.status_code == 404
