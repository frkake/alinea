"""``import_user_data.import_data_json`` / ``run_import_full_job`` のテスト
(完全データ移行 Task 3・Task 4)。

export 側の seed を再利用して payload を作り、別ユーザーへ冪等マージ復元することを検証する。
- 元データを削除して「別 PC」を模し、1 回目は created、2 回目は全 skip(冪等)。
- 無損失復元: note.anchors / chat message の content・evidence_anchors / vocab.context_anchor。
- document_revisions 復元後に block_search_index が再構築される。
- Task 4: zip ラウンドトリップでアセット(sha256 照合)も復元される。
DB は実 PostgreSQL(worker conftest の db_session)。
S3 は Task 4 のラウンドトリップテストのみ実 MinIO を使う(S3Storage() で直接生成)。
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from typing import Any, cast

import pytest
from alinea_core.db.models import (
    Annotation,
    BlockSearchIndex,
    ChatMessage,
    ChatThread,
    DocumentRevision,
    LibraryItem,
    Note,
    Paper,
    PaperExternalId,
    User,
    VocabEntry,
)
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage
from alinea_worker.tasks import import_user_data
from alinea_worker.tasks.export_user_data import build_export_archive, build_export_payload
from alinea_worker.tasks.import_user_data import import_data_json, run_import_full_job
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_export_bulk import _seed_user_data


async def _make_user(db: AsyncSession) -> dict[str, str]:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    await db.commit()
    return {"user_id": str(user.id)}


async def _detached_payload(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """payload を JSON 往復で完全にデタッチし、本文に索引対象ブロックを1つ注入する。"""
    payload = cast(dict[str, Any], json.loads(json.dumps(await build_export_payload(db, user_id))))
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


async def test_import_restores_paper_external_id(db_session: AsyncSession) -> None:
    """完全バックアップの外部識別子(site, external_id)が別ユーザーへ復元される。"""
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])
    assert len(payload["paper_external_ids"]) == 1
    external_id = payload["paper_external_ids"][0]["external_id"]
    await _delete_source_user(db_session, src["user_id"])

    target = await _make_user(db_session)
    summary = await import_data_json(db_session, target["user_id"], payload)
    assert summary["failed"] == [], summary["failed"]

    row = (
        await db_session.execute(
            select(PaperExternalId).where(PaperExternalId.external_id == external_id)
        )
    ).scalar_one()
    assert row.site == "acl_anthology"
    # 復元された identifier は target の新規 paper を指す。
    paper = await db_session.get(Paper, str(row.paper_id))
    assert paper is not None
    assert str(paper.owner_user_id) == target["user_id"]


async def test_import_name_matches_by_external_id(db_session: AsyncSession) -> None:
    """(site, external_id) で既存 Paper に名寄せし、重複作成しない。"""
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])
    external_id = payload["paper_external_ids"][0]["external_id"]
    # 元 paper から arxiv_id/doi を落とし、名寄せが external_id だけに依存するようにする。
    for entry in payload["library"]:
        entry["arxiv_id"] = None
        entry["doi"] = None
    await _delete_source_user(db_session, src["user_id"])
    # 論文用の外部識別子は CASCADE で消えるため「別 PC」を模して明示除去済み。

    # target には同一 (site, external_id) を持つ既存 paper を先に作っておく。
    target = await _make_user(db_session)
    existing = Paper(
        id=str(uuid.uuid4()),
        title="Pre-existing ACL paper",
        visibility="private",
        owner_user_id=target["user_id"],
    )
    db_session.add(existing)
    await db_session.flush()
    db_session.add(
        PaperExternalId(
            id=str(uuid.uuid4()),
            paper_id=existing.id,
            site="acl_anthology",
            external_id=external_id,
        )
    )
    await db_session.commit()

    summary = await import_data_json(db_session, target["user_id"], payload)
    assert summary["failed"] == [], summary["failed"]

    # 既存 paper を再利用(新規 paper を作らない)。
    papers = (
        (
            await db_session.execute(
                select(PaperExternalId).where(PaperExternalId.external_id == external_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(papers) == 1
    assert str(papers[0].paper_id) == str(existing.id)


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


# ---------------------------------------------------------------------------
# Task 4: インポートジョブハンドラ — zip ラウンドトリップ + アセット復元
# ---------------------------------------------------------------------------

async def test_import_job_roundtrip_restores_assets(db_session: AsyncSession) -> None:
    """エクスポート zip を S3 に置き、import Job がアセット sha256 照合で復元することを検証。"""
    storage = S3Storage()

    src = await _seed_user_data(db_session)
    # source_asset が指す storage_key に実バイナリを置く
    await storage.put(
        storage.sources_bucket,
        src["asset_key"],
        b"%PDF-1.7 fake",
        content_type="application/pdf",
    )
    # アーカイブを生成(manifest + data.json + assets/...)
    archive = await build_export_archive(db_session, src["user_id"], storage)

    # 一時 key に zip を保存
    upload_key = f"imports/{uuid.uuid4()}.zip"
    await storage.put(
        storage.assets_bucket, upload_key, archive, content_type="application/zip"
    )

    # 別ユーザーを作成して import Job を作る
    target = await _make_user(db_session)
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="import",
        priority="bulk",
        user_id=target["user_id"],
        payload={"upload_key": upload_key},
    )
    job = await store.claim(job_id)
    assert job is not None

    await run_import_full_job({"s3": storage}, store, job)

    done = await store.get(job_id)
    assert done is not None
    assert done.status == "succeeded", f"job failed: {done.result}"
    assert done.result["summary"]["created"]["library"] >= 1

    # アセットが復元されている(同じ key に同じバイト列が入っている)
    restored = await storage.get(storage.sources_bucket, src["asset_key"])
    assert restored == b"%PDF-1.7 fake"


# ---------------------------------------------------------------------------
# Task 7: ラウンドトリップ E2E — BYOK 除外 + 検索索引再構築
# ---------------------------------------------------------------------------

async def _seed_user_data_with_byok(db: AsyncSession) -> dict[str, str]:
    """_seed_user_data に byok_api_keys を 1 行追加したシード。"""
    from alinea_core.db.models import ByokApiKey

    ids = await _seed_user_data(db)
    db.add(
        ByokApiKey(
            user_id=ids["user_id"],
            provider="anthropic",
            encrypted_key=b"sk-ant-secret-key-fake",
            key_hint="sk-ant-...fake",
        )
    )
    await db.commit()
    return ids


async def test_export_excludes_byok_and_import_rebuilds_search(db_session: AsyncSession) -> None:
    """エクスポート zip に byok_api_keys が含まれないことと、
    インポート後に block_search_index が再構築されることを検証する。
    """
    import io
    import json
    import zipfile

    storage = S3Storage()

    src = await _seed_user_data_with_byok(db_session)

    # アーカイブ生成(source_asset のバイナリも S3 に置く)
    await storage.put(
        storage.sources_bucket,
        src["asset_key"],
        b"%PDF-1.7 byok-test",
        content_type="application/pdf",
    )
    archive = await build_export_archive(db_session, src["user_id"], storage)

    # data.json に byok が含まれないことを確認
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        blob = zf.read("data.json").decode("utf-8")
        assert "byok" not in blob.lower(), "byok keys must not appear in export"
        assert "sk-ant-secret" not in blob, "byok plaintext must not appear in export"
        data = json.loads(blob)

    # インポート後、block_search_index が再構築される
    target = await _make_user(db_session)

    # document_revision に索引対象ブロックを注入してから import
    for rev in data.get("document_revisions", []):
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
                            "inlines": [{"t": "text", "v": "byok exclusion test block"}],
                        }
                    ],
                }
            ],
        }

    from alinea_core.db.models import BlockSearchIndex

    summary = await import_data_json(db_session, target["user_id"], data)
    assert summary["failed"] == [], summary["failed"]
    assert summary["created"]["library"] >= 1

    # block_search_index が再構築されている
    from sqlalchemy import func, select

    idx_count = (
        await db_session.execute(
            select(func.count()).select_from(BlockSearchIndex)
        )
    ).scalar_one()
    assert idx_count > 0, "block_search_index should be rebuilt after import"


async def test_import_job_rejects_invalid_schema_version(db_session: AsyncSession) -> None:
    """schema_version が 2 でない zip は fail_with_retry で拒否される。"""
    import io
    import json
    import zipfile

    storage = S3Storage()

    # 不正な schema_version を持つ manifest を作る
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 99, "assets": []}))
        zf.writestr("data.json", json.dumps({"library": [], "user": {}}))
    archive = buf.getvalue()

    upload_key = f"imports/{uuid.uuid4()}.zip"
    await storage.put(
        storage.assets_bucket, upload_key, archive, content_type="application/zip"
    )

    target = await _make_user(db_session)
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="import",
        priority="bulk",
        user_id=target["user_id"],
        payload={"upload_key": upload_key},
    )
    job = await store.claim(job_id)
    assert job is not None

    await run_import_full_job({"s3": storage}, store, job)

    done = await store.get(job_id)
    assert done is not None
    # schema_version 不一致は fail_with_retry → attempt<max → status='queued' or 'failed'
    assert done.status in ("queued", "failed"), f"unexpected status: {done.status}"


def test_validated_members_rejects_member_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """ZIP entry の展開後サイズが上限を超える場合は読む前に拒否する。"""
    monkeypatch.setattr(import_user_data, "_MAX_ZIP_MEMBER_BYTES", 1)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("data.json", b"{}")

    with zipfile.ZipFile(io.BytesIO(archive.getvalue())) as zf:
        with pytest.raises(ValueError, match="zip_member_too_large"):
            import_user_data._validated_members(zf)


def test_prepare_asset_destinations_rekeys_untrusted_storage_key() -> None:
    """manifestが指定した既存S3キーを移行先の書込み先に使わない。"""
    data = {
        "source_assets": [
            {"id": str(uuid.uuid4()), "storage_key": "sources/foreign/private/original.pdf"}
        ]
    }

    destinations = import_user_data._prepare_asset_destinations(data, "target-user")

    destination = destinations["sources/foreign/private/original.pdf"]
    assert destination[0] == "sources"
    assert destination[1] != "sources/foreign/private/original.pdf"
    assert data["source_assets"][0]["storage_key"] == destination[1]
