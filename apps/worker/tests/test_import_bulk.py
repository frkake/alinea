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
    ArticlePublication,
    BlockSearchIndex,
    ChatMessage,
    ChatThread,
    DocumentRevision,
    LibraryItem,
    Note,
    Paper,
    PaperExternalId,
    PublicationComment,
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


async def test_import_restores_shared_translation_and_paper_glossary(
    db_session: AsyncSession,
) -> None:
    """共有翻訳(user_id IS NULL)と論文用用語集が別ユーザーへ無損失復元される。

    共有行は移行先ユーザー専用行へ変換せず、user_id IS NULL のまま保つ。
    """
    from alinea_core.db.models import Glossary, GlossaryTerm, TranslationSet, TranslationUnit

    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])

    # ペイロードに共有翻訳と論文用用語集が含まれる(エクスポート側の前提)。
    assert any(ts["scope"] == "shared" for ts in payload["translation_sets"])
    assert any(g["scope"] == "paper" for g in payload["glossaries"])

    await _delete_source_user(db_session, src["user_id"])
    # 論文用用語集(user_id IS NULL)はユーザー削除の CASCADE で消えないため、
    # 「別 PC の空環境」を模して明示的に除去する。
    orphan = await db_session.get(Glossary, src["paper_glossary_id"])
    if orphan is not None:
        await db_session.delete(orphan)
        await db_session.commit()
        db_session.expunge_all()

    target = await _make_user(db_session)
    summary = await import_data_json(db_session, target["user_id"], payload)
    assert summary["failed"] == [], summary["failed"]

    # 共有翻訳セットが復元され、user_id IS NULL のまま(専用行へ変換されていない)。
    shared_set = await db_session.get(TranslationSet, src["shared_set_id"])
    assert shared_set is not None
    assert shared_set.scope == "shared"
    assert shared_set.user_id is None
    assert shared_set.style == "literal"
    assert str(shared_set.revision_id) == src["revision_id"]

    # 共有セットの翻訳単位が復元される。
    shared_units = (
        (
            await db_session.execute(
                select(TranslationUnit).where(
                    TranslationUnit.set_id == src["shared_set_id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(shared_units) == 1
    assert shared_units[0].text_ja == "共有フロー"

    # 論文用用語集が復元され、user_id IS NULL のまま、library_item_id は移行先へ張り替え。
    target_items = [
        str(i.id)
        for i in (
            await db_session.execute(
                select(LibraryItem).where(LibraryItem.user_id == target["user_id"])
            )
        )
        .scalars()
        .all()
    ]
    paper_glossary = await db_session.get(Glossary, src["paper_glossary_id"])
    assert paper_glossary is not None
    assert paper_glossary.scope == "paper"
    assert paper_glossary.user_id is None
    assert str(paper_glossary.library_item_id) in target_items

    # 論文用用語集の用語が復元される。
    paper_terms = (
        (
            await db_session.execute(
                select(GlossaryTerm).where(
                    GlossaryTerm.glossary_id == src["paper_glossary_id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(paper_terms) == 1
    assert paper_terms[0].source_term == "straight map"


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


async def test_import_forces_publication_unlisted(db_session: AsyncSession) -> None:
    """Task 24: 記事公開スナップショットの復元は必ず visibility=unlisted に落とす。

    元は public でも、移行操作だけで公開 URL を再公開しない(P4)。slug と blocks は保持する。
    """
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])
    # エクスポートは public のまま保持している(復元側で unlisted に落とすのを検証する)。
    assert payload["publications"][0]["visibility"] == "public"
    await _delete_source_user(db_session, src["user_id"])
    target = await _make_user(db_session)

    summary = await import_data_json(db_session, target["user_id"], payload)
    assert summary["created"].get("publications", 0) >= 1

    restored = (
        (
            await db_session.execute(
                select(ArticlePublication).where(ArticlePublication.user_id == target["user_id"])
            )
        )
        .scalars()
        .all()
    )
    assert len(restored) == 1
    pub = restored[0]
    # 公開 URL の再公開はしない。
    assert pub.visibility == "unlisted"
    # slug と本文スナップショットは保持される。
    assert pub.slug == src["publication_slug"]
    assert pub.title == "やさしい解説"
    assert pub.blocks[0]["type"] == "heading"


async def test_import_restores_own_publication_comment(db_session: AsyncSession) -> None:
    """Task 25: バックアップの本人 own コメントが移行先ユーザーへ復元される。

    export 側が本人 own コメントだけを含めるため、payload には第三者コメントが存在しない。
    復元後は投稿者が移行先ユーザーへ付け替えられ、publication へ正しく紐づく。
    """
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])

    # payload には本人 own の 1 件だけが含まれる(第三者コメントは export で除外済み)。
    assert len(payload["publication_comments"]) == 1
    assert payload["publication_comments"][0]["id"] == src["own_comment_id"]
    exported_ids = {c["id"] for c in payload["publication_comments"]}
    assert src["third_party_comment_id"] not in exported_ids

    await _delete_source_user(db_session, src["user_id"])
    target = await _make_user(db_session)

    summary = await import_data_json(db_session, target["user_id"], payload)
    assert summary["failed"] == [], summary["failed"]
    assert summary["created"].get("publication_comments", 0) == 1

    restored = (
        (
            await db_session.execute(
                select(PublicationComment).where(
                    PublicationComment.user_id == target["user_id"]
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(restored) == 1
    comment = restored[0]
    assert comment.body == "自分のコメント"
    assert comment.block_id == "0"
    # 投稿者は移行先ユーザーへ付け替えられる。
    assert str(comment.user_id) == target["user_id"]
    # 第三者コメントは復元されない。
    third = await db_session.get(PublicationComment, src["third_party_comment_id"])
    assert third is None


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


# ---------------------------------------------------------------------------
# Task 3: 既存データ不変 — settings マージ・DB 行不変・冪等二重取り込み
# ---------------------------------------------------------------------------


async def test_import_preserves_target_settings(db_session: AsyncSession) -> None:
    """取り込み先にある settings キーはバックアップで上書きされない。
    バックアップ側のみにある新規キーだけが追加される。"""
    # 取り込み元ユーザーを作ってバックアップ payload を取得
    src = await _seed_user_data(db_session)
    # バックアップ payload に settings を混入
    payload = await _detached_payload(db_session, src["user_id"])
    payload["settings"] = {
        "display": {"theme": "light", "font_size": 14},
        "new_section": {"key": "from_backup"},
    }
    await _delete_source_user(db_session, src["user_id"])

    # 取り込み先ユーザーを既存 settings で作る(theme は "dark" = バックアップと意図的に異なる)
    target_id = str(uuid.uuid4())
    target_user = User(
        id=target_id,
        email=f"{uuid.uuid4().hex}@t.test",
        settings={"display": {"theme": "dark"}, "existing_key": "target value"},
    )
    db_session.add(target_user)
    await db_session.commit()

    await import_data_json(db_session, target_id, payload)
    await db_session.refresh(target_user)

    # 既存キーは変わっていない
    assert target_user.settings["display"]["theme"] == "dark", (
        "import must not overwrite existing settings keys"
    )
    assert target_user.settings["existing_key"] == "target value"
    # バックアップ側の新規セクションは追加される
    assert target_user.settings.get("new_section") == {"key": "from_backup"}, (
        "new keys from backup should be merged in"
    )
    # バックアップ側の display.font_size は既存 display dict に追加される
    assert target_user.settings["display"].get("font_size") == 14


async def test_import_preserves_existing_notes_and_resources(db_session: AsyncSession) -> None:
    """インポートは既存の Note / ResourceLink 行を UPDATE しない(skip する)。"""
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])
    await _delete_source_user(db_session, src["user_id"])

    target = await _make_user(db_session)

    # 1 回目: 新規行として挿入される
    summary1 = await import_data_json(db_session, target["user_id"], payload)
    assert summary1["failed"] == [], summary1["failed"]

    # 取り込み後の Note を取得して body_md を変更しておく(target が後で編集した想定)
    from alinea_core.db.models import Note, ResourceLink

    notes = (await db_session.execute(select(Note))).scalars().all()
    assert notes, "notes should have been imported"
    note = notes[0]
    original_note_id = note.id
    note.body_md = "target value"
    await db_session.commit()

    # 2 回目: 同じ payload を再取り込み
    summary2 = await import_data_json(db_session, target["user_id"], payload)
    assert summary2["failed"] == [], summary2["failed"]

    # Note は skip され、target が後で編集した body_md は上書きされない
    await db_session.refresh(note)
    assert note.body_md == "target value", (
        "existing note body_md must not be overwritten on re-import"
    )
    assert summary2["skipped"]["notes"] >= 1


async def test_idempotent_merge_double_import(db_session: AsyncSession) -> None:
    """同じバックアップを 2 回取り込んでも created が 0 になる(完全冪等)。"""
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])
    payload["settings"] = {"display": {"theme": "light"}, "new_section": {"val": 1}}
    await _delete_source_user(db_session, src["user_id"])

    target_id = str(uuid.uuid4())
    db_session.add(User(id=target_id, email=f"{uuid.uuid4().hex}@t.test"))
    await db_session.commit()

    summary1 = await import_data_json(db_session, target_id, payload)
    assert summary1["failed"] == [], summary1["failed"]
    assert summary1["created"]["library"] >= 1

    summary2 = await import_data_json(db_session, target_id, payload)
    assert summary2["failed"] == [], summary2["failed"]
    # 2 回目は何も created されない
    assert summary2["created"]["library"] == 0
    assert summary2["created"]["notes"] == 0
    assert summary2["created"]["resources"] == 0


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


async def test_import_all_columns_roundtrip(db_session: AsyncSession) -> None:
    """Paper と LibraryItem の全列が export → import 後に同値で戻ることを検証する。"""
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])
    await _delete_source_user(db_session, src["user_id"])
    target = await _make_user(db_session)

    summary = await import_data_json(db_session, target["user_id"], payload)
    assert summary["failed"] == [], summary["failed"]

    items = (
        (await db_session.execute(
            select(LibraryItem).where(LibraryItem.user_id == target["user_id"])
        ))
        .scalars()
        .all()
    )
    assert len(items) == 1
    item = items[0]

    lib_entry = payload["library"][0]

    # Paper フィールドは paper から確認
    from alinea_core.db.models import Paper as PaperModel
    paper = await db_session.get(PaperModel, item.paper_id)
    assert paper is not None
    assert paper.abstract == lib_entry["abstract"]
    assert paper.abstract_ja == lib_entry["abstract_ja"]
    assert paper.summary_lines == lib_entry["summary_lines"]
    assert sorted(paper.arxiv_categories) == sorted(lib_entry["arxiv_categories"])
    assert paper.license == (lib_entry["license"] or "unknown")
    assert paper.official_repo_url == lib_entry["official_repo_url"]
    assert paper.visibility == lib_entry["visibility"]
    assert paper.latest_version == lib_entry["latest_version"]
    assert paper.pdf_sha256 == lib_entry["pdf_sha256"]  # None in seed → None restored
    assert paper.thumbnail_key == lib_entry["paper_thumbnail_key"]  # Paper.thumbnail_key

    # LibraryItem フィールドを確認
    assert item.suggested_tags == lib_entry["suggested_tags"]
    assert item.reading_position == lib_entry["reading_position"]
    assert item.queue_order == lib_entry["queue_order"]
    assert item.thumbnail_key == lib_entry["thumbnail_key"]  # LibraryItem.thumbnail_key


async def test_import_paper_license_none_roundtrip(db_session: AsyncSession) -> None:
    """license=None の Paper が import 後も None(DB デフォルト 'unknown')で戻ることを検証する。"""
    src = await _seed_user_data(db_session)
    payload = await _detached_payload(db_session, src["user_id"])

    # license を明示的に None にしてインポートする(old archive compatibility test)
    payload["library"][0]["license"] = None

    await _delete_source_user(db_session, src["user_id"])
    target = await _make_user(db_session)

    summary = await import_data_json(db_session, target["user_id"], payload)
    assert summary["failed"] == [], summary["failed"]

    from alinea_core.db.models import Paper as PaperModel
    items = (
        (await db_session.execute(
            select(LibraryItem).where(LibraryItem.user_id == target["user_id"])
        ))
        .scalars()
        .all()
    )
    assert items
    paper = await db_session.get(PaperModel, items[0].paper_id)
    assert paper is not None
    # None を渡したとき DB が server_default='unknown' を使う(または None が許容される)
    assert paper.license in ("unknown", None), f"unexpected license: {paper.license!r}"


async def test_import_provenance_note_source_chat_message_id(db_session: AsyncSession) -> None:
    """source_chat_message_id がエクスポートペイロードに含まれ、移行後は None になることを検証する。

    INT PK(chat_messages.id)は移行先で再採番されるため NULL に落とす(来歴の軽微な劣化)。
    ペイロードに source_chat_message_id フィールドが存在することは export の直接検査で確認し、
    インポート後には必ず None になることを検証する。
    """
    src = await _seed_user_data(db_session)
    # ペイロードを構築して source_chat_message_id フィールドの存在を確認
    payload = await _detached_payload(db_session, src["user_id"])

    # エクスポートペイロードの Note に source_chat_message_id フィールドが存在する
    assert len(payload["notes"]) >= 1, "payload should have at least one note"
    assert "source_chat_message_id" in payload["notes"][0], (
        "source_chat_message_id field must be serialized in export"
    )

    # payload["notes"][0]["source_chat_message_id"] を非 None 値に差し替えてインポートする
    # (実際に FK を張ると asyncpg の制約で複雑になるため、ここはシリアライズ→復元の検証)
    payload["notes"][0]["source_chat_message_id"] = 99999

    await _delete_source_user(db_session, src["user_id"])
    target = await _make_user(db_session)

    summary = await import_data_json(db_session, target["user_id"], payload)
    assert summary["failed"] == [], summary["failed"]

    target_items = [
        i.id
        for i in (
            await db_session.execute(
                select(LibraryItem).where(LibraryItem.user_id == target["user_id"])
            )
        ).scalars().all()
    ]
    restored_notes = (
        (await db_session.execute(select(Note).where(Note.library_item_id.in_(target_items))))
        .scalars()
        .all()
    )
    # INT PK は移行先で再採番されるため None に落とす
    for note in restored_notes:
        assert note.source_chat_message_id is None, (
            "source_chat_message_id must be None after cross-user import"
        )


# ---------------------------------------------------------------------------
# Task 19: import 完了後、フラグ on なら復元 revision の index_embeddings を enqueue
# ---------------------------------------------------------------------------
def test_import_data_json_reports_restored_revision_ids() -> None:
    """import_data_json は復元した(新規挿入の)revision id を summary に載せる。

    バックアップに埋め込みは含めない(派生データ)。インポート後に feature flag が
    有効なら index job を enqueue するため、復元された revision id が必要になる。
    """
    from alinea_worker.tasks.import_user_data import restored_revision_ids

    summary = {"indexed_revision_ids": ["rev-1", "rev-2"]}
    assert restored_revision_ids(summary) == ["rev-1", "rev-2"]
    assert restored_revision_ids({}) == []


class _FakeArqPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((function, args))


class _Settings:
    def __init__(self, *, enabled: bool) -> None:
        self.semantic_search_enabled = enabled


class _FakeStore:
    """JobStore の enqueue のみを差し替えるフェイク(index_embeddings の kind を live DB に
    書かない=head 0013 の ck_jobs_kind CHECK を避ける。実 DB 永続化は Task 32 で検証)。

    session.get(DocumentRevision) は実 DB を使う(復元済み revision の paper 解決のため)。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.enqueued: list[dict[str, Any]] = []

    async def enqueue(self, *, kind: str, payload: dict[str, Any], **kwargs: Any) -> str:
        self.enqueued.append({"kind": kind, "payload": payload, **kwargs})
        return f"job-{len(self.enqueued)}"


class _FakeJob:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


async def test_import_enqueues_index_when_flag_on(db_session: AsyncSession) -> None:
    """フラグ on のとき、復元した revision ごとに index_embeddings ジョブを enqueue する。"""
    from alinea_worker.tasks.import_user_data import _enqueue_embedding_index_jobs

    # 実 DB に revision を用意して paper 解決を通す。
    src = await _seed_user_data(db_session)
    revision = (await db_session.execute(select(DocumentRevision))).scalars().first()
    assert revision is not None
    summary = {"indexed_revision_ids": [str(revision.id)]}

    store = _FakeStore(db_session)
    arq_pool = _FakeArqPool()
    ctx = {"arq_pool": arq_pool, "settings": _Settings(enabled=True)}

    enqueued = await _enqueue_embedding_index_jobs(ctx, store, _FakeJob(src["user_id"]), summary)

    assert len(enqueued) == 1
    assert store.enqueued[0]["kind"] == "index_embeddings"
    assert store.enqueued[0]["payload"]["revision_id"] == str(revision.id)
    assert store.enqueued[0]["payload"]["paper_id"] == str(revision.paper_id)
    # arq へ run_job を投入している。
    assert [c[0] for c in arq_pool.calls] == ["run_job"]


async def test_import_does_not_enqueue_index_when_flag_off(db_session: AsyncSession) -> None:
    """フラグ off のときは index_embeddings を enqueue しない(既存挙動を変えない)。"""
    from alinea_worker.tasks.import_user_data import _enqueue_embedding_index_jobs

    src = await _seed_user_data(db_session)
    revision = (await db_session.execute(select(DocumentRevision))).scalars().first()
    summary = {"indexed_revision_ids": [str(revision.id)]}

    store = _FakeStore(db_session)
    arq_pool = _FakeArqPool()
    ctx = {"arq_pool": arq_pool, "settings": _Settings(enabled=False)}

    enqueued = await _enqueue_embedding_index_jobs(ctx, store, _FakeJob(src["user_id"]), summary)

    assert enqueued == []
    assert store.enqueued == []
    assert arq_pool.calls == []


async def test_import_job_roundtrip_does_not_enqueue_index_by_default(
    db_session: AsyncSession,
) -> None:
    """既定(フラグ無し ctx)の run_import_full_job は index を enqueue せず既存挙動のまま。"""
    storage = S3Storage()
    src = await _seed_user_data(db_session)
    await storage.put(
        storage.sources_bucket, src["asset_key"], b"%PDF-1.7 fake", content_type="application/pdf"
    )
    archive = await build_export_archive(db_session, src["user_id"], storage)
    upload_key = f"imports/{uuid.uuid4()}.zip"
    await storage.put(storage.assets_bucket, upload_key, archive, content_type="application/zip")

    target = await _make_user(db_session)
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="import", priority="bulk", user_id=target["user_id"], payload={"upload_key": upload_key}
    )
    job = await store.claim(job_id)
    arq_pool = _FakeArqPool()

    # settings 無し ctx → フラグは False 扱い(既定 off)。
    await run_import_full_job({"s3": storage, "arq_pool": arq_pool}, store, job)

    done = await store.get(job_id)
    assert done.status == "succeeded"
    assert arq_pool.calls == []
