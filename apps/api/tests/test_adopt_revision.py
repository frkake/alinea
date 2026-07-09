"""M1-22 (c): POST /api/library-items/{id}/adopt-revision(plans/03 §6.8)。

PY-ING-07 の adopt-revision 部: 既存の別リビジョンへ切替え、リアンカー結果
``{moved, unplaced}`` を返す。「新しいバージョンがあります」バナー・B→A 昇格提案の適用の
いずれも本エンドポイントが唯一の適用経路(自動切替はしない。P6)。
PY-ANN-02 の API 経由確認(block_id 引き継ぎ/quote 探索/失敗分の orphaned=true)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from alinea_api.services.session_service import COOKIE_NAME, create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import Paper
from alinea_core.document.blocks import DocumentContent
from alinea_core.search.rebuild import rebuild_block_search_index
from factories import make_annotation, make_library_item, make_paper, make_revision, make_user
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


_OLD_CONTENT = _content(
    [
        ("blk-keep", "Keep me across revisions"),
        ("blk-move", "Move me via quote search"),
        ("blk-lost", "Will be lost forever"),
    ]
)
_NEW_CONTENT = _content(
    [
        ("blk-keep", "Keep me across revisions"),
        ("blk-moved-new", "Move me via quote search"),
    ]
)


@pytest_asyncio.fixture
async def ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[SimpleNamespace]:
    user = await make_user(db_session, email=f"adopt-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, owner=user, visibility="private")
    old_rev = await make_revision(
        db_session,
        paper=paper,
        quality_level="B",
        source_format="pdf",
        parser_version="pdf-1.0.0",
        content=_OLD_CONTENT,
        set_latest=True,
    )
    new_rev = await make_revision(
        db_session,
        paper=paper,
        quality_level="A",
        source_format="latex",
        parser_version="latex-1.0.0",
        content=_NEW_CONTENT,
        set_latest=False,
    )
    # 実運用では ingest の structuring 段で構築済み(plans/11 §9 フック#1)。ここでは factory が
    # 索引を作らないため明示的に構築する(quote 探索がこの索引を読むため)。
    await rebuild_block_search_index(
        db_session, str(new_rev.id), DocumentContent.model_validate(_NEW_CONTENT)
    )
    li = await make_library_item(db_session, user=user, paper=paper, status="reading")
    await db_session.commit()
    user_id = str(user.id)  # rollback 後の属性アクセス(greenlet 事故)を避けるため先に確定

    token = await create_session(redis_client, user_id)
    client.cookies.set(COOKIE_NAME, token)
    try:
        yield SimpleNamespace(
            user=user,
            user_id=user_id,
            paper=paper,
            old_rev=old_rev,
            new_rev=new_rev,
            library_item=li,
        )
    finally:
        await db_session.commit()
        await purge_user(db_session, user_id)
        await db_session.commit()


def _anchor(revision_id: str, block_id: str, quote: str) -> dict[str, Any]:
    return {
        "revision_id": revision_id,
        "block_id": block_id,
        "start": 0,
        "end": len(quote),
        "quote": quote,
        "side": "source",
    }


async def test_adopt_revision_switches_current_and_reanchors(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    ann_keep = await make_annotation(
        db_session,
        library_item=ctx.library_item,
        anchor=_anchor(str(ctx.old_rev.id), "blk-keep", "Keep me across revisions"),
    )
    ann_moved = await make_annotation(
        db_session,
        library_item=ctx.library_item,
        anchor=_anchor(str(ctx.old_rev.id), "blk-move", "Move me via quote search"),
    )
    ann_lost = await make_annotation(
        db_session,
        library_item=ctx.library_item,
        anchor=_anchor(str(ctx.old_rev.id), "blk-lost", "Will be lost forever"),
    )
    await db_session.commit()

    r = await client.post(
        f"/api/library-items/{ctx.library_item.id}/adopt-revision",
        json={"revision_id": str(ctx.new_rev.id)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reanchor"] == {"moved": 2, "unplaced": 1}
    assert body["library_item"]["id"] == str(ctx.library_item.id)
    assert body["library_item"]["quality_level"] == "A"

    await db_session.refresh(ctx.paper)
    assert str(ctx.paper.latest_revision_id) == str(ctx.new_rev.id)

    await db_session.refresh(ann_keep)
    assert ann_keep.anchor["revision_id"] == str(ctx.new_rev.id)
    assert ann_keep.anchor["block_id"] == "blk-keep"
    assert ann_keep.orphaned is False

    await db_session.refresh(ann_moved)
    assert ann_moved.anchor["revision_id"] == str(ctx.new_rev.id)
    assert ann_moved.anchor["block_id"] == "blk-moved-new"
    assert ann_moved.orphaned is False

    await db_session.refresh(ann_lost)
    assert ann_lost.orphaned is True  # 消えない(P3)
    assert ann_lost.anchor["revision_id"] == str(ctx.old_rev.id)


async def test_adopt_revision_rejects_revision_from_another_paper(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    other_paper = await make_paper(db_session, owner=ctx.user, visibility="private")
    other_rev = await make_revision(db_session, paper=other_paper, set_latest=True)
    await db_session.commit()

    r = await client.post(
        f"/api/library-items/{ctx.library_item.id}/adopt-revision",
        json={"revision_id": str(other_rev.id)},
    )
    assert r.status_code == 422, r.text


async def test_adopt_revision_requires_ownership(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    other_user = await make_user(db_session)
    other_item = await make_library_item(db_session, user=other_user, paper=ctx.paper)
    await db_session.commit()

    r = await client.post(
        f"/api/library-items/{other_item.id}/adopt-revision",
        json={"revision_id": str(ctx.new_rev.id)},
    )
    assert r.status_code == 404, r.text


async def test_adopt_revision_is_idempotent_when_already_current(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace
) -> None:
    r1 = await client.post(
        f"/api/library-items/{ctx.library_item.id}/adopt-revision",
        json={"revision_id": str(ctx.old_rev.id)},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["reanchor"] == {"moved": 0, "unplaced": 0}

    paper = await db_session.get(Paper, ctx.paper.id)
    assert paper is not None
    assert str(paper.latest_revision_id) == str(ctx.old_rev.id)
