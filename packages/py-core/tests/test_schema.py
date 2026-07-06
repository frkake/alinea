"""PY-DB-01〜12: 完全 DDL(plans/02 §4)がスキーマとして正しく投入されていることを検証。

前提: docker-compose の db が起動し、apps/api/alembic upgrade head 済み。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

EXPECTED_TABLES = {
    "users",
    "auth_identities",
    "byok_api_keys",
    "papers",
    "source_assets",
    "document_revisions",
    "block_search_index",
    "translation_sets",
    "translation_units",
    "glossaries",
    "glossary_terms",
    "library_items",
    "chat_threads",
    "chat_messages",
    "notes",
    "annotations",
    "vocab_entries",
    "resource_links",
    "collections",
    "collection_entries",
    "collection_share_tokens",
    "saved_filters",
    "notifications",
    "articles",
    "article_blocks",
    "overview_figures",
    "explainer_figures",
    "reading_sessions",
    "jobs",
    "usage_records",
    "quota_limits",
}


async def _mk_user(db: AsyncSession, email: str | None = None) -> str:
    email = email or f"{uuid.uuid4().hex}@example.com"
    row = await db.execute(text("INSERT INTO users (email) VALUES (:e) RETURNING id"), {"e": email})
    return str(row.scalar_one())


@pytest.mark.asyncio
async def test_py_db_01_all_tables_exist(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    )
    actual = {r[0] for r in rows}
    missing = EXPECTED_TABLES - actual
    assert not missing, f"missing tables: {missing}"


@pytest.mark.asyncio
async def test_py_db_02_primary_key_types(db_session: AsyncSession) -> None:
    # UUID 主キーの代表テーブルと BIGINT identity の明細テーブル
    async def pk_type(table: str) -> str:
        r = await db_session.execute(
            text(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_name = :t AND column_name = 'id'
                """
            ),
            {"t": table},
        )
        return str(r.scalar_one())

    assert await pk_type("users") == "uuid"
    assert await pk_type("papers") == "uuid"
    assert await pk_type("translation_units") == "bigint"
    assert await pk_type("chat_messages") == "bigint"
    assert await pk_type("block_search_index") == "bigint"


@pytest.mark.asyncio
async def test_py_db_03_user_delete_cascades(db_session: AsyncSession) -> None:
    uid = await _mk_user(db_session)
    # library_item 経由の個人資産がカスケードで消えること(paper は public 共有で残す)
    pid = (
        await db_session.execute(text("INSERT INTO papers (title) VALUES ('T') RETURNING id"))
    ).scalar_one()
    liid = (
        await db_session.execute(
            text("INSERT INTO library_items (user_id, paper_id) VALUES (:u,:p) RETURNING id"),
            {"u": uid, "p": pid},
        )
    ).scalar_one()
    await db_session.execute(
        text("INSERT INTO notes (library_item_id, body_md) VALUES (:l, 'note')"),
        {"l": liid},
    )
    await db_session.flush()
    await db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
    await db_session.flush()
    remaining = await db_session.execute(
        text("SELECT count(*) FROM library_items WHERE id = :l"), {"l": liid}
    )
    assert remaining.scalar_one() == 0
    # public paper は残る
    paper_left = await db_session.execute(
        text("SELECT count(*) FROM papers WHERE id = :p"), {"p": pid}
    )
    assert paper_left.scalar_one() == 1
    await db_session.rollback()


@pytest.mark.asyncio
async def test_py_db_04_translation_set_scope_check(db_session: AsyncSession) -> None:
    rev = await _mk_revision(db_session)
    uid = await _mk_user(db_session)
    # shared に user_id を付けると ck_translation_sets_scope_user 違反
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO translation_sets (revision_id, scope, user_id) "
                "VALUES (:r, 'shared', :u)"
            ),
            {"r": rev, "u": uid},
        )
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_py_db_05_translation_set_shared_unique(db_session: AsyncSession) -> None:
    rev = await _mk_revision(db_session)
    await db_session.execute(
        text(
            "INSERT INTO translation_sets (revision_id, style, scope) VALUES (:r,'natural','shared')"
        ),
        {"r": rev},
    )
    await db_session.flush()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO translation_sets (revision_id, style, scope) VALUES (:r,'natural','shared')"
            ),
            {"r": rev},
        )
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_py_db_06_library_status_check(db_session: AsyncSession) -> None:
    uid = await _mk_user(db_session)
    pid = (
        await db_session.execute(text("INSERT INTO papers (title) VALUES ('T') RETURNING id"))
    ).scalar_one()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("INSERT INTO library_items (user_id, paper_id, status) VALUES (:u,:p,'bogus')"),
            {"u": uid, "p": pid},
        )
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_py_db_07_document_quality_check(db_session: AsyncSession) -> None:
    pid = (
        await db_session.execute(text("INSERT INTO papers (title) VALUES ('T') RETURNING id"))
    ).scalar_one()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                """
                INSERT INTO document_revisions
                    (paper_id, parser_version, quality_level, source_format, content)
                VALUES (:p, 'x-1', 'C', 'latex', '{}')
                """
            ),
            {"p": pid},
        )
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_py_db_08_vocab_unique_per_user_term(db_session: AsyncSession) -> None:
    uid = await _mk_user(db_session)
    pid = (
        await db_session.execute(text("INSERT INTO papers (title) VALUES ('T') RETURNING id"))
    ).scalar_one()
    liid = (
        await db_session.execute(
            text("INSERT INTO library_items (user_id, paper_id) VALUES (:u,:p) RETURNING id"),
            {"u": uid, "p": pid},
        )
    ).scalar_one()

    async def add(term: str) -> None:
        await db_session.execute(
            text(
                """
                INSERT INTO vocab_entries
                  (user_id, library_item_id, term, context_anchor, context_sentence)
                VALUES (:u, :l, :t, '{}', 's')
                """
            ),
            {"u": uid, "l": liid, "t": term},
        )
        await db_session.flush()

    await add("Boil Down To")
    with pytest.raises(IntegrityError):
        await add("boil down to")  # lower() で衝突
    await db_session.rollback()


@pytest.mark.asyncio
async def test_py_db_09_resource_dedupe_and_dismiss(db_session: AsyncSession) -> None:
    uid = await _mk_user(db_session)
    pid = (
        await db_session.execute(text("INSERT INTO papers (title) VALUES ('T') RETURNING id"))
    ).scalar_one()
    liid = (
        await db_session.execute(
            text("INSERT INTO library_items (user_id, paper_id) VALUES (:u,:p) RETURNING id"),
            {"u": uid, "p": pid},
        )
    ).scalar_one()

    async def add(status: str) -> None:
        await db_session.execute(
            text(
                """
                INSERT INTO resource_links
                  (library_item_id, status, kind, url, url_normalized)
                VALUES (:l, :s, 'github', 'https://github.com/x/y', 'github.com/x/y')
                """
            ),
            {"l": liid, "s": status},
        )
        await db_session.flush()

    await add("dismissed")
    with pytest.raises(IntegrityError):
        await add("active")  # 同一 (item, url_normalized) は再提案抑止=一意
    await db_session.rollback()


@pytest.mark.asyncio
async def test_py_db_10_share_token_single_active(db_session: AsyncSession) -> None:
    async def new_collection() -> str:
        uid = await _mk_user(db_session)
        cid = (
            await db_session.execute(
                text("INSERT INTO collections (user_id, name) VALUES (:u,'c') RETURNING id"),
                {"u": uid},
            )
        ).scalar_one()
        await db_session.flush()
        return str(cid)

    async def add(cid: str, token: str, status: str) -> None:
        await db_session.execute(
            text(
                "INSERT INTO collection_share_tokens (collection_id, token, status) "
                "VALUES (:c, :t, :s)"
            ),
            {"c": cid, "t": token, "s": status},
        )
        await db_session.flush()

    # (1) active はコレクション毎1本(部分一意)
    cid = await new_collection()
    await add(cid, "tok00001", "active")
    with pytest.raises(IntegrityError):
        await add(cid, "tok00002", "active")
    await db_session.rollback()

    # (2) revoke 後は再発行できる(rollback で消えたので作り直す)
    cid = await new_collection()
    await add(cid, "tok00001", "active")
    await db_session.execute(
        text(
            "UPDATE collection_share_tokens SET status='revoked', revoked_at=now() "
            "WHERE token='tok00001'"
        )
    )
    await db_session.flush()
    await add(cid, "tok00003", "active")
    await db_session.rollback()


@pytest.mark.asyncio
async def test_py_db_11_pgroonga_indexes_present(db_session: AsyncSession) -> None:
    rows = await db_session.execute(
        text(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexdef ILIKE '%pgroonga%'"
        )
    )
    assert rows.scalar_one() == 9


@pytest.mark.asyncio
async def test_py_db_12_annotation_shape_check(db_session: AsyncSession) -> None:
    uid = await _mk_user(db_session)
    pid = (
        await db_session.execute(text("INSERT INTO papers (title) VALUES ('T') RETURNING id"))
    ).scalar_one()
    liid = (
        await db_session.execute(
            text("INSERT INTO library_items (user_id, paper_id) VALUES (:u,:p) RETURNING id"),
            {"u": uid, "p": pid},
        )
    ).scalar_one()
    # bookmark は color/body を持てない
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO annotations (library_item_id, kind, color, anchor) "
                "VALUES (:l, 'bookmark', 'important', '{}')"
            ),
            {"l": liid},
        )
        await db_session.flush()
    await db_session.rollback()


async def _mk_revision(db: AsyncSession) -> str:
    pid = (
        await db.execute(text("INSERT INTO papers (title) VALUES ('T') RETURNING id"))
    ).scalar_one()
    rev = (
        await db.execute(
            text(
                """
                INSERT INTO document_revisions
                    (paper_id, parser_version, quality_level, source_format, content)
                VALUES (:p, 'x-1', 'A', 'latex', '{}') RETURNING id
                """
            ),
            {"p": pid},
        )
    ).scalar_one()
    await db.flush()
    return str(rev)
