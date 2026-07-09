"""``jobs.kind='export'`` ハンドラのテスト(PY-EXP-04。plans/03 §18・M2-15)。

全量 JSON(``export_user_data.run_export_full_job``)がライブラリ・注釈・メモ・チャット・
語彙(SRS 含む)・リソース・記事・コレクション・設定の全キーを持ち、対象データを反映した
zip を S3(assets バケット)へアップロードして ``jobs.result.download_url`` を確定させる
ことを検証する。DB は実 PostgreSQL、S3 は実 MinIO(worker conftest の規約と同じ)。
"""

from __future__ import annotations

import json
import uuid
import zipfile
from io import BytesIO
from typing import Any

from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    Collection,
    CollectionEntry,
    LibraryItem,
    Note,
    Paper,
    ResourceLink,
    User,
    VocabEntry,
)
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_worker.tasks.export_user_data import build_export_payload, run_export_full_job
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_user_data(db: AsyncSession) -> dict[str, str]:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4().hex}@t.test",
        settings={"translation": {"default_style": "literal"}},
    )
    db.add(user)
    await db.flush()

    paper = Paper(
        id=str(uuid.uuid4()),
        title="Flow Straight and Fast",
        authors=[{"name": "Xingchang Liu"}],
        arxiv_id=f"2209.{uuid.uuid4().int % 100000:05d}",
        owner_user_id=user.id,
        visibility="private",
    )
    db.add(paper)
    await db.flush()

    item = LibraryItem(
        id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="reading", tags=["flow"]
    )
    db.add(item)
    await db.flush()

    db.add(Note(id=str(uuid.uuid4()), library_item_id=item.id, title="要点", body_md="整流フロー"))
    db.add(
        Annotation(
            id=str(uuid.uuid4()),
            library_item_id=item.id,
            kind="highlight",
            color="important",
            anchor={
                "revision_id": str(uuid.uuid4()),
                "block_id": "blk-1",
                "start": 0,
                "end": 5,
                "quote": "flow",
                "side": "source",
            },
        )
    )

    thread = ChatThread(id=str(uuid.uuid4()), library_item_id=item.id, title="メイン", is_main=True)
    db.add(thread)
    await db.flush()
    db.add(
        ChatMessage(
            thread_id=thread.id,
            role="user",
            content={"segments": [{"type": "text", "text": "質問"}]},
            text_plain="質問",
        )
    )

    db.add(
        VocabEntry(
            id=str(uuid.uuid4()),
            user_id=user.id,
            library_item_id=item.id,
            term="rectified flow",
            context_anchor={
                "revision_id": str(uuid.uuid4()),
                "block_id": "blk-1",
                "start": 0,
                "end": 5,
                "quote": "flow",
                "side": "source",
            },
            context_sentence="Rectified flow learns a straight map.",
        )
    )

    db.add(
        ResourceLink(
            id=str(uuid.uuid4()),
            library_item_id=item.id,
            kind="github",
            url="https://github.com/gnobitab/RectifiedFlow",
            url_normalized="https://github.com/gnobitab/rectifiedflow",
        )
    )

    article = Article(id=str(uuid.uuid4()), library_item_id=item.id, title="やさしい解説")
    db.add(article)
    await db.flush()
    db.add(
        ArticleBlock(
            article_id=article.id,
            position=0,
            type="heading",
            content={"heading": {"level": 2, "text": "はじめに"}},
            text_plain="はじめに",
        )
    )

    collection = Collection(id=str(uuid.uuid4()), user_id=user.id, name="輪読会")
    db.add(collection)
    await db.flush()
    db.add(
        CollectionEntry(
            id=str(uuid.uuid4()), collection_id=collection.id, library_item_id=item.id, position=0
        )
    )

    await db.commit()
    return {"user_id": str(user.id), "library_item_id": str(item.id)}


async def _run_export_job(db: AsyncSession, user_id: str) -> Any:
    store = JobStore(db)
    job_id = await store.enqueue(kind="export", user_id=user_id, payload={})
    job = await store.claim(job_id)
    assert job is not None
    await run_export_full_job({}, store, job)
    return await store.get(job_id)


async def test_build_export_payload_has_all_category_keys(db_session: AsyncSession) -> None:
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    for key in (
        "exported_at",
        "user",
        "library",
        "notes",
        "annotations",
        "chat_threads",
        "vocab",
        "resources",
        "articles",
        "collections",
        "settings",
    ):
        assert key in payload, f"missing key: {key}"

    assert len(payload["library"]) == 1
    assert payload["library"][0]["library_item_id"] == ids["library_item_id"]
    assert len(payload["notes"]) == 1
    assert payload["notes"][0]["body_md"] == "整流フロー"
    assert len(payload["annotations"]) == 1
    assert len(payload["chat_threads"]) == 1
    assert payload["chat_threads"][0]["messages"][0]["text"] == "質問"
    assert len(payload["vocab"]) == 1
    assert payload["vocab"][0]["srs"]["stage"] == 1  # 既定ステージ(plans/02)
    assert len(payload["resources"]) == 1
    assert len(payload["articles"]) == 1
    assert payload["articles"][0]["blocks"][0]["content"]["heading"]["text"] == "はじめに"
    assert len(payload["collections"]) == 1
    assert payload["collections"][0]["library_item_ids"] == [ids["library_item_id"]]
    assert payload["settings"]["translation"]["default_style"] == "literal"


async def test_run_export_full_job_uploads_zip_and_sets_download_url(
    db_session: AsyncSession,
) -> None:
    ids = await _seed_user_data(db_session)
    job = await _run_export_job(db_session, ids["user_id"])

    assert job is not None
    assert job.status == "succeeded"
    download_url = job.result.get("download_url")
    assert isinstance(download_url, str) and download_url

    storage = S3Storage()
    key = StorageKeys.export(ids["user_id"], str(job.id))
    archive = await storage.get(storage.assets_bucket, key)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        names = zf.namelist()
        assert names == ["alinea-export.json"]
        payload = json.loads(zf.read("alinea-export.json"))
    assert payload["user"]["id"] == ids["user_id"]
    assert len(payload["library"]) == 1


async def test_export_job_for_user_with_no_data_still_succeeds(db_session: AsyncSession) -> None:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db_session.add(user)
    await db_session.commit()

    job = await _run_export_job(db_session, str(user.id))
    assert job is not None
    assert job.status == "succeeded"
    assert isinstance(job.result.get("download_url"), str)
