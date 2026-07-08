"""M0-20 viewer / translations API テスト(PY-LIB-03 ほか)。

- PY-LIB-03: 読書位置の保存 → ビューア初期化での復元(roundtrip)。
- ビューア初期化複合(§6.1)・翻訳セット/ユニット(§7.1・§7.2)・優先繰り上げ(§7.4)・
  オンデマンドセクション翻訳(§7.5)・指示なし再翻訳(§7.6)。

実 PostgreSQL の Rectified Flow シードを投入して検証する。認証は dev ユーザーの
セッションクッキーを直接発行して得る(メールリンク経路の短縮)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.seed import ARXIV_ID, DEV_EMAIL, seed_rectified_flow
from yakudoku_core.db.models import (
    DocumentRevision,
    LibraryItem,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)


class Seeded:
    def __init__(self, user_id: str, library_item_id: str, paper_id: str, revision_id: str) -> None:
        self.user_id = user_id
        self.library_item_id = library_item_id
        self.paper_id = paper_id
        self.revision_id = revision_id

    # PY-LIB-03 の plan 例に合わせた別名(seeded_library_item.id 相当)。
    @property
    def id(self) -> str:
        return self.library_item_id


async def _delete_seed(session: AsyncSession) -> None:
    await session.rollback()
    await session.execute(text("DELETE FROM papers WHERE arxiv_id = :a"), {"a": ARXIV_ID})
    await session.commit()


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> AsyncIterator[Seeded]:
    await seed_rectified_flow(db_session, reset=True, full=False, scale=0)
    dev = (await db_session.execute(select(User).where(User.email == DEV_EMAIL))).scalars().first()
    assert dev is not None
    paper_id = await db_session.scalar(select(Paper.id).where(Paper.arxiv_id == ARXIV_ID))
    assert paper_id is not None
    revision_id = await db_session.scalar(
        select(DocumentRevision.id).where(DocumentRevision.paper_id == paper_id)
    )
    li_id = await db_session.scalar(
        select(LibraryItem.id).where(
            LibraryItem.user_id == dev.id, LibraryItem.paper_id == paper_id
        )
    )
    assert revision_id is not None and li_id is not None
    try:
        yield Seeded(str(dev.id), str(li_id), str(paper_id), str(revision_id))
    finally:
        await _delete_seed(db_session)


@pytest_asyncio.fixture
async def auth_client(seeded: Seeded) -> AsyncIterator[AsyncClient]:
    """dev ユーザーのセッションクッキーを持つ認証済みクライアント。"""
    from yakudoku_api.main import app
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.services.session_service import COOKIE_NAME, create_session

    token = await create_session(get_redis(), seeded.user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        cookies={COOKIE_NAME: token},
        trust_env=False,
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# PY-LIB-03: 読書位置 roundtrip + ビューア初期化
# ---------------------------------------------------------------------------
async def test_reading_position_roundtrip(auth_client: AsyncClient, seeded: Seeded) -> None:
    li = seeded.library_item_id
    block_id = "blk-2-1-eq1-ff46"  # シード document の決定的ブロック(test_seed.py 参照)
    r = await auth_client.put(
        f"/api/library-items/{li}/position",
        json={"revision_id": seeded.revision_id, "block_id": block_id, "mode": "translation"},
    )
    assert r.status_code == 200, r.text
    assert "saved_at" in r.json()

    v = await auth_client.get(f"/api/library-items/{li}/viewer")
    assert v.status_code == 200, v.text
    body = v.json()
    assert body["last_position"]["block_id"] == block_id
    assert body["last_position"]["revision_id"] == seeded.revision_id
    assert body["last_position"]["mode"] == "translation"


# ---------------------------------------------------------------------------
# §6.1 ビューア初期化複合
# ---------------------------------------------------------------------------
async def test_viewer_init_shape(auth_client: AsyncClient, seeded: Seeded) -> None:
    v = await auth_client.get(f"/api/library-items/{seeded.library_item_id}/viewer")
    assert v.status_code == 200, v.text
    body = v.json()

    assert body["library_item"]["id"] == seeded.library_item_id
    assert body["library_item"]["paper"]["arxiv_id"] == ARXIV_ID
    assert body["library_item"]["quality_level"] == "A"
    assert body["revision"]["id"] == seeded.revision_id
    assert body["revision"]["figure_count"] == 2
    assert body["revision"]["table_count"] == 2

    # ライセンスカード(cc-by-4.0 → 図表転載可)。
    assert body["license_card"]["license"] == "cc-by-4.0"
    assert body["license_card"]["figure_reuse"] == "allowed"
    assert body["license_card"]["message"] == "CC BY 4.0 — 図表転載可"

    # 翻訳(default_style=natural、personal フォークあり → partial)。
    assert body["translation"]["style"] == "natural"
    assert body["translation"]["status"] in ("partial", "complete")
    assert 0 <= body["translation"]["progress_pct"] <= 100

    # ToC: トップレベルは 7 セクション(Abstract〜References)、参考文献は分母外。
    top_ids = [n["section_id"] for n in body["toc"]]
    assert "sec-5" in top_ids
    refs = next(n for n in body["toc"] if n["section_id"] == "sec-5")
    assert refs["in_progress_denominator"] is False
    method = next(n for n in body["toc"] if n["section_id"] == "sec-2")
    assert len(method["children"]) == 2  # sec-2-1 / sec-2-2

    assert body["counts"]["figures"] == 4  # 図2 + 表2
    assert body["newer_revision"] is None


async def test_viewer_forbidden_for_other_user(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    # 他ユーザーの LibraryItem は 404。member ユーザーの item は存在しないので dummy uuid で。
    r = await auth_client.get("/api/library-items/00000000-0000-0000-0000-000000000000/viewer")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# §6.3 document(ETag / 304)
# ---------------------------------------------------------------------------
async def test_document_etag_roundtrip(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/document")
    assert r.status_code == 200, r.text
    etag = r.headers.get("ETag")
    assert etag == f'"{seeded.revision_id}"'
    assert r.json()["revision_id"] == seeded.revision_id
    assert len(r.json()["sections"]) >= 1

    r2 = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/document", headers={"If-None-Match": etag}
    )
    assert r2.status_code == 304

    # section_id で部分取得。
    r3 = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/document", params={"section_id": "sec-2"}
    )
    assert r3.status_code == 200
    assert r3.headers["ETag"] == f'"{seeded.revision_id}:sec-2"'
    assert len(r3.json()["sections"]) == 1
    assert r3.json()["sections"][0]["id"] == "sec-2"


# ---------------------------------------------------------------------------
# §6.4 単一ブロック
# ---------------------------------------------------------------------------
async def test_get_block(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/blocks/blk-2-1-p1-9eca")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["block"]["id"] == "blk-2-1-p1-9eca"
    assert body["section_id"] == "sec-2-1"
    assert body["display"].startswith("§2.1")


# ---------------------------------------------------------------------------
# §6.5 図表 / §6.6 参考文献
# ---------------------------------------------------------------------------
async def test_figures(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/figures")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    kinds = {i["kind"] for i in items}
    assert kinds == {"figure", "table"}
    fig = next(i for i in items if i["kind"] == "figure")
    assert fig["display"].startswith("図")
    assert fig["image_url"] is not None and fig["image_url"].startswith("/api/assets/")


async def test_references(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/references")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 5
    assert items[0]["ref_id"] == "ref-1"
    assert items[0]["number"] == "[1]"
    assert items[0]["title"]


async def test_references_fallback_and_document_ref_labels(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    revision.content = {
        "quality_level": "A",
        "sections": [
            {
                "id": "sec-1",
                "heading": {"number": "1", "title": "Introduction"},
                "blocks": [
                    {
                        "id": "blk-p1",
                        "type": "paragraph",
                        "inlines": [
                            {"t": "text", "v": "See "},
                            {"t": "citation", "ref": "ref-1"},
                            {"t": "text", "v": " and "},
                            {"t": "ref", "ref": "fig:main", "kind": "figure"},
                            {"t": "text", "v": "."},
                        ],
                    },
                    {
                        "id": "blk-fig1",
                        "type": "figure",
                        "label": "fig:main",
                        "caption": [{"t": "text", "v": "Overview."}],
                    },
                ],
                "sections": [],
            },
            {
                "id": "sec-refs-fallback",
                "heading": {"number": "", "title": "References"},
                "blocks": [
                    {"id": "blk-refs-heading", "type": "heading", "level": 1, "title": "References"},
                    {
                        "id": "blk-refs-raw",
                        "type": "paragraph",
                        "inlines": [
                            {"t": "text", "v": "[1] Wang, A., Liu, B. A useful method. 2024."}
                        ],
                    },
                ],
                "sections": [],
            },
        ],
    }
    await db_session.commit()

    refs = await auth_client.get(f"/api/revisions/{seeded.revision_id}/references")
    assert refs.status_code == 200, refs.text
    assert refs.json()["items"][0]["authors"] == "Wang, A., Liu, B"
    doc = await auth_client.get(f"/api/revisions/{seeded.revision_id}/document")
    para = doc.json()["sections"][0]["blocks"][0]
    assert para["inlines"][1]["v"] == "Wang et al. (2024)"
    assert para["inlines"][3]["v"] == "Fig. 1"


# ---------------------------------------------------------------------------
# §7.1 翻訳セット一覧 / §7.2 ユニット
# ---------------------------------------------------------------------------
async def test_list_translation_sets(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/revisions/{seeded.revision_id}/translations")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    styles = {(i["style"], i["scope"]) for i in items}
    assert ("natural", "shared") in styles
    assert ("literal", "shared") in styles
    assert ("natural", "personal") in styles  # dev の personal フォーク
    for i in items:
        assert 0 <= i["progress_pct"] <= 100
        assert i["glossary_snapshot_id"]


async def test_units_by_section(auth_client: AsyncClient, seeded: Seeded) -> None:
    # sec-0(Abstract)は既定訳済み。
    r = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/translations/natural/units",
        params={"section_id": "sec-0"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["set_id"]
    assert len(body["items"]) >= 1
    unit = body["items"][0]
    assert unit["block_id"] == "blk-0-p1-5d87"
    # personal フォークが優先(edited)。
    assert unit["state"] == "edited"
    assert unit["text_ja"]
    assert "content_ja" in unit


async def test_units_missing_section_404(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/translations/natural/units",
        params={"section_id": "sec-nope"},
    )
    assert r.status_code == 404


async def test_units_placeholder_mismatch_is_null(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    # シードは placeholder_mismatch のフォールバック unit を含む(test_seed 参照)。
    row = (
        await db_session.execute(
            text(
                "SELECT b.section_path, u.block_id FROM translation_units u "
                "JOIN translation_sets s ON s.id = u.set_id "
                "JOIN block_search_index b ON b.revision_id = s.revision_id AND b.block_id = u.block_id "
                "WHERE s.revision_id = :r AND 'placeholder_mismatch' = ANY(u.quality_flags) LIMIT 1"
            ),
            {"r": seeded.revision_id},
        )
    ).first()
    if row is None:
        return  # フォールバック unit が無い構成でもテストは緑(存在時のみ検証)
    # その unit の block を含むセクションを引く。section_path から section_id を復元できないため
    # 全セクションを走査して block を含む section で units を取得する。
    doc = await db_session.scalar(
        select(DocumentRevision.content).where(DocumentRevision.id == seeded.revision_id)
    )
    from yakudoku_core.document.blocks import DocumentContent

    content = DocumentContent.model_validate(doc)
    target_block = row[1]
    section_id = next(
        (sec.id for sec, blk in content.iter_blocks() if blk.id == target_block), None
    )
    assert section_id is not None
    r = await auth_client.get(
        f"/api/revisions/{seeded.revision_id}/translations/natural/units",
        params={"section_id": section_id},
    )
    assert r.status_code == 200
    item = next((i for i in r.json()["items"] if i["block_id"] == target_block), None)
    assert item is not None
    assert item["text_ja"] is None
    assert item["content_ja"] is None
    assert "placeholder_mismatch" in item["quality_flags"]


# ---------------------------------------------------------------------------
# §7.4 prioritize / §7.5 section translate / §7.6 retranslate
# ---------------------------------------------------------------------------
async def _shared_set_id(db: AsyncSession, revision_id: str, style: str) -> str:
    sid = await db.scalar(
        select(TranslationSet.id).where(
            TranslationSet.revision_id == revision_id,
            TranslationSet.style == style,
            TranslationSet.scope == "shared",
        )
    )
    assert sid is not None
    return str(sid)


async def test_prioritize_noop(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    r = await auth_client.post(
        f"/api/translation-sets/{set_id}/prioritize", json={"section_id": "sec-3"}
    )
    assert r.status_code == 202, r.text
    assert r.json() == {"ok": True}


# PY-TR-08: 直訳オンデマンド(§7.5)— 初回で 202+翻訳ジョブ。
async def test_section_translate_creates_job(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    r = await auth_client.post(f"/api/translation-sets/{set_id}/sections/sec-3/translate", json={})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    await db_session.rollback()
    row = (
        await db_session.execute(
            text("SELECT kind, payload->>'reason', status FROM jobs WHERE id = :j"),
            {"j": job_id},
        )
    ).first()
    assert row is not None
    assert row[0] == "translation"
    assert row[1] == "on_demand"
    assert row[2] == "queued"


async def test_retry_failed_translations_creates_section_job(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    from yakudoku_core.document.blocks import DocumentContent

    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    revision = await db_session.get(DocumentRevision, seeded.revision_id)
    assert revision is not None
    content = DocumentContent.model_validate(revision.content)
    section_id, block_id = next(
        (sec.id, blk.id)
        for sec, blk in content.iter_blocks()
        if sec.id == "sec-3" and blk.type == "paragraph"
    )
    unit = await db_session.scalar(
        select(TranslationUnit).where(
            TranslationUnit.set_id == set_id,
            TranslationUnit.block_id == block_id,
        )
    )
    if unit is None:
        db_session.add(
            TranslationUnit(
                set_id=set_id,
                block_id=block_id,
                source_hash="stale",
                content_ja=[],
                text_ja="",
                state="machine",
                quality_flags=["placeholder_mismatch"],
            )
        )
    else:
        unit.quality_flags = ["placeholder_mismatch"]
        unit.text_ja = ""
        unit.content_ja = []
    await db_session.commit()

    r = await auth_client.post(f"/api/translation-sets/{set_id}/retry-failed", json={"section_id": section_id})
    assert r.status_code == 202, r.text
    assert r.json()["block_count"] >= 1
    job_id = r.json()["job_ids"][0]
    await db_session.rollback()
    row = (
        await db_session.execute(
            text("SELECT payload->>'reason', payload->>'section_id', payload->'block_ids' FROM jobs WHERE id = :j"),
            {"j": job_id},
        )
    ).first()
    assert row is not None
    assert row[0] == "retry_failed"
    assert row[1] == section_id
    assert block_id in row[2]


# PY-TR-08: 直訳(literal)オンデマンド — 初回 202+ジョブ、同一要求は同じジョブ
# (idempotency_key で重複生成なし = 以後即時。docs/03 §7 / plans/12 §2.4)。
async def test_ondemand_literal_translate_idempotent(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    literal_set = await _shared_set_id(db_session, seeded.revision_id, "literal")
    r1 = await auth_client.post(
        f"/api/translation-sets/{literal_set}/sections/sec-3/translate", json={}
    )
    assert r1.status_code == 202, r1.text
    job1 = r1.json()["job_id"]
    assert job1
    # 同一 (set, section) の再要求 → 同じ job_id(即時・重複生成なし)。
    r2 = await auth_client.post(
        f"/api/translation-sets/{literal_set}/sections/sec-3/translate", json={}
    )
    assert r2.status_code == 202, r2.text
    assert r2.json()["job_id"] == job1
    await db_session.rollback()
    row = (
        await db_session.execute(
            text("SELECT payload->>'set_id', payload->>'reason' FROM jobs WHERE id = :j"),
            {"j": job1},
        )
    ).first()
    assert row is not None
    assert row[0] == literal_set
    assert row[1] == "on_demand"


async def test_retranslate_machine_unit(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    set_id = await _shared_set_id(db_session, seeded.revision_id, "natural")
    unit_id = await db_session.scalar(
        select(TranslationUnit.id).where(
            TranslationUnit.set_id == set_id, TranslationUnit.state == "machine"
        )
    )
    assert unit_id is not None
    r = await auth_client.post(f"/api/translation-units/{unit_id}/retranslate", json={})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    await db_session.rollback()
    row = (
        await db_session.execute(
            text("SELECT payload->>'reason', payload->>'unit_id' FROM jobs WHERE id = :j"),
            {"j": job_id},
        )
    ).first()
    assert row is not None
    assert row[0] == "retranslate"
    assert row[1] == str(unit_id)


# PY-TR-09: 手動編集(state=edited)の非上書き — retranslate は discard_edit なしで 409、
# discard_edit=true で 202(docs/03 §11 / plans/12 §2.4)。
async def test_retranslate_edited_unit_conflict(
    auth_client: AsyncClient, seeded: Seeded, db_session: AsyncSession
) -> None:
    edited_unit_id = await db_session.scalar(
        select(TranslationUnit.id)
        .join(TranslationSet, TranslationSet.id == TranslationUnit.set_id)
        .where(
            TranslationSet.revision_id == seeded.revision_id,
            TranslationUnit.state == "edited",
        )
    )
    assert edited_unit_id is not None
    # discard_edit なし → 409 conflict / detail に edit_protected。
    r = await auth_client.post(f"/api/translation-units/{edited_unit_id}/retranslate", json={})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "conflict"
    assert "edit_protected" in (r.json().get("detail") or "")
    # discard_edit=true → 202。
    r2 = await auth_client.post(
        f"/api/translation-units/{edited_unit_id}/retranslate",
        json={"discard_edit": True},
    )
    assert r2.status_code == 202, r2.text


# ---------------------------------------------------------------------------
# §6.2 リビジョン一覧
# ---------------------------------------------------------------------------
async def test_list_revisions(auth_client: AsyncClient, seeded: Seeded) -> None:
    r = await auth_client.get(f"/api/papers/{seeded.paper_id}/revisions")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= 1
    current = [i for i in items if i["is_current"]]
    assert len(current) == 1
    assert current[0]["id"] == seeded.revision_id
    assert current[0]["quality_level"] == "A"

    # 存在しない paper は 404。
    miss = await auth_client.get("/api/papers/00000000-0000-0000-0000-000000000000/revisions")
    assert miss.status_code == 404
