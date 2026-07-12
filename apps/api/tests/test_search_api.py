"""M1-12/M2-15: 横断検索 API(PY-SRCH-01〜05。plans/03 §15・§6.7、plans/11 §3〜§7)。

検索コーパスは plans/12-testing.md §11.1 の S1〜S7 をモデルにするが、S7(記事)は
`article_ctx` フィクスチャで `search_ctx` に追記する(M1 分の既存アサーションを壊さないため
基本 `search_ctx` には含めない — 下記「article 部」節)。以下の 2 点は実データで検証したうえで
調整している(deviations 参照):

- S2(「EMA teacher」の埋め込み文)は plans/12 の素朴な "ため EMA teacher を" 形だと
  実際の MeCab(IPADIC)が "EMA" を e/m/a の文字単位に誤分割し、クエリ「EMA teacher」が
  一切ヒットしない(実 PostgreSQL で確認済み)。plans/11 §1 自身の例文
  "EMA 教師(EMA teacher)"(括弧区切り)は正しく1トークンに分割されるため、本テストは
  この形を採用する。
- annotation の quote(引用スナップショット)は plans/11 §2.2(4)の決定で索引しない
  (body の索引と二重ヒットになるため)。plans/12 §11.2 の「transport paths → S1+S5(注釈
  quote)」という期待は、この決定と矛盾するため採用しない。本テストは、注釈のヒットは
  comment 本文(body)一致でのみ起きることを別クエリで検証する。
- 「最小二乗」→ S6(チャット)ヒットという plans/12 §11.2 の記載も、S6 のチャット本文
  (「1 回の reflow で経路がほぼ直線になります」)に「最小二乗」が現れないため再現しない。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import (
    Annotation,
    ArticleBlock,
    DocumentRevision,
    LibraryItem,
    Paper,
)
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.search.rebuild import rebuild_block_search_index
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession


async def _insert_comment_annotation(
    db: AsyncSession, *, library_item_id: str, color: str, body: str, anchor: dict[str, Any]
) -> str:
    """``factories.make_annotation`` の代替(このテストファイル限定のワークアラウンド)。

    ``annotations.quote`` は ``GENERATED ALWAYS AS (anchor->>'quote') STORED`` 列だが、
    ``factories.make_annotation`` は ORM ``Annotation(...)`` コンストラクタ経由で INSERT
    するため、``quote`` を明示していなくても SQLAlchemy が(``Computed()`` マーカーが無い
    ORM 定義のため)常に ``quote=NULL`` を INSERT 列に含めてしまい、Postgres が
    ``GeneratedAlwaysError`` で拒否する(実 DB で確認済み)。factories.py は所有範囲外
    (読み取り専用)のため、ここでは annotations ルータと同じ Core ``insert().values()``
    (quote 列を含めない)で直接書き込む。followups で M1-01 側に報告する。
    """
    annotation_id = str(uuid.uuid4())
    await db.execute(
        insert(Annotation).values(
            id=annotation_id,
            library_item_id=library_item_id,
            kind="comment",
            color=color,
            body=body,
            anchor=anchor,
        )
    )
    await db.flush()
    return annotation_id


# ---------------------------------------------------------------------------
# 検索コーパス(S1〜S6。§11 の corpus に対応)
# ---------------------------------------------------------------------------
S1_EN = "Rectified flow learns straight transport paths between two distributions."
S1_JA = "整流フロー(rectified flow)は 2 つの分布間の直線的な輸送経路を学習する。"
S2_EN = "We use an EMA teacher to stabilize distillation."
S2_JA = "EMA 教師(EMA teacher)を用いて蒸留を安定させる。"
S3_EN = "the training objective boils down to a least squares regression"
S3_JA = "学習目的は最小二乗回帰に帰着する"
S4_NOTE_BODY = "reflow の反復回数と直線性の関係を後で確認"
S5_ANNOTATION_BODY = "拡散モデルとの違いはここ"
S5_ANNOTATION_QUOTE = "straight transport paths"
S6_USER_MSG = "reflow は何回必要ですか?"
S6_ASSISTANT_MSG = "1 回の reflow で経路がほぼ直線になります"
BIBLIO_ABSTRACT_TERM = "diffusionbridgezzq"


def _document() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(id="blk-s1", type="paragraph", inlines=[Inline(t="text", v=S1_EN)]),
                ],
            ),
            Section(
                id="sec-2-1",
                heading=SectionHeading(number="2.1", title="Setup"),
                blocks=[
                    Block(id="blk-s3", type="paragraph", inlines=[Inline(t="text", v=S3_EN)]),
                ],
            ),
            Section(
                id="sec-3",
                heading=SectionHeading(number="3", title="Distillation"),
                blocks=[
                    Block(id="blk-s2", type="paragraph", inlines=[Inline(t="text", v=S2_EN)]),
                ],
            ),
        ],
    )


@pytest_asyncio.fixture
async def search_ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, factories: Any
) -> AsyncIterator[SimpleNamespace]:
    user = await upsert_user_by_email(
        db_session, f"search-{uuid.uuid4().hex}@example.com", provider="email"
    )
    paper = Paper(
        title="Flow Straight and Fast",
        authors=[{"name": "Xingchang Liu"}],
        abstract=f"We study generative modeling and {BIBLIO_ABSTRACT_TERM} phenomena.",
        visibility="private",
        owner_user_id=user.id,
        published_on=dt.date(2022, 9, 7),
        venue="ICLR 2023",
    )
    db_session.add(paper)
    await db_session.flush()

    content = _document()
    rev = DocumentRevision(
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="arxiv_html",
        content=content.model_dump(),
    )
    db_session.add(rev)
    await db_session.flush()
    paper.latest_revision_id = rev.id
    await rebuild_block_search_index(db_session, str(rev.id), content)

    item = LibraryItem(user_id=user.id, paper_id=paper.id, status="reading")
    db_session.add(item)
    await db_session.flush()

    tset = await factories.make_translation_set(
        db_session, revision=rev, style="natural", scope="shared", status="complete"
    )
    for block_id, text_ja in (("blk-s1", S1_JA), ("blk-s2", S2_JA), ("blk-s3", S3_JA)):
        await factories.make_translation_unit(
            db_session, translation_set=tset, block_id=block_id, text_ja=text_ja
        )

    note = await factories.make_note(
        db_session, library_item=item, title="メモ", body_md=S4_NOTE_BODY
    )

    annotation_id = await _insert_comment_annotation(
        db_session,
        library_item_id=str(item.id),
        color="idea",
        body=S5_ANNOTATION_BODY,
        anchor={
            "revision_id": str(rev.id),
            "block_id": "blk-s1",
            "start": None,
            "end": None,
            "quote": S5_ANNOTATION_QUOTE,
            "side": "source",
        },
    )

    thread = await factories.make_chat_thread(
        db_session, library_item=item, title="メイン", is_main=True
    )
    user_msg = await factories.make_chat_message(
        db_session, thread=thread, role="user", text_plain=S6_USER_MSG
    )
    assistant_msg = await factories.make_chat_message(
        db_session, thread=thread, role="assistant", text_plain=S6_ASSISTANT_MSG
    )

    await db_session.commit()

    token = await create_session(redis_client, user.id)
    client.cookies.set("yk_session", token)

    try:
        yield SimpleNamespace(
            user_id=str(user.id),
            item_id=str(item.id),
            paper_id=str(paper.id),
            revision_id=str(rev.id),
            note_id=str(note.id),
            annotation_id=annotation_id,
            thread_id=str(thread.id),
            user_msg_id=user_msg.id,
            assistant_msg_id=assistant_msg.id,
        )
    finally:
        await db_session.rollback()
        await purge_user(db_session, str(user.id))
        await db_session.commit()


# ---------------------------------------------------------------------------
# PY-SRCH-01: 日英クロス + 同一ブロック統合
# ---------------------------------------------------------------------------
async def test_ja_query_hits_translation_only(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": "整流フロー"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    hit = body["groups"][0]["hits"][0]
    assert hit["source"] == "body"
    assert hit["matched_in"] == ["translation"]
    assert hit["snippet_lang"] == "ja"


async def test_combined_query_merges_source_and_translation_hit(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": "rectified flow"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1  # 統合されて1件(重複カウントしない)
    hit = body["groups"][0]["hits"][0]
    assert hit["matched_in"] == ["source", "translation"]
    assert hit["snippet_lang"] == "en"  # source を含むので原文スニペット優先
    assert hit["target"]["kind"] == "viewer"
    assert hit["target"]["anchor"]["block_id"] == "blk-s1"


async def test_ema_teacher_combined_hit(client: AsyncClient, search_ctx: SimpleNamespace) -> None:
    resp = await client.get("/api/search", params={"q": "EMA teacher"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    hit = body["groups"][0]["hits"][0]
    assert hit["matched_in"] == ["source", "translation"]
    assert hit["target"]["anchor"]["block_id"] == "blk-s2"


async def test_english_source_only_hit(client: AsyncClient, search_ctx: SimpleNamespace) -> None:
    resp = await client.get("/api/search", params={"q": "least squares"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    hit = body["groups"][0]["hits"][0]
    assert hit["matched_in"] == ["source"]
    assert hit["snippet_lang"] == "en"
    assert '<mark class="alinea-search-hit">' in hit["snippet"]


async def test_japanese_translation_only_hit(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": "最小二乗"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    hit = body["groups"][0]["hits"][0]
    assert hit["matched_in"] == ["translation"]
    assert hit["snippet_lang"] == "ja"


async def test_english_stemming_matches_inflected_form(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    # S2 原文 "...to stabilize distillation." に対し語形変化クエリでヒットする(docs/09 §7.2)。
    resp = await client.get("/api/search", params={"q": "stabilizes"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["groups"][0]["hits"][0]["target"]["anchor"]["block_id"] == "blk-s2"


# ---------------------------------------------------------------------------
# PY-SRCH-02: ヒット源 4 種 + facets + グループ化
# ---------------------------------------------------------------------------
async def test_note_and_chat_sources_group_and_facets(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": "reflow"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["paper_count"] == 1
    assert body["facets"]["source"] == {"all": 2, "body": 0, "notes": 1, "chat": 1, "article": 0}
    assert len(body["groups"]) == 1
    group = body["groups"][0]
    assert group["hit_count"] == 2
    assert group["library_item"]["id"] == search_ctx.item_id
    sources = sorted(h["source"] for h in group["hits"])
    assert sources == ["chat", "note"]
    note_hit = next(h for h in group["hits"] if h["source"] == "note")
    assert note_hit["target"] == {
        "kind": "note",
        "library_item_id": search_ctx.item_id,
        "note_id": search_ctx.note_id,
    }
    chat_hit = next(h for h in group["hits"] if h["source"] == "chat")
    assert chat_hit["target"]["kind"] == "chat"
    assert chat_hit["target"]["thread_id"] == search_ctx.thread_id
    assert "Q:" in chat_hit["snippet"] or "A:" in chat_hit["snippet"]


# ---------------------------------------------------------------------------
# PY-SRCH-02 (article 部。M2-15): 記事ヒット・グループヘッダ・facets・source フィルタ
# (plans/12 §11.1 S7・§11.2「まっすぐ」)
# ---------------------------------------------------------------------------
S7_HEADING = "なぜ直線なのか"
S7_BODY = "整流フローを一言でいえば『まっすぐ流す』ことです。"


@pytest_asyncio.fixture
async def article_ctx(
    db_session: AsyncSession, search_ctx: SimpleNamespace, factories: Any
) -> SimpleNamespace:
    item = await db_session.get(LibraryItem, search_ctx.item_id)
    assert item is not None
    article = await factories.make_article(db_session, library_item=item, with_blocks=False)
    db_session.add_all(
        [
            ArticleBlock(
                article_id=str(article.id),
                position=0,
                type="heading",
                content={"heading": {"level": 2, "text": S7_HEADING}},
                text_plain=S7_HEADING,
                origin="ai",
            ),
            ArticleBlock(
                article_id=str(article.id),
                position=1,
                type="paragraph",
                content={"markdown": S7_BODY},
                text_plain=S7_BODY,
                origin="ai",
            ),
        ]
    )
    await db_session.commit()
    return SimpleNamespace(
        article_id=str(article.id), title=article.title, generated_at=article.generated_at
    )


async def test_article_source_hit_display_and_group_header(
    client: AsyncClient, article_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": "まっすぐ"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["facets"]["source"]["article"] == 1
    assert body["facets"]["source"]["body"] == 0
    group = body["groups"][0]
    assert group["hit_count"] == 1
    assert group["article"] == {
        "article_id": article_ctx.article_id,
        "title": article_ctx.title,
        "generated_at": article_ctx.generated_at.isoformat(),
    }
    hit = group["hits"][0]
    assert hit["source"] == "article"
    assert hit["matched_in"] is None
    assert hit["display"] == f"「{S7_HEADING}」セクション"
    assert hit["target"] == {
        "kind": "article",
        "library_item_id": group["library_item"]["id"],
        "article_block_id": hit["target"]["article_block_id"],
    }


async def test_article_source_filter_narrows_to_article_only(
    client: AsyncClient, article_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": "まっすぐ", "source": "article"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [h["source"] for h in body["groups"][0]["hits"]] == ["article"]

    resp2 = await client.get("/api/search", params={"q": "まっすぐ", "source": "body"})
    body2 = resp2.json()
    assert body2["groups"] == []
    # facets は絞り込み前の全ヒット集合(plans/11 §6.1)。
    assert body2["facets"]["source"]["article"] == 1


async def test_ja_query_combines_translation_and_article_hits(
    client: AsyncClient, article_ctx: SimpleNamespace
) -> None:
    # plans/12 §11.2: 「整流フロー」→ S1(訳文)+ S7(記事)。同一論文グループに 2 ヒット。
    resp = await client.get("/api/search", params={"q": "整流フロー"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    group = body["groups"][0]
    sources = sorted(h["source"] for h in group["hits"])
    assert sources == ["article", "body"]
    assert group["article"] is not None


async def test_group_without_article_hit_has_null_article_field(
    client: AsyncClient, article_ctx: SimpleNamespace
) -> None:
    # 記事コーパスに含まれない語(reflow)は article ヒットを生まないので article は null。
    resp = await client.get("/api/search", params={"q": "reflow"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["groups"][0]["article"] is None


async def test_source_filter_narrows_groups_but_not_facets(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": "reflow", "source": "notes"})
    assert resp.status_code == 200
    body = resp.json()
    # facets は絞り込み前の全ヒット集合(plans/11 §6.1)。
    assert body["facets"]["source"]["chat"] == 1
    assert body["facets"]["source"]["notes"] == 1
    group = body["groups"][0]
    assert [h["source"] for h in group["hits"]] == ["note"]


async def test_annotation_comment_hit_not_quote(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    # comment 本文(body)一致でヒットする(plans/11 §2.2(4): quote は索引しない)。
    resp = await client.get("/api/search", params={"q": "拡散モデル"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    hit = body["groups"][0]["hits"][0]
    assert hit["source"] == "annotation"
    assert hit["display"].startswith("注釈")
    assert hit["target"]["kind"] == "viewer"
    assert hit["target"]["anchor"]["block_id"] == "blk-s1"

    # quote 側の文言("straight transport paths")では注釈はヒットしない(索引対象外)。
    resp2 = await client.get("/api/search", params={"q": "transport paths"})
    body2 = resp2.json()
    assert body2["total"] == 1
    assert body2["groups"][0]["hits"][0]["source"] == "body"


async def test_no_hits_returns_zero_facets(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": "存在しない語XYZQ"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["paper_count"] == 0
    assert body["groups"] == []
    assert body["facets"]["source"] == {"all": 0, "body": 0, "notes": 0, "chat": 0, "article": 0}


async def test_biblio_hit_folds_into_body_source_with_null_anchor(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search", params={"q": BIBLIO_ABSTRACT_TERM})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    hit = body["groups"][0]["hits"][0]
    assert hit["source"] == "body"
    assert hit["display"] == "書誌"
    assert hit["target"]["kind"] == "viewer"
    assert hit["target"]["anchor"] is None


# ---------------------------------------------------------------------------
# PY-SRCH-03: target 形状・snippet サニタイズ
# ---------------------------------------------------------------------------
async def test_snippet_is_escaped_html(client: AsyncClient, search_ctx: SimpleNamespace) -> None:
    resp = await client.get("/api/search", params={"q": "least squares"})
    body = resp.json()
    snippet = body["groups"][0]["hits"][0]["snippet"]
    assert "<script>" not in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")


# ---------------------------------------------------------------------------
# PY-SRCH-04: preview + 論文内検索
# ---------------------------------------------------------------------------
async def test_preview_returns_top_hits_and_total(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get("/api/search/preview", params={"q": "reflow"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    for it in body["items"]:
        assert "library_item" in it
        assert it["library_item"]["id"] == search_ctx.item_id


async def test_in_paper_search_translation_hit(
    client: AsyncClient, search_ctx: SimpleNamespace
) -> None:
    resp = await client.get(
        f"/api/revisions/{search_ctx.revision_id}/search", params={"q": "整流フロー"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["block_id"] == "blk-s1"
    assert item["matched_in"] == ["translation"]
    assert '<mark class="alinea-search-hit">' in item["snippet"]


async def test_in_paper_search_source_hit(client: AsyncClient, search_ctx: SimpleNamespace) -> None:
    resp = await client.get(
        f"/api/revisions/{search_ctx.revision_id}/search", params={"q": "least squares"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["block_id"] == "blk-s3"
    assert body["items"][0]["matched_in"] == ["source"]


# ---------------------------------------------------------------------------
# PY-SRCH-05: アクセス制御(自分のライブラリのみ)
# ---------------------------------------------------------------------------
async def test_search_is_scoped_to_own_library(
    client: AsyncClient,
    bare_client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    search_ctx: SimpleNamespace,
) -> None:
    other_user = await upsert_user_by_email(
        db_session, f"search-other-{uuid.uuid4().hex}@example.com", provider="email"
    )
    other_paper = Paper(
        title="A Wholly Unrelated Paper",
        authors=[{"name": "Someone Else"}],
        abstract="",
        visibility="private",
        owner_user_id=other_user.id,
        published_on=dt.date(2021, 1, 1),
    )
    db_session.add(other_paper)
    await db_session.flush()
    other_content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Intro"),
                blocks=[
                    Block(
                        id="blk-other",
                        type="paragraph",
                        inlines=[
                            Inline(t="text", v="This concerns a least squares regression too.")
                        ],
                    )
                ],
            )
        ],
    )
    other_rev = DocumentRevision(
        paper_id=other_paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="arxiv_html",
        content=other_content.model_dump(),
    )
    db_session.add(other_rev)
    await db_session.flush()
    other_paper.latest_revision_id = other_rev.id
    await rebuild_block_search_index(db_session, str(other_rev.id), other_content)
    other_item = LibraryItem(user_id=other_user.id, paper_id=other_paper.id, status="reading")
    db_session.add(other_item)
    await db_session.flush()
    await db_session.commit()

    try:
        other_token = await create_session(redis_client, other_user.id)
        bare_client.cookies.set("yk_session", other_token)
        resp_other = await bare_client.get("/api/search", params={"q": "least squares"})
        assert resp_other.status_code == 200
        body_other = resp_other.json()
        assert body_other["total"] == 1
        assert body_other["groups"][0]["library_item"]["id"] == str(other_item.id)

        # 元ユーザー(search_ctx)は自分の分だけ(相手の "least squares" 文書は見えない)。
        resp_self = await client.get("/api/search", params={"q": "least squares"})
        body_self = resp_self.json()
        assert body_self["total"] == 1
        assert body_self["groups"][0]["library_item"]["id"] == search_ctx.item_id
    finally:
        await db_session.rollback()
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()


async def test_search_never_uses_foreign_reading_position_revision(
    client: AsyncClient,
    db_session: AsyncSession,
    search_ctx: SimpleNamespace,
    factories: Any,
) -> None:
    """壊れた読書位置から他ユーザーの私有本文を検索結果へ出さない。"""
    other_user = await upsert_user_by_email(
        db_session, f"search-leak-{uuid.uuid4().hex}@example.com", provider="email"
    )
    other_paper = Paper(
        title="Private foreign search corpus",
        authors=[],
        visibility="private",
        owner_user_id=other_user.id,
    )
    db_session.add(other_paper)
    await db_session.flush()
    other_content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-secret",
                heading=SectionHeading(number="1", title="Secret"),
                blocks=[
                    Block(
                        id="blk-secret",
                        type="paragraph",
                        inlines=[Inline(t="text", v="crosspaperleaksecret")],
                    )
                ],
            )
        ],
    )
    other_revision = DocumentRevision(
        paper_id=other_paper.id,
        parser_version="test-1",
        quality_level="B",
        source_format="arxiv_html",
        content=other_content.model_dump(),
    )
    db_session.add(other_revision)
    await db_session.flush()
    other_paper.latest_revision_id = other_revision.id
    await rebuild_block_search_index(db_session, str(other_revision.id), other_content)
    foreign_translation_set = await factories.make_translation_set(
        db_session,
        revision=other_revision,
        style="natural",
        scope="shared",
        status="complete",
    )
    await factories.make_translation_unit(
        db_session,
        translation_set=foreign_translation_set,
        block_id="blk-secret",
        text_ja="crosspapertranslationleaksecret",
    )
    item = await db_session.get(LibraryItem, search_ctx.item_id)
    assert item is not None
    item.reading_position = {
        "revision_id": str(other_revision.id),
        "block_id": "blk-secret",
    }
    await db_session.commit()

    try:
        response = await client.get("/api/search", params={"q": "crosspaperleaksecret"})

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0
        assert response.json()["groups"] == []

        translation_response = await client.get(
            "/api/search", params={"q": "crosspapertranslationleaksecret"}
        )
        assert translation_response.status_code == 200, translation_response.text
        assert translation_response.json()["total"] == 0
        assert translation_response.json()["groups"] == []

        # note ヒットのグループ要約も foreign quality/last_position を採用しない。
        note_response = await client.get("/api/search", params={"q": "反復回数"})
        assert note_response.status_code == 200, note_response.text
        note_summary = note_response.json()["groups"][0]["library_item"]
        assert note_summary["quality_level"] == "A"
        assert note_summary["last_position"] is None

        # reading_position が無い場合も、壊れた latest_revision_id は採用しない。
        item.reading_position = None
        own_paper = await db_session.get(Paper, search_ctx.paper_id)
        assert own_paper is not None
        own_paper.latest_revision_id = other_revision.id
        await db_session.commit()

        latest_response = await client.get(
            "/api/search", params={"q": "crosspapertranslationleaksecret"}
        )
        assert latest_response.status_code == 200, latest_response.text
        assert latest_response.json()["total"] == 0
        assert latest_response.json()["groups"] == []
    finally:
        await db_session.rollback()
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()


async def test_search_invalid_reading_position_revision_does_not_500(
    client: AsyncClient,
    db_session: AsyncSession,
    search_ctx: SimpleNamespace,
) -> None:
    item = await db_session.get(LibraryItem, search_ctx.item_id)
    assert item is not None
    item.reading_position = {"revision_id": "not-a-uuid", "block_id": "blk-s1"}
    await db_session.commit()

    response = await client.get("/api/search", params={"q": "rectified flow"})

    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# 検証エラー(422)
# ---------------------------------------------------------------------------
async def test_blank_query_is_422(client: AsyncClient, search_ctx: SimpleNamespace) -> None:
    resp = await client.get("/api/search", params={"q": "   "})
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_too_long_query_is_422(client: AsyncClient, search_ctx: SimpleNamespace) -> None:
    resp = await client.get("/api/search", params={"q": "a" * 201})
    assert resp.status_code == 422
