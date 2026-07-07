"""M0-25 シード投入テスト(plans/12 §14)。

実 PostgreSQL(docker-compose)に対して ``seed_rectified_flow`` を実行し、
- 論文 / リビジョン / 翻訳セット / ライブラリ項目が投入されること
- ブロック安定 ID が ``document.stable_id`` 由来の決定値であること
- ``--reset`` が冪等(二重実行で重複しない)であること
を検証する。テストは自分が投入したシードのみを teardown で削除する。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.seed import (
    ARXIV_ID,
    DEV_EMAIL,
    FIXTURE_DIR,
    seed_rectified_flow,
)
from yakudoku_core.db.models import DocumentRevision, LibraryItem, Paper, TranslationSet, User
from yakudoku_core.document.blocks import DocumentContent
from yakudoku_core.parsing.block_ids import assign_block_ids

RunSeed = Callable[..., Awaitable[str | None]]


async def _delete_seed(session: AsyncSession) -> None:
    await session.rollback()  # 失敗テストのアボート状態を解消してから掃除する
    dev = (await session.execute(select(User).where(User.email == DEV_EMAIL))).scalars().first()
    if dev is not None:
        await session.execute(
            text(
                "DELETE FROM papers WHERE owner_user_id = :u AND visibility = 'private' "
                "AND title LIKE 'Flow Straight and Fast%'"
            ),
            {"u": dev.id},
        )
    await session.execute(text("DELETE FROM papers WHERE arxiv_id = :a"), {"a": ARXIV_ID})
    await session.commit()


@pytest_asyncio.fixture
async def run_seed(db_session: AsyncSession) -> AsyncIterator[RunSeed]:
    async def _run(
        sample: str = "rectified-flow", *, reset: bool = True, full: bool = False, scale: int = 0
    ) -> str | None:
        assert sample == "rectified-flow"
        return await seed_rectified_flow(db_session, reset=reset, full=full, scale=scale)

    try:
        yield _run
    finally:
        await _delete_seed(db_session)


async def _dev_id(session: AsyncSession) -> str:
    dev = (await session.execute(select(User).where(User.email == DEV_EMAIL))).scalars().first()
    assert dev is not None
    return dev.id


async def _rf_paper_id(session: AsyncSession) -> str:
    pid = await session.scalar(select(Paper.id).where(Paper.arxiv_id == ARXIV_ID))
    assert pid is not None
    return pid


async def test_seed_creates_rectified_flow(db_session: AsyncSession, run_seed: RunSeed) -> None:
    await run_seed("rectified-flow")
    n = await db_session.scalar(
        text("SELECT count(*) FROM papers WHERE arxiv_id = :a"), {"a": ARXIV_ID}
    )
    assert n == 1

    paper_id = await _rf_paper_id(db_session)
    revisions = await db_session.scalar(
        text("SELECT count(*) FROM document_revisions WHERE paper_id = :p"), {"p": paper_id}
    )
    assert revisions == 1
    # latest_revision_id が設定されている
    latest = await db_session.scalar(select(Paper.latest_revision_id).where(Paper.id == paper_id))
    assert latest is not None

    rev_id = await db_session.scalar(
        select(DocumentRevision.id).where(DocumentRevision.paper_id == paper_id)
    )
    sets = await db_session.scalar(
        text("SELECT count(*) FROM translation_sets WHERE revision_id = :r"), {"r": rev_id}
    )
    assert sets >= 2  # natural shared + literal shared (+ personal fork)

    dev_id = await _dev_id(db_session)
    items = await db_session.scalar(
        text("SELECT count(*) FROM library_items WHERE user_id = :u AND paper_id = :p"),
        {"u": dev_id, "p": paper_id},
    )
    assert items == 1
    # reading_position が §2.1 のブロックを指す(plans/12 §14.2)
    pos = await db_session.scalar(
        select(LibraryItem.reading_position).where(
            LibraryItem.user_id == dev_id, LibraryItem.paper_id == paper_id
        )
    )
    assert pos is not None and pos["block_id"] == "blk-2-1-p1-9eca"
    assert pos["revision_id"] == rev_id


async def test_block_ids_are_deterministic(db_session: AsyncSession, run_seed: RunSeed) -> None:
    await run_seed("rectified-flow")
    paper_id = await _rf_paper_id(db_session)
    rev_id = await db_session.scalar(
        select(DocumentRevision.id).where(DocumentRevision.paper_id == paper_id)
    )

    # document.stable_id / block_ids の決定的導出を独立に再計算する。
    content = DocumentContent.model_validate(
        json.loads((FIXTURE_DIR / "document.json").read_text("utf-8"))
    )
    assign_block_ids(content.sections)
    expected = {blk.id for _sec, blk in content.iter_blocks()}
    # 既知の決定値(document/stable_id.derive_block_id 由来)
    assert "blk-2-1-p1-9eca" in expected
    assert "blk-2-1-eq1-ff46" in expected

    db_rows = (
        (
            await db_session.execute(
                text("SELECT block_id FROM block_search_index WHERE revision_id = :r"),
                {"r": rev_id},
            )
        )
        .scalars()
        .all()
    )
    assert set(db_rows) == expected

    # translation_units の block_id もすべて決定値の部分集合
    set_ids = (
        (
            await db_session.execute(
                select(TranslationSet.id).where(TranslationSet.revision_id == rev_id)
            )
        )
        .scalars()
        .all()
    )
    unit_ids = (
        (
            await db_session.execute(
                text("SELECT block_id FROM translation_units WHERE set_id = ANY(:s)"),
                {"s": list(set_ids)},
            )
        )
        .scalars()
        .all()
    )
    assert set(unit_ids) <= expected


async def test_source_fallback_units_present(db_session: AsyncSession, run_seed: RunSeed) -> None:
    """P3 検証用: 未訳(原文フォールバック)の unit が投入される。"""
    await run_seed("rectified-flow")
    paper_id = await _rf_paper_id(db_session)
    rev_id = await db_session.scalar(
        select(DocumentRevision.id).where(DocumentRevision.paper_id == paper_id)
    )
    fallback = await db_session.scalar(
        text(
            "SELECT count(*) FROM translation_units u "
            "JOIN translation_sets s ON s.id = u.set_id "
            "WHERE s.revision_id = :r AND u.text_ja = '' AND 'placeholder_mismatch' = ANY(u.quality_flags)"
        ),
        {"r": rev_id},
    )
    assert fallback >= 1


async def test_full_translation_covers_more(db_session: AsyncSession, run_seed: RunSeed) -> None:
    await run_seed("rectified-flow", full=True)
    paper_id = await _rf_paper_id(db_session)
    rev_id = await db_session.scalar(
        select(DocumentRevision.id).where(DocumentRevision.paper_id == paper_id)
    )
    natural = (
        (
            await db_session.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == rev_id,
                    TranslationSet.style == "natural",
                    TranslationSet.scope == "shared",
                )
            )
        )
        .scalars()
        .first()
    )
    assert natural is not None
    assert natural.status == "complete"
    units = await db_session.scalar(
        text("SELECT count(*) FROM translation_units WHERE set_id = :s"), {"s": natural.id}
    )
    assert units >= 15  # 全対象ブロック(≈19)


async def test_reset_is_idempotent(db_session: AsyncSession, run_seed: RunSeed) -> None:
    await run_seed("rectified-flow", reset=True)
    first_papers = await db_session.scalar(
        text("SELECT count(*) FROM papers WHERE arxiv_id = :a"), {"a": ARXIV_ID}
    )
    rev_id = await db_session.scalar(
        select(DocumentRevision.id)
        .join(Paper, Paper.id == DocumentRevision.paper_id)
        .where(Paper.arxiv_id == ARXIV_ID)
    )
    first_units = await db_session.scalar(
        text(
            "SELECT count(*) FROM translation_units u JOIN translation_sets s ON s.id = u.set_id "
            "WHERE s.revision_id = :r"
        ),
        {"r": rev_id},
    )

    # reset なしの二重実行はスキップ(重複しない)
    skipped = await run_seed("rectified-flow", reset=False)
    assert skipped is None
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM papers WHERE arxiv_id = :a"), {"a": ARXIV_ID}
        )
        == first_papers
    )

    # reset ありの二重実行も 1 件のまま・unit 数も一致
    await run_seed("rectified-flow", reset=True)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM papers WHERE arxiv_id = :a"), {"a": ARXIV_ID}
        )
        == 1
    )
    rev_id2 = await db_session.scalar(
        select(DocumentRevision.id)
        .join(Paper, Paper.id == DocumentRevision.paper_id)
        .where(Paper.arxiv_id == ARXIV_ID)
    )
    second_units = await db_session.scalar(
        text(
            "SELECT count(*) FROM translation_units u JOIN translation_sets s ON s.id = u.set_id "
            "WHERE s.revision_id = :r"
        ),
        {"r": rev_id2},
    )
    assert second_units == first_units


async def test_scale_adds_library_items(db_session: AsyncSession, run_seed: RunSeed) -> None:
    await run_seed("rectified-flow", reset=True, scale=5)
    dev_id = await _dev_id(db_session)
    dummies = await db_session.scalar(
        text(
            "SELECT count(*) FROM papers WHERE owner_user_id = :u AND visibility = 'private' "
            "AND title LIKE 'Flow Straight and Fast%'"
        ),
        {"u": dev_id},
    )
    assert dummies == 5
