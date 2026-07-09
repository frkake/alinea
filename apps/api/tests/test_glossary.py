"""M1-14 glossaries API テスト(plans/03 §7.9・plans/06 §8)。

- PY-GLS-01: 3 層用語集 CRUD・適用優先度(paper > user > global)・global への書き込み 403・
  promote で user 複製。
- PY-GLS-02: 訳語変更の dry_run=true(影響ブロック数のみ・副作用なし)/false(影響ブロックのみの
  再翻訳ジョブ。全文再翻訳が走らないこと。state=edited のブロックは除外 — PY-TR-09 と対をなす)。

用語集は語彙帳(英語学習)とは別物(docs/03 §7 注記)。認証は dev 相当のセッションクッキーを
直接発行して得る(既存 M0 パターンに合わせる)。

注意: `glossaries`/`glossary_terms(scope=global)` は特定ユーザーに属さず、他テスト
(他エージェント並走分含む)が残した行が DB に残存し得る。本ファイルの GET 系検証は件数の
厳密一致に依存せず、各テストが自分で作った一意な語(ランダム英字トークン)だけを抽出して
検証する。
"""

from __future__ import annotations

import random
import string
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.services.session_service import COOKIE_NAME, create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import (
    DocumentRevision,
    Glossary,
    GlossaryTerm,
    Job,
    Paper,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.search.rebuild import rebuild_block_search_index
from alinea_core.translation.glossary import build_snapshot
from factories import (
    make_library_item,
    make_paper,
    make_translation_set,
    make_translation_unit,
    make_user,
)
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _random_term() -> str:
    """PGroonga のトークナイズに適した純英字のランダム語(他テストとの衝突を避ける)。"""
    return "".join(random.choices(string.ascii_lowercase, k=10))


@pytest.fixture
def term() -> str:
    return _random_term()


def _p(block_id: str, text: str) -> Block:
    return Block(id=block_id, type="paragraph", inlines=[Inline(t="text", v=text)])


def _make_document(term: str) -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="2", title="Method"),
                blocks=[
                    _p("blk-p1", f"Knowledge {term} transfers a teacher's knowledge."),
                    _p("blk-p2", f"We use {term} to compress the model further."),
                    _p("blk-p3", f"This paragraph is about {term} as well."),
                    _p("blk-p4", "We compare with baseline models instead."),
                    _p("blk-pb", f"This paragraph mentions non{term} techniques."),
                    Block(
                        id="blk-ref1",
                        type="reference_entry",
                        raw=f"Hinton, G. {term.capitalize()} of knowledge, 2015.",
                    ),
                ],
            )
        ],
    )


async def _make_revision(
    db: AsyncSession, *, paper: Paper, content: DocumentContent
) -> DocumentRevision:
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=str(paper.id),
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(),
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    await rebuild_block_search_index(db, str(revision.id), content)
    return revision


async def _login(client: AsyncClient, redis_client: Any, user: User) -> None:
    token = await create_session(redis_client, str(user.id))
    client.cookies.set(COOKIE_NAME, token)


@pytest_asyncio.fixture
async def ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, term: str
) -> AsyncIterator[SimpleNamespace]:
    user = await make_user(db_session, email=f"gls-{uuid.uuid4().hex}@example.com")
    # private にして purge_user(user) 一発で papers/revisions/翻訳/用語集をカスケード削除する。
    paper = await make_paper(db_session, owner=user, visibility="private")
    content = _make_document(term)
    revision = await _make_revision(db_session, paper=paper, content=content)
    li = await make_library_item(db_session, user=user, paper=paper, status="reading")
    await db_session.commit()
    user_id = str(user.id)  # rollback 後の属性アクセス(greenlet 事故)を避けるため先に確定

    await _login(client, redis_client, user)
    try:
        yield SimpleNamespace(
            user=user, user_id=user_id, paper=paper, revision=revision, library_item=li
        )
    finally:
        # 直前の読み取りで開いたトランザクションを commit で終端する(rollback は全属性を
        # expire し、対象ユーザーが既にセッションから見えなくなる場合に greenlet エラーを
        # 誘発するため使わない)。
        await db_session.commit()
        await purge_user(db_session, user_id)
        await db_session.commit()


async def _insert_global_term(
    db: AsyncSession, *, source_term: str, target_term: str, policy: str = "translate"
) -> GlossaryTerm:
    glossary = Glossary(id=str(uuid.uuid4()), scope="global", name="test seed")
    db.add(glossary)
    await db.flush()
    gterm = GlossaryTerm(
        id=str(uuid.uuid4()),
        glossary_id=str(glossary.id),
        source_term=source_term,
        target_term=target_term,
        policy=policy,
    )
    db.add(gterm)
    await db.flush()
    await db.commit()
    return gterm


async def _delete_glossary(db: AsyncSession, glossary_id: str) -> None:
    glossary = await db.get(Glossary, glossary_id)
    if glossary is not None:
        await db.delete(glossary)  # glossary_terms へ CASCADE
        await db.commit()


# ---------------------------------------------------------------------------
# PY-GLS-01: 3 層 CRUD・適用優先度・global 403・promote
# ---------------------------------------------------------------------------
async def test_three_layer_crud_priority_and_promote(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace, term: str
) -> None:
    global_term = await _insert_global_term(db_session, source_term=term, target_term="蒸留")
    try:
        # GET scope=user: global は常に含む(読み取り専用)。他テスト由来の pollution は無視し、
        # 自分が作った一意語だけを抽出して検証する。
        r = await client.get("/api/glossary/terms", params={"scope": "user"})
        assert r.status_code == 200, r.text
        matches = [i for i in r.json()["items"] if i["source_term"] == term]
        assert len(matches) == 1
        assert matches[0]["scope"] == "global"

        # POST scope=user
        r = await client.post(
            "/api/glossary/terms",
            json={
                "scope": "user",
                "source_term": term,
                "target_term": "蒸留(独自)",
                "policy": "translate",
            },
        )
        assert r.status_code == 201, r.text
        user_term = r.json()
        assert user_term["scope"] == "user"
        assert user_term["library_item_id"] is None
        assert user_term["auto_extracted"] is False

        r = await client.get("/api/glossary/terms", params={"scope": "user"})
        scopes = {i["scope"] for i in r.json()["items"] if i["source_term"] == term}
        assert scopes == {"global", "user"}

        # 重複作成は 409 duplicate。
        r = await client.post(
            "/api/glossary/terms",
            json={
                "scope": "user",
                "source_term": term,
                "target_term": "別訳",
                "policy": "translate",
            },
        )
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "duplicate"

        # POST scope=paper(library_item 紐付け)
        r = await client.post(
            "/api/glossary/terms",
            json={
                "scope": "paper",
                "library_item_id": str(ctx.library_item.id),
                "source_term": term,
                "target_term": "蒸留(論文用)",
                "policy": "both",
            },
        )
        assert r.status_code == 201, r.text
        paper_term = r.json()
        assert paper_term["scope"] == "paper"
        assert paper_term["library_item_id"] == str(ctx.library_item.id)

        r = await client.get(
            "/api/glossary/terms",
            params={"scope": "paper", "library_item_id": str(ctx.library_item.id)},
        )
        scopes = {i["scope"] for i in r.json()["items"] if i["source_term"] == term}
        assert scopes == {"global", "paper"}

        # 適用優先度: paper > user > global(plans/06 §8.1)。
        snapshot, _hash = await build_snapshot(
            db_session,
            user_id=ctx.user_id,
            library_item_id=str(ctx.library_item.id),
            shared=False,
        )
        entry = next(e for e in snapshot if e["source_term"] == term)
        assert entry == {
            "source_term": term,
            "target_term": "蒸留(論文用)",
            "policy": "both",
            "origin": "paper",
        }

        # scope=global への書き込みは 403(plans/03 §7.9)。
        r = await client.patch(
            f"/api/glossary/terms/{global_term.id}", json={"target_term": "改変"}
        )
        assert r.status_code == 403, r.text
        r = await client.delete(f"/api/glossary/terms/{global_term.id}")
        assert r.status_code == 403, r.text

        # promote: 論文ローカル→ユーザー用語集(既存の同名 user 語を上書き)。
        r = await client.post(f"/api/glossary/terms/{paper_term['id']}/promote")
        assert r.status_code == 201, r.text
        promoted = r.json()["term"]
        assert promoted["scope"] == "user"
        assert promoted["target_term"] == "蒸留(論文用)"
        assert promoted["policy"] == "both"

        r = await client.get("/api/glossary/terms", params={"scope": "user"})
        user_items = [
            i for i in r.json()["items"] if i["scope"] == "user" and i["source_term"] == term
        ]
        assert len(user_items) == 1  # 上書きなので複製は増えない
        assert user_items[0]["target_term"] == "蒸留(論文用)"

        # 元の paper term は残る(promote は複製。plans/06 §8.5)。
        r = await client.get(
            "/api/glossary/terms",
            params={"scope": "paper", "library_item_id": str(ctx.library_item.id)},
        )
        paper_items = [
            i for i in r.json()["items"] if i["scope"] == "paper" and i["source_term"] == term
        ]
        assert len(paper_items) == 1
        assert paper_items[0]["target_term"] == "蒸留(論文用)"

        # DELETE: 自分の user term を削除できる。
        r = await client.delete(f"/api/glossary/terms/{promoted['id']}")
        assert r.status_code == 204, r.text
        r = await client.get("/api/glossary/terms", params={"scope": "user"})
        assert all(
            not (i["scope"] == "user" and i["source_term"] == term) for i in r.json()["items"]
        )
    finally:
        await _delete_glossary(db_session, str(global_term.glossary_id))


async def test_other_user_cannot_access_paper_scope(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, ctx: SimpleNamespace
) -> None:
    other = await make_user(db_session, email=f"gls-other-{uuid.uuid4().hex}@example.com")
    await db_session.commit()
    other_id = str(other.id)
    try:
        await _login(client, redis_client, other)
        r = await client.get(
            "/api/glossary/terms",
            params={"scope": "paper", "library_item_id": str(ctx.library_item.id)},
        )
        assert r.status_code == 404, r.text

        r = await client.post(
            "/api/glossary/terms",
            json={
                "scope": "paper",
                "library_item_id": str(ctx.library_item.id),
                "source_term": "x",
                "target_term": "y",
                "policy": "translate",
            },
        )
        assert r.status_code == 404, r.text
    finally:
        await db_session.commit()
        await purge_user(db_session, other_id)
        await db_session.commit()


async def test_create_scope_global_is_forbidden(
    client: AsyncClient, ctx: SimpleNamespace, term: str
) -> None:
    """PY-GLS-01: global への書き込み 403 は PATCH/DELETE だけでなく POST(作成)も対象。"""
    r = await client.post(
        "/api/glossary/terms",
        json={"scope": "global", "source_term": term, "target_term": "y", "policy": "translate"},
    )
    assert r.status_code == 403, r.text


async def test_create_and_list_scope_paper_without_library_item_id_is_422(
    client: AsyncClient, ctx: SimpleNamespace, term: str
) -> None:
    r = await client.post(
        "/api/glossary/terms",
        json={"scope": "paper", "source_term": term, "target_term": "y", "policy": "translate"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "validation_error"

    r2 = await client.get("/api/glossary/terms", params={"scope": "paper"})
    assert r2.status_code == 422, r2.text


async def test_promote_non_paper_scope_term_is_conflict(
    client: AsyncClient, ctx: SimpleNamespace, term: str
) -> None:
    r = await client.post(
        "/api/glossary/terms",
        json={"scope": "user", "source_term": term, "target_term": "y", "policy": "translate"},
    )
    assert r.status_code == 201, r.text
    user_term_id = r.json()["id"]

    r2 = await client.post(f"/api/glossary/terms/{user_term_id}/promote")
    assert r2.status_code == 409, r2.text
    assert r2.json()["code"] == "conflict"


async def test_patch_and_delete_scope_user_by_non_owner_is_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    ctx: SimpleNamespace,
    term: str,
) -> None:
    r = await client.post(
        "/api/glossary/terms",
        json={"scope": "user", "source_term": term, "target_term": "y", "policy": "translate"},
    )
    assert r.status_code == 201, r.text
    term_id = r.json()["id"]

    other = await make_user(db_session, email=f"gls-nonowner-{uuid.uuid4().hex}@example.com")
    await db_session.commit()
    other_id = str(other.id)
    try:
        await _login(client, redis_client, other)
        r2 = await client.patch(f"/api/glossary/terms/{term_id}", json={"target_term": "z"})
        assert r2.status_code == 403, r2.text
        r3 = await client.delete(f"/api/glossary/terms/{term_id}")
        assert r3.status_code == 403, r3.text
    finally:
        await db_session.commit()
        await purge_user(db_session, other_id)
        await db_session.commit()


# ---------------------------------------------------------------------------
# PY-GLS-02: dry_run 影響数 / 実適用(影響ブロックのみ再翻訳)。PY-TR-09 と対をなす。
# ---------------------------------------------------------------------------
async def test_glossary_change_dry_run_then_apply_affected_blocks_only(
    client: AsyncClient, db_session: AsyncSession, ctx: SimpleNamespace, term: str
) -> None:
    shared = await make_translation_set(
        db_session, revision=ctx.revision, style="natural", scope="shared", status="complete"
    )
    for block_id in ("blk-p1", "blk-p2", "blk-p3", "blk-p4", "blk-pb"):
        await make_translation_unit(
            db_session, translation_set=shared, block_id=block_id, text_ja="ダミー訳"
        )
    personal = await make_translation_set(
        db_session,
        revision=ctx.revision,
        style="natural",
        scope="personal",
        user=ctx.user,
        base_set=shared,
        status="complete",
    )
    # blk-p2 は手動編集済み(state=edited) → 用語変更ジョブで上書きしない(plans/06 §8.4-4)。
    edited_unit = await make_translation_unit(
        db_session,
        translation_set=personal,
        block_id="blk-p2",
        text_ja="人力で編集した訳",
        state="edited",
    )
    await db_session.commit()

    r = await client.post(
        "/api/glossary/terms",
        json={
            "scope": "user",
            "source_term": term,
            "target_term": "蒸留",
            "policy": "translate",
        },
    )
    assert r.status_code == 201, r.text
    term_id = r.json()["id"]

    # dry_run=true: 影響ブロック数のみ、副作用なし。
    r = await client.patch(
        f"/api/glossary/terms/{term_id}",
        params={"dry_run": "true"},
        json={"target_term": "蒸留(新)"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"affected_block_count": 2}  # blk-p1, blk-p3(p2 除外・p4/pb 不一致)

    jobs_before = (
        (
            await db_session.execute(
                select(Job).where(Job.kind == "translation", Job.paper_id == ctx.paper.id)
            )
        )
        .scalars()
        .all()
    )
    assert jobs_before == []
    term_row = await db_session.get(GlossaryTerm, term_id)
    assert term_row is not None
    assert term_row.target_term == "蒸留"  # dry_run は適用しない

    # dry_run=false(既定): 実適用 + 影響ブロックのみ再翻訳ジョブ(全文再翻訳が走らないこと)。
    r = await client.patch(
        f"/api/glossary/terms/{term_id}",
        json={"target_term": "蒸留(新)", "policy": "both"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["affected_block_count"] == 2
    assert body["term"]["target_term"] == "蒸留(新)"
    assert body["term"]["policy"] == "both"
    job_id = body["job_id"]
    assert job_id

    job = await db_session.get(Job, job_id)
    assert job is not None
    assert job.kind == "translation"
    assert job.payload["reason"] == "glossary_change"
    assert set(job.payload["block_ids"]) == {"blk-p1", "blk-p3"}
    assert job.payload["set_id"] == str(personal.id)

    # db_session の identity map に残る term_row を明示的に再読込する(get() は既知の PK を
    # キャッシュから返すため、refresh しないと API 側の commit が見えない)。
    await db_session.refresh(term_row)
    assert term_row.target_term == "蒸留(新)"
    assert term_row.policy == "both"

    await db_session.refresh(personal)
    snapshot_entry = next(e for e in personal.glossary_snapshot if e["source_term"] == term)
    assert snapshot_entry["target_term"] == "蒸留(新)"
    assert snapshot_entry["origin"] == "user"

    # 手動編集ブロックは非上書き(PY-TR-09 と対をなす確認)。
    await db_session.refresh(edited_unit)
    assert edited_unit.state == "edited"
    assert edited_unit.text_ja == "人力で編集した訳"

    # 影響外ブロック(blk-p4/blk-pb/blk-ref1)には personal 側ユニットが作られない。
    personal_units = (
        (
            await db_session.execute(
                select(TranslationUnit).where(TranslationUnit.set_id == personal.id)
            )
        )
        .scalars()
        .all()
    )
    assert {u.block_id for u in personal_units} == {"blk-p2"}
