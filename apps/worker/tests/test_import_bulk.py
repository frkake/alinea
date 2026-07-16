"""``import_user_data.import_data_json`` のテスト(完全データ移行 Task 3)。

export 側の seed を再利用して payload を作り、別ユーザーへ冪等マージ復元することを検証する。
- 元データを削除して「別 PC」を模し、1 回目は created、2 回目は全 skip(冪等)。
- 無損失復元: note.anchors / chat message の content・evidence_anchors / vocab.context_anchor。
- document_revisions 復元後に block_search_index が再構築される。
DB は実 PostgreSQL(worker conftest の db_session)。S3 は使わない(メタのみ復元)。
"""

from __future__ import annotations

import json
import uuid

from alinea_core.db.models import (
    Annotation,
    BlockSearchIndex,
    ChatMessage,
    ChatThread,
    DocumentRevision,
    LibraryItem,
    Note,
    User,
    VocabEntry,
)
from alinea_worker.tasks.export_user_data import build_export_payload
from alinea_worker.tasks.import_user_data import import_data_json
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_export_bulk import _seed_user_data


async def _make_user(db: AsyncSession) -> dict[str, str]:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    await db.commit()
    return {"user_id": str(user.id)}


async def _detached_payload(db: AsyncSession, user_id: str) -> dict:
    """payload を JSON 往復で完全にデタッチし、本文に索引対象ブロックを1つ注入する。"""
    payload = json.loads(json.dumps(await build_export_payload(db, user_id)))
    # 空ブロックの revision だと block_search_index が0件になるため、段落を1つ注入。
    for rev in payload["document_revisions"]:
        rev["content"] = {
            "quality_level": "A",
            "sections": [
                {
                    "id": "s1",
                    "heading": {"number": "1", "title": "Introduction"},
                    "blocks": [
                        {
                            "id": "blk-1",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "rectified flow"}],
                        }
                    ],
                }
            ],
        }
    return payload


async def _delete_source_user(db: AsyncSession, user_id: str) -> None:
    """元ユーザーを削除(FK ON DELETE CASCADE で全所有データが消える=別 PC を模す)。"""
    user = await db.get(User, user_id)
    assert user is not None
    await db.delete(user)
    await db.commit()
    # 同一セッション再利用のため identity map を空にし、session.get が DB を叩くようにする
    # (別 PC への移行では identity map は当然空。ここはテスト上の擬似化)。
    db.expunge_all()


async def test_import_merges_idempotently(db_session: AsyncSession) -> None:
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])
    await _delete_source_user(db_session, src["user_id"])

    target = await _make_user(db_session)

    summary1 = await import_data_json(db_session, target["user_id"], payload)
    assert summary1["failed"] == [], summary1["failed"]
    assert summary1["created"]["library"] >= 1
    assert summary1["created"]["document_revisions"] >= 1

    # target に本文・翻訳・語彙などが復元されている
    items = (
        (await db_session.execute(select(LibraryItem).where(LibraryItem.user_id == target["user_id"])))
        .scalars()
        .all()
    )
    assert len(items) >= 1
    revs = (await db_session.execute(select(DocumentRevision))).scalars().all()
    assert len(revs) >= 1

    # block_search_index が再構築されている(新 revision に対し行数>0)
    new_rev_id = payload["document_revisions"][0]["id"]
    idx_count = (
        await db_session.execute(
            select(func.count())
            .select_from(BlockSearchIndex)
            .where(BlockSearchIndex.revision_id == new_rev_id)
        )
    ).scalar_one()
    assert idx_count > 0

    # 2 回目は全 skip(冪等)
    summary2 = await import_data_json(db_session, target["user_id"], payload)
    assert summary2["created"]["library"] == 0
    assert summary2["skipped"]["library"] >= 1
    assert summary2["created"]["document_revisions"] == 0
    assert summary2["skipped"]["document_revisions"] >= 1


async def test_import_is_lossless_for_anchors_and_content(db_session: AsyncSession) -> None:
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])
    await _delete_source_user(db_session, src["user_id"])
    target = await _make_user(db_session)

    await import_data_json(db_session, target["user_id"], payload)

    target_items = [
        i.id
        for i in (
            await db_session.execute(
                select(LibraryItem).where(LibraryItem.user_id == target["user_id"])
            )
        )
        .scalars()
        .all()
    ]

    # note.anchors が保持される
    note = (
        (await db_session.execute(select(Note).where(Note.library_item_id.in_(target_items))))
        .scalars()
        .first()
    )
    assert note is not None
    exported_note = payload["notes"][0]
    assert note.anchors == exported_note["anchors"]

    # annotation は quote(GENERATED)が anchor から再生成される
    ann = (
        (await db_session.execute(select(Annotation).where(Annotation.library_item_id.in_(target_items))))
        .scalars()
        .first()
    )
    assert ann is not None
    assert ann.quote == ann.anchor.get("quote")

    # chat message の構造化 content / evidence_anchors が保持される
    threads = (
        (await db_session.execute(select(ChatThread).where(ChatThread.library_item_id.in_(target_items))))
        .scalars()
        .all()
    )
    assert threads
    msgs = (
        (await db_session.execute(select(ChatMessage).where(ChatMessage.thread_id == threads[0].id)))
        .scalars()
        .all()
    )
    assert msgs
    exported_msg = payload["chat_threads"][0]["messages"][0]
    assert msgs[0].content == exported_msg["content"]
    assert msgs[0].evidence_anchors == exported_msg["evidence_anchors"]

    # vocab.context_anchor が保持される
    vocab = (
        (await db_session.execute(select(VocabEntry).where(VocabEntry.user_id == target["user_id"])))
        .scalars()
        .first()
    )
    assert vocab is not None
    assert vocab.context_anchor == payload["vocab"][0]["context_anchor"]
