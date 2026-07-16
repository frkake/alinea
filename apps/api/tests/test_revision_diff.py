"""S10: GET /api/papers/{paper_id}/revisions/diff — バージョン間ブロック差分。

決定的な構造差分(LLM 不使用)を stats + 変更ブロック列で返す。所属外リビジョン拒否・
アクセス制御・不存在リビジョンを確認する。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from alinea_api.services.session_service import COOKIE_NAME, create_session
from alinea_api.services.user_service import purge_user
from factories import make_library_item, make_paper, make_revision, make_user
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _content(blocks: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "quality_level": "A",
        "sections": [
            {
                "id": "sec-1",
                "heading": {"number": "1", "title": "Introduction"},
                "blocks": [
                    {"id": bid, "type": "paragraph", "inlines": [{"t": "text", "v": text}]}
                    for bid, text in blocks
                ],
            }
        ],
    }


_OLD = _content(
    [
        ("blk-a", "Alpha unchanged text"),
        ("blk-b", "Beta original wording"),
        ("blk-c", "Gamma removed entirely"),
    ]
)
_NEW = _content(
    [
        ("blk-a", "Alpha unchanged text"),
        ("blk-b", "Beta rewritten wording"),
        ("blk-d", "Delta freshly added"),
    ]
)


@pytest_asyncio.fixture
async def ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[SimpleNamespace]:
    user = await make_user(db_session, email=f"diff-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, owner=user, visibility="private")
    old_rev = await make_revision(
        db_session, paper=paper, content=_OLD, source_version="v1", set_latest=False
    )
    new_rev = await make_revision(
        db_session, paper=paper, content=_NEW, source_version="v2", set_latest=True
    )
    await make_library_item(db_session, user=user, paper=paper, status="reading")
    await db_session.commit()
    user_id = str(user.id)

    token = await create_session(redis_client, user_id)
    client.cookies.set(COOKIE_NAME, token)
    try:
        yield SimpleNamespace(
            user=user, user_id=user_id, paper=paper, old_rev=old_rev, new_rev=new_rev
        )
    finally:
        await db_session.commit()
        await purge_user(db_session, user_id)
        await db_session.commit()


async def test_revision_diff_returns_stats_and_changes(
    client: AsyncClient, ctx: SimpleNamespace
) -> None:
    r = await client.get(
        f"/api/papers/{ctx.paper.id}/revisions/diff",
        params={"from": str(ctx.old_rev.id), "to": str(ctx.new_rev.id)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stats"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 1}

    by_status = {c["status"]: c for c in body["changes"]}
    assert set(by_status) == {"added", "removed", "changed"}
    assert by_status["changed"]["block_id"] == "blk-b"
    assert by_status["changed"]["old_text"] == "Beta original wording"
    assert by_status["changed"]["new_text"] == "Beta rewritten wording"
    assert by_status["removed"]["block_id"] == "blk-c"
    assert by_status["removed"]["new_text"] is None
    assert by_status["added"]["block_id"] == "blk-d"
    assert by_status["added"]["old_text"] is None


async def test_revision_diff_rejects_revision_from_another_paper(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    other_paper = await make_paper(db_session, owner=ctx.user, visibility="private")
    other_rev = await make_revision(db_session, paper=other_paper, set_latest=True)
    await db_session.commit()

    r = await client.get(
        f"/api/papers/{ctx.paper.id}/revisions/diff",
        params={"from": str(ctx.old_rev.id), "to": str(other_rev.id)},
    )
    assert r.status_code == 404, r.text


async def test_revision_diff_missing_revision_is_404(
    client: AsyncClient, ctx: SimpleNamespace
) -> None:
    r = await client.get(
        f"/api/papers/{ctx.paper.id}/revisions/diff",
        params={"from": str(ctx.old_rev.id), "to": str(uuid.uuid4())},
    )
    assert r.status_code == 404, r.text


async def test_revision_diff_requires_access(
    db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    from alinea_api.main import app
    from alinea_api.redis_client import get_redis
    from httpx import ASGITransport

    outsider = await make_user(db_session, email=f"out-{uuid.uuid4().hex}@example.com")
    await db_session.commit()
    outsider_id = str(outsider.id)
    token = await create_session(get_redis(), outsider_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        cookies={COOKIE_NAME: token},
        trust_env=False,
    ) as outsider_client:
        r = await outsider_client.get(
            f"/api/papers/{ctx.paper.id}/revisions/diff",
            params={"from": str(ctx.old_rev.id), "to": str(ctx.new_rev.id)},
        )
    assert r.status_code == 404, r.text
    await purge_user(db_session, outsider_id)
    await db_session.commit()
