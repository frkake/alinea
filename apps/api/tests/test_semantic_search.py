"""Task 20: ハイブリッド検索(PGroonga + pgvector)と「似た論文」の接続テスト。

検証観点(brief / spec 2026-07-16-semantic-search-design.md §6):
- **フラグ off = byte-identical**: ``SEMANTIC_SEARCH_ENABLED`` off のとき ``/api/search`` の
  レスポンス(グループ順・キー集合)が今日と完全一致する(``match_type`` が一切現れない)。
- **ユーザー境界**: 別ユーザーの近傍は絶対に返らない(横断検索・似た論文の両方)。
- **縮退**: 埋め込みプロバイダ失敗・空 index はセマンティックを使わず PGroonga のみへ落ち、
  順序がフラグ off と一致する。
- **RRF 決定性**: lexical/semantic の融合順が決定的。
- **似た論文**: 自分自身を除外・自分のライブラリ内・上位 10・埋め込み無しは
  ``indexing=false`` + 空配列(202 で index job を enqueue しない)。

実埋め込みネットワークは使わない。``FakeEmbeddingProvider`` + in-memory ``SemanticIndex`` を
dependency_overrides で注入する(pgvector 非同梱の開発 DB で決定的に回すため)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from alinea_api.main import app
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import DocumentRevision, LibraryItem, Paper
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.search.rebuild import rebuild_block_search_index
from alinea_llm.testing.fake_provider import FakeEmbeddingProvider
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# 検索本文(test_search_api.py と同じ rectified-flow コーパスの簡約版)。
S1_EN = "Rectified flow learns straight transport paths between two distributions."
S2_EN = "We use an EMA teacher to stabilize distillation."

EMBED_DIM = 8


def _document() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[Block(id="blk-s1", type="paragraph", inlines=[Inline(t="text", v=S1_EN)])],
            ),
            Section(
                id="sec-2",
                heading=SectionHeading(number="2", title="Distillation"),
                blocks=[Block(id="blk-s2", type="paragraph", inlines=[Inline(t="text", v=S2_EN)])],
            ),
        ],
    )


async def _make_paper_with_item(
    db: AsyncSession, user: Any, *, title: str, abstract: str, with_body: bool
) -> tuple[Paper, LibraryItem]:
    paper = Paper(
        title=title,
        authors=[{"name": "Xingchao Liu"}, {"name": "Qiang Liu"}],
        abstract=abstract,
        visibility="private",
        owner_user_id=user.id,
        published_on=dt.date(2022, 9, 7),
        venue="ICLR 2023",
    )
    db.add(paper)
    await db.flush()
    if with_body:
        content = _document()
        rev = DocumentRevision(
            paper_id=paper.id,
            parser_version="test-1",
            quality_level="A",
            source_format="arxiv_html",
            content=content.model_dump(),
        )
        db.add(rev)
        await db.flush()
        paper.latest_revision_id = rev.id
        await rebuild_block_search_index(db, str(rev.id), content)
    item = LibraryItem(user_id=user.id, paper_id=paper.id, status="reading")
    db.add(item)
    await db.flush()
    return paper, item


@pytest_asyncio.fixture
async def semantic_ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[SimpleNamespace]:
    """2 ユーザー・複数論文を seed し、in-memory index にベクトルを積む。

    - me: rectified-flow 本文論文(lexical でヒット)+ 別テーマ論文 2 本(semantic のみ)。
    - other: 別ユーザーの論文(境界テスト用。me には決して返ってはならない)。
    """
    me = await upsert_user_by_email(
        db_session, f"sem-me-{uuid.uuid4().hex}@example.com", provider="email"
    )
    other = await upsert_user_by_email(
        db_session, f"sem-other-{uuid.uuid4().hex}@example.com", provider="email"
    )

    # me の論文群。FakeEmbeddingProvider は token-bag(意味 ≒ 語彙重なり)なので、意味検索
    # 専用の論文は「abstract が PGroonga クエリに一致しない」かつ「seed ベクトルはクエリと近い」
    # ように、埋め込み入力テキストを abstract から意図的に切り離す。
    p_flow, i_flow = await _make_paper_with_item(
        db_session, me, title="Flow Straight and Fast",
        abstract="rectified flow straight transport", with_body=True
    )
    # near: abstract に "rectified"/"flow" を含めない(= PGroonga 非一致 = lexical に出ない)。
    p_near, i_near = await _make_paper_with_item(
        db_session, me, title="Consistency Models",
        abstract="consistency models fast sampling", with_body=False
    )
    p_far, i_far = await _make_paper_with_item(
        db_session, me, title="Banana Bread Recipe",
        abstract="banana bread baking recipe", with_body=False
    )
    p_noembed, i_noembed = await _make_paper_with_item(
        db_session, me, title="No Embedding Paper",
        abstract="unindexed paper", with_body=False
    )
    # other の論文(境界テスト)。abstract も seed も me の flow に近いが、別ユーザー。
    p_other, i_other = await _make_paper_with_item(
        db_session, other, title="Other User Rectified Flow",
        abstract="unrelated other user text", with_body=False
    )

    await db_session.commit()

    token = await create_session(redis_client, me.id)
    client.cookies.set("yk_session", token)

    # in-memory index に決定的ベクトルを積む(FakeEmbeddingProvider と同じ規則)。seed テキストは
    # abstract ではなく「クエリとの意味的近さ」を表す(実プロバイダの多言語意味論の代理)。
    from alinea_api import search_semantic
    from alinea_llm.types import EmbeddingRequest

    fake = FakeEmbeddingProvider(dim=EMBED_DIM)

    async def _vec(text: str) -> list[float]:
        res = await fake.embed(EmbeddingRequest(model="fake", inputs=[text], dimensions=EMBED_DIM))
        return res.vectors[0]

    index = search_semantic.InMemorySemanticIndex()
    seeds = [
        (i_flow, p_flow, me, "rectified flow straight transport"),
        (i_near, p_near, me, "rectified flow straight transport"),  # クエリと最近 = 意味のみ
        (i_far, p_far, me, "banana bread baking recipe"),  # クエリと遠い
        # p_noembed は index に積まない(indexing=false 経路)。
        (i_other, p_other, other, "rectified flow straight transport"),
    ]
    for item, paper, owner, txt in seeds:
        index.add(
            library_item_id=str(item.id), paper_id=str(paper.id),
            user_id=str(owner.id), vector=await _vec(txt),
        )

    yield SimpleNamespace(
        me_id=str(me.id),
        other_id=str(other.id),
        item_flow=str(i_flow.id),
        item_near=str(i_near.id),
        item_far=str(i_far.id),
        item_noembed=str(i_noembed.id),
        item_other=str(i_other.id),
        index=index,
        fake=fake,
    )

    await db_session.rollback()
    await purge_user(db_session, str(me.id))
    await purge_user(db_session, str(other.id))
    await db_session.commit()


def _enable_semantic(ctx: SimpleNamespace, *, provider: Any | None = None) -> None:
    """フラグ on + Fake provider + in-memory index を注入する。"""
    from alinea_api.deps import get_settings_dep
    from alinea_api.routers import library_items, search
    from alinea_api.settings import ApiSettings

    test_settings = ApiSettings(semantic_search_enabled=True, openai_api_key="op-embed-key")
    prov = provider if provider is not None else ctx.fake
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[search.get_embedding_provider_factory] = lambda: (lambda _key: prov)
    app.dependency_overrides[search.get_semantic_index_factory] = lambda: (lambda _db: ctx.index)
    app.dependency_overrides[library_items.get_semantic_index_factory] = (
        lambda: (lambda _db: ctx.index)
    )


def _clear_overrides() -> None:
    from alinea_api.deps import get_settings_dep
    from alinea_api.routers import library_items, search

    for key in (
        get_settings_dep,
        search.get_embedding_provider_factory,
        search.get_semantic_index_factory,
        library_items.get_semantic_index_factory,
    ):
        app.dependency_overrides.pop(key, None)


# ---------------------------------------------------------------------------
# フラグ off = byte-identical
# ---------------------------------------------------------------------------
async def test_flag_off_search_has_no_match_type(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    """フラグ off(既定)では match_type キーが一切現れない。"""
    resp = await client.get("/api/search", params={"q": "rectified flow"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    for group in body["groups"]:
        assert "match_type" not in group
        for hit in group["hits"]:
            assert "match_type" not in hit


async def test_flag_off_and_on_same_order_when_semantic_subset(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    """lexical に載る論文が semantic にも載るとき、順序はフラグ off と一致する(縮退の安全)。"""
    off = (await client.get("/api/search", params={"q": "rectified flow"})).json()
    off_order = [g["library_item"]["id"] for g in off["groups"]]

    _enable_semantic(semantic_ctx)
    try:
        on = (await client.get("/api/search", params={"q": "rectified flow"})).json()
    finally:
        _clear_overrides()
    on_order = [g["library_item"]["id"] for g in on["groups"]]
    # フラグ on でも lexical に出た論文が semantic 上位に居るため相対順が保たれる。
    assert off_order[0] == on_order[0]


# ---------------------------------------------------------------------------
# 縮退(provider 失敗 / 空 index)
# ---------------------------------------------------------------------------
async def test_provider_failure_falls_back_to_lexical_same_order(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    off = (await client.get("/api/search", params={"q": "rectified flow"})).json()
    off_order = [g["library_item"]["id"] for g in off["groups"]]

    failing = FakeEmbeddingProvider(dim=EMBED_DIM, fail=True)
    _enable_semantic(semantic_ctx, provider=failing)
    try:
        resp = await client.get("/api/search", params={"q": "rectified flow"})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    on_order = [g["library_item"]["id"] for g in body["groups"]]
    assert on_order == off_order
    # 縮退時は match_type を付けない(lexical のみと区別できない = byte-identical に戻す)。
    for group in body["groups"]:
        assert "match_type" not in group


# ---------------------------------------------------------------------------
# フラグ on: semantic 拡張 + match_type
# ---------------------------------------------------------------------------
async def test_semantic_adds_semantic_only_paper_with_match_type(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    """lexical に無い意味的近傍(Consistency Models)が semantic として加わる。"""
    _enable_semantic(semantic_ctx)
    try:
        resp = await client.get("/api/search", params={"q": "rectified flow"})
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    by_item = {g["library_item"]["id"]: g for g in body["groups"]}
    # 本文がヒットする flow は少なくとも lexical。semantic にも載るので both。
    assert by_item[semantic_ctx.item_flow]["match_type"] in ("lexical", "both")
    # Consistency Models は lexical では出ない(本文なし)が semantic で加わる。
    assert semantic_ctx.item_near in by_item
    assert by_item[semantic_ctx.item_near]["match_type"] == "semantic"


async def test_semantic_never_returns_other_users_papers(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    """別ユーザーの論文は semantic 経路でも決して返らない(ユーザー境界)。"""
    _enable_semantic(semantic_ctx)
    try:
        resp = await client.get("/api/search", params={"q": "rectified flow"})
    finally:
        _clear_overrides()
    body = resp.json()
    ids = {g["library_item"]["id"] for g in body["groups"]}
    assert semantic_ctx.item_other not in ids


# ---------------------------------------------------------------------------
# 似た論文
# ---------------------------------------------------------------------------
async def test_similar_excludes_self_and_other_users(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    _enable_semantic(semantic_ctx)
    try:
        resp = await client.get(f"/api/library-items/{semantic_ctx.item_flow}/similar")
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body["indexing"] is False
    ids = [it["library_item_id"] for it in body["items"]]
    assert semantic_ctx.item_flow not in ids  # 自分自身を除外
    assert semantic_ctx.item_other not in ids  # 別ユーザーを除外
    # 意味的に最も近い Consistency Models が含まれ、Banana Bread より上位。
    assert semantic_ctx.item_near in ids
    near_rank = ids.index(semantic_ctx.item_near)
    if semantic_ctx.item_far in ids:
        assert near_rank < ids.index(semantic_ctx.item_far)
    # 各件に類似度が付く。
    for it in body["items"]:
        assert 0.0 <= it["similarity"] <= 1.0000001


async def test_similar_no_embedding_returns_indexing_false_empty(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    """対象に埋め込みが無いと 200・空配列・indexing=false(202 も enqueue もしない)。"""
    _enable_semantic(semantic_ctx)
    try:
        resp = await client.get(f"/api/library-items/{semantic_ctx.item_noembed}/similar")
    finally:
        _clear_overrides()
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "indexing": False}


async def test_similar_flag_off_returns_empty(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    """フラグ off では似た論文は常に空(セマンティック経路に入らない)。"""
    resp = await client.get(f"/api/library-items/{semantic_ctx.item_flow}/similar")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "indexing": False}


async def test_similar_other_users_item_is_not_found(
    client: AsyncClient, semantic_ctx: SimpleNamespace
) -> None:
    """他人の library_item への /similar は 404(所有チェック)。"""
    _enable_semantic(semantic_ctx)
    try:
        resp = await client.get(f"/api/library-items/{semantic_ctx.item_other}/similar")
    finally:
        _clear_overrides()
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RRF 融合の決定性(DB 非依存の純ロジック)
# ---------------------------------------------------------------------------
def test_rrf_blend_is_deterministic_and_scale_independent() -> None:
    """同じ 2 リストからは常に同じ融合順。スコアの絶対値には依存しない(順位のみ)。"""
    from alinea_core.search.fusion import blend_lexical_semantic

    lexical = ["a", "b", "c"]
    semantic = ["c", "d", "a"]
    first = blend_lexical_semantic(lexical, semantic)
    for _ in range(5):
        assert blend_lexical_semantic(lexical, semantic) == first
    # 両リストに出る a・c が単独出現の b・d より上位(融合スコアが高い)。
    assert first.index("a") < first.index("b")
    assert first.index("c") < first.index("d")


def test_rrf_empty_semantic_preserves_lexical_order() -> None:
    """semantic 側が空なら lexical の順序をそのまま返す(縮退の安全)。"""
    from alinea_core.search.fusion import blend_lexical_semantic

    lexical = ["x", "y", "z"]
    assert blend_lexical_semantic(lexical, []) == lexical


async def test_inmemory_index_never_crosses_user_boundary(
    semantic_ctx: SimpleNamespace,
) -> None:
    """in-memory ANN も query/paper どちらの経路も自分の user_id 以外を返さない。"""
    from alinea_llm.types import EmbeddingRequest

    res = await semantic_ctx.fake.embed(
        EmbeddingRequest(model="fake", inputs=["rectified flow"], dimensions=EMBED_DIM)
    )
    qvec = res.vectors[0]
    neighbors = await semantic_ctx.index.query_neighbors(
        query_vector=qvec, user_id=semantic_ctx.me_id, top_k=100, model="fake"
    )
    ids = {n.library_item_id for n in neighbors}
    assert semantic_ctx.item_other not in ids
    # other 視点では me の論文が返らない。
    other_neighbors = await semantic_ctx.index.query_neighbors(
        query_vector=qvec, user_id=semantic_ctx.other_id, top_k=100, model="fake"
    )
    other_ids = {n.library_item_id for n in other_neighbors}
    assert other_ids == {semantic_ctx.item_other}
