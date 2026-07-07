"""notes API テスト(M1-04 / plans/03 §9・§10.5、docs/05 §8)。

- PY-NOTE-01: チャット→メモ昇格。``source_message_id`` 指定で根拠アンカーを複写し、
  出自参照(``source.chat_message_id``)を保つ。明示 ``anchors`` 指定時は複写しない。
- 手動メモ CRUD(GET/POST/PATCH/DELETE)・更新降順・所有権チェック(404)。
- スレッド「まとめてメモ化」(§10.5): 同期実行で Note を作成し、複数回答の根拠アンカーを
  重複なく複写する。LLM は FakeLLMProvider(決定的)。

DB は実 PostgreSQL。テストデータは私有 Paper(owner=テストユーザー)として作り、
teardown の purge_user でカスケード削除する。認証はセッション直発行 + cookie(test_chat.py と同型)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import factories
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.deps import get_settings_dep
from yakudoku_api.routers.notes import get_notes_provider_factory
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_api.settings import ApiSettings
from yakudoku_core.document.blocks import DocumentContent
from yakudoku_core.search.rebuild import rebuild_block_search_index
from yakudoku_llm.testing.fake_provider import FakeLLMProvider


def _fake_factory(provider: str, api_key: str) -> FakeLLMProvider:
    return FakeLLMProvider(name=provider)


# ---------------------------------------------------------------------------
# 統合フィクスチャ(DB + notes/chat エンドポイント)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def note_ctx(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[SimpleNamespace]:
    from yakudoku_api.main import app

    user = await upsert_user_by_email(
        db_session, f"note-{uuid.uuid4().hex}@example.com", provider="email"
    )
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    revision = await factories.make_revision(db_session, paper=paper)
    content = DocumentContent.model_validate(revision.content)
    await rebuild_block_search_index(db_session, str(revision.id), content)
    item = await factories.make_library_item(db_session, user=user, paper=paper, status="reading")
    thread = await factories.make_chat_thread(db_session, library_item=item)
    await db_session.commit()

    token = await create_session(redis_client, user.id)
    client.cookies.set("yk_session", token)
    test_settings = ApiSettings(anthropic_api_key="test-op-key")
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[get_notes_provider_factory] = lambda: _fake_factory
    try:
        yield SimpleNamespace(
            db=db_session,
            user_id=str(user.id),
            item_id=str(item.id),
            revision=revision,
            thread_id=str(thread.id),
            thread=thread,
        )
    finally:
        app.dependency_overrides.pop(get_settings_dep, None)
        app.dependency_overrides.pop(get_notes_provider_factory, None)
        await db_session.rollback()
        await purge_user(db_session, str(user.id))
        await db_session.commit()


# ---------------------------------------------------------------------------
# 手動メモ CRUD
# ---------------------------------------------------------------------------
async def test_create_and_list_manual_note(client: AsyncClient, note_ctx: SimpleNamespace) -> None:
    resp = await client.post(
        f"/api/library-items/{note_ctx.item_id}/notes",
        json={"content_md": "手動メモ本文"},
    )
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["content_md"] == "手動メモ本文"
    assert note["source"] is None
    assert note["anchors"] == []
    assert note["created_at"] and note["updated_at"]

    listed = await client.get(f"/api/library-items/{note_ctx.item_id}/notes")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == note["id"]


async def test_notes_list_is_updated_desc(client: AsyncClient, note_ctx: SimpleNamespace) -> None:
    first = (
        await client.post(
            f"/api/library-items/{note_ctx.item_id}/notes", json={"content_md": "1つ目"}
        )
    ).json()
    second = (
        await client.post(
            f"/api/library-items/{note_ctx.item_id}/notes", json={"content_md": "2つ目"}
        )
    ).json()

    listed = (await client.get(f"/api/library-items/{note_ctx.item_id}/notes")).json()["items"]
    assert [n["id"] for n in listed] == [second["id"], first["id"]]  # 更新降順

    # 1つ目を更新すると先頭に来る(更新降順)。
    patched = await client.patch(f"/api/notes/{first['id']}", json={"content_md": "改訂版"})
    assert patched.status_code == 200
    assert patched.json()["content_md"] == "改訂版"

    listed2 = (await client.get(f"/api/library-items/{note_ctx.item_id}/notes")).json()["items"]
    assert [n["id"] for n in listed2] == [first["id"], second["id"]]


async def test_delete_note(client: AsyncClient, note_ctx: SimpleNamespace) -> None:
    created = (
        await client.post(
            f"/api/library-items/{note_ctx.item_id}/notes", json={"content_md": "削除対象"}
        )
    ).json()

    resp = await client.delete(f"/api/notes/{created['id']}")
    assert resp.status_code == 204

    listed = (await client.get(f"/api/library-items/{note_ctx.item_id}/notes")).json()["items"]
    assert listed == []

    # 再削除は 404。
    again = await client.delete(f"/api/notes/{created['id']}")
    assert again.status_code == 404


async def test_notes_ownership_is_enforced(
    client: AsyncClient, db_session: AsyncSession, note_ctx: SimpleNamespace
) -> None:
    other_user = await factories.make_user(db_session)
    other_item = await factories.make_library_item(db_session, user=other_user)
    other_note = await factories.make_note(db_session, library_item=other_item)
    await db_session.commit()

    forbidden_list = await client.get(f"/api/library-items/{other_item.id}/notes")
    assert forbidden_list.status_code == 404

    forbidden_create = await client.post(
        f"/api/library-items/{other_item.id}/notes", json={"content_md": "x"}
    )
    assert forbidden_create.status_code == 404

    forbidden_patch = await client.patch(f"/api/notes/{other_note.id}", json={"content_md": "x"})
    assert forbidden_patch.status_code == 404

    forbidden_delete = await client.delete(f"/api/notes/{other_note.id}")
    assert forbidden_delete.status_code == 404


# ---------------------------------------------------------------------------
# PY-NOTE-01: チャット→メモ昇格
# ---------------------------------------------------------------------------
async def test_create_note_copies_evidence_anchors_from_message(
    client: AsyncClient, note_ctx: SimpleNamespace
) -> None:
    anchor_eq1 = factories.anchor_for(note_ctx.revision, 2)  # blk-eq1(式(1))
    msg = await factories.make_chat_message(
        note_ctx.db,
        thread=note_ctx.thread,
        role="assistant",
        text_plain="整流フローは式(1)の輸送を最小化します。",
        evidence_anchors=[anchor_eq1],
    )
    await note_ctx.db.commit()

    resp = await client.post(
        f"/api/library-items/{note_ctx.item_id}/notes",
        json={"content_md": "↑ メモに保存", "source_message_id": str(msg.id)},
    )
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["source"] == {"chat_message_id": str(msg.id)}
    assert len(note["anchors"]) == 1
    assert note["anchors"][0]["block_id"] == "blk-eq1"
    assert note["anchors"][0]["display"] == "式(1)"  # §2.5.2: equation → element_label

    # 出自参照は GET でも保持される。
    listed = (await client.get(f"/api/library-items/{note_ctx.item_id}/notes")).json()["items"]
    assert listed[0]["source"] == {"chat_message_id": str(msg.id)}


async def test_create_note_explicit_anchors_override_source_message(
    client: AsyncClient, note_ctx: SimpleNamespace
) -> None:
    anchor_eq1 = factories.anchor_for(note_ctx.revision, 2)
    msg = await factories.make_chat_message(
        note_ctx.db, thread=note_ctx.thread, role="assistant", evidence_anchors=[anchor_eq1]
    )
    await note_ctx.db.commit()

    resp = await client.post(
        f"/api/library-items/{note_ctx.item_id}/notes",
        json={
            "content_md": "明示アンカー",
            "source_message_id": str(msg.id),
            "anchors": [],
        },
    )
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["source"] == {"chat_message_id": str(msg.id)}
    assert note["anchors"] == []  # 明示 anchors=[] は複写しない


async def test_create_note_invalid_source_message_id_is_422(
    client: AsyncClient, note_ctx: SimpleNamespace
) -> None:
    resp = await client.post(
        f"/api/library-items/{note_ctx.item_id}/notes",
        json={"content_md": "x", "source_message_id": "999999999"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_create_note_non_numeric_source_message_id_is_422(
    client: AsyncClient, note_ctx: SimpleNamespace
) -> None:
    resp = await client.post(
        f"/api/library-items/{note_ctx.item_id}/notes",
        json={"content_md": "x", "source_message_id": "not-a-number"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_create_note_source_message_from_other_item_is_422(
    client: AsyncClient, note_ctx: SimpleNamespace
) -> None:
    """source_message_id が別 library_item のスレッドに属するメッセージは 422(§9)。"""
    other_item = await factories.make_library_item(note_ctx.db, status="reading")
    other_thread = await factories.make_chat_thread(note_ctx.db, library_item=other_item)
    other_msg = await factories.make_chat_message(
        note_ctx.db, thread=other_thread, role="assistant", text_plain="別の論文の回答"
    )
    await note_ctx.db.commit()

    resp = await client.post(
        f"/api/library-items/{note_ctx.item_id}/notes",
        json={"content_md": "x", "source_message_id": str(other_msg.id)},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_notes_invalid_uuid_path_params_are_404(
    client: AsyncClient, note_ctx: SimpleNamespace
) -> None:
    assert (await client.get("/api/library-items/not-a-uuid/notes")).status_code == 404
    assert (
        await client.post("/api/library-items/not-a-uuid/notes", json={"content_md": "x"})
    ).status_code == 404
    assert (
        await client.patch("/api/notes/not-a-uuid", json={"content_md": "x"})
    ).status_code == 404
    assert (await client.delete("/api/notes/not-a-uuid")).status_code == 404


async def test_summarize_to_note_other_users_thread_is_404(
    client: AsyncClient, note_ctx: SimpleNamespace, db_session: AsyncSession
) -> None:
    other_item = await factories.make_library_item(db_session, status="reading")
    other_thread = await factories.make_chat_thread(db_session, library_item=other_item)
    await db_session.commit()

    resp = await client.post(f"/api/chat/threads/{other_thread.id}/summarize-to-note")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# §10.5: まとめてメモ化
# ---------------------------------------------------------------------------
async def test_summarize_to_note_creates_note_with_deduped_anchors(
    client: AsyncClient, note_ctx: SimpleNamespace
) -> None:
    anchor_eq1 = factories.anchor_for(note_ctx.revision, 2)  # blk-eq1
    anchor_p3 = factories.anchor_for(note_ctx.revision, 3)  # blk-p3

    await factories.make_chat_message(
        note_ctx.db,
        thread=note_ctx.thread,
        role="user",
        text_plain="整流フローとは何ですか?",
    )
    await factories.make_chat_message(
        note_ctx.db,
        thread=note_ctx.thread,
        role="assistant",
        text_plain="整流フローは輸送を直線化します(式(1))。",
        evidence_anchors=[anchor_eq1],
    )
    await factories.make_chat_message(
        note_ctx.db,
        thread=note_ctx.thread,
        role="assistant",
        text_plain="reflow は式(1)の経路をさらに直線化します(§2 ¶1)。",
        evidence_anchors=[anchor_eq1, anchor_p3],  # blk-eq1 は重複
    )
    await note_ctx.db.commit()

    resp = await client.post(f"/api/chat/threads/{note_ctx.thread_id}/summarize-to-note")
    assert resp.status_code == 201, resp.text
    note = resp.json()["note"]
    assert note["content_md"]  # FakeLLMProvider の決定的エコー(非空)
    assert note["source"] is None  # スレッド全体の要約は単一メッセージ出自を持たない
    block_ids = [a["block_id"] for a in note["anchors"]]
    assert block_ids == ["blk-eq1", "blk-p3"]  # 出現順・重複除去(§8)

    # 作成された Note はメモパネル(GET 一覧)にも現れる。
    listed = (await client.get(f"/api/library-items/{note_ctx.item_id}/notes")).json()["items"]
    assert any(n["id"] == note["id"] for n in listed)


async def test_summarize_to_note_thread_not_found_is_404(
    client: AsyncClient, note_ctx: SimpleNamespace
) -> None:
    resp = await client.post(f"/api/chat/threads/{uuid.uuid4()}/summarize-to-note")
    assert resp.status_code == 404
