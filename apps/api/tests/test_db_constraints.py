"""PY-DB-05 / PY-DB-06: library_items・document_revisions の制約(実 PostgreSQL)。

plans/12 §2.4:
- PY-DB-05(integration): library_items は 6 値以外の status・1-5 外の understanding・
  (user, paper) 重複が拒否される。
- PY-DB-06(integration): document_revisions.quality_level が A/B 以外拒否・source_format 3 値。

制約の正は apps/api/alembic/versions/0001_initial_schema.py(plans/02 §4)。
db_session は実 PostgreSQL。無効行は SAVEPOINT 内で試し、拒否を確認後に savepoint を
巻き戻す(外側の有効行は保持される)。全変更は fixture teardown の rollback で破棄。
"""

from __future__ import annotations

from typing import Any

import pytest
from alinea_core.db.models import DocumentRevision, LibraryItem
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _rejects(db: AsyncSession, obj: Any) -> None:
    """obj の挿入が CHECK/一意制約で拒否されることを確認(savepoint 巻き戻し)。"""
    with pytest.raises((IntegrityError, DBAPIError)):
        async with db.begin_nested():
            db.add(obj)
            await db.flush()


# ---------------------------------------------------------------------------
# PY-DB-05: library_items の status(6 値)・understanding(1-5)・(user,paper) 一意
# ---------------------------------------------------------------------------
async def test_library_items_constraints(db_session: AsyncSession, factories: Any) -> None:
    user = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=user)

    # 有効: 6 値の status と 1-5 の understanding は通る。
    for status in ("planned", "up_next", "reading", "done", "reread", "on_hold"):
        other_paper = await factories.make_paper(db_session, owner=user)
        item = LibraryItem(
            user_id=str(user.id), paper_id=str(other_paper.id), status=status, understanding=3
        )
        db_session.add(item)
        await db_session.flush()

    # PY-DB-05: status が 6 値以外 → 拒否。
    await _rejects(
        db_session, LibraryItem(user_id=str(user.id), paper_id=str(paper.id), status="bogus")
    )
    # PY-DB-05: understanding が 1-5 外 → 拒否(0 と 6)。
    await _rejects(
        db_session,
        LibraryItem(
            user_id=str(user.id), paper_id=str(paper.id), status="reading", understanding=0
        ),
    )
    await _rejects(
        db_session,
        LibraryItem(
            user_id=str(user.id), paper_id=str(paper.id), status="reading", understanding=6
        ),
    )

    # PY-DB-05: (user, paper) 重複 → 一意制約で拒否。
    dup_paper = await factories.make_paper(db_session, owner=user)
    db_session.add(LibraryItem(user_id=str(user.id), paper_id=str(dup_paper.id), status="planned"))
    await db_session.flush()
    await _rejects(
        db_session,
        LibraryItem(user_id=str(user.id), paper_id=str(dup_paper.id), status="reading"),
    )


# ---------------------------------------------------------------------------
# PY-DB-06: document_revisions の quality_level(A/B)・source_format(3 値)
# ---------------------------------------------------------------------------
async def test_document_revisions_constraints(db_session: AsyncSession, factories: Any) -> None:
    paper = await factories.make_paper(db_session)

    # 有効: quality A/B x source_format 3 値。
    valid_combos = [
        ("A", "latex"),
        ("A", "arxiv_html"),
        ("B", "pdf"),
    ]
    for i, (quality, fmt) in enumerate(valid_combos):
        rev = DocumentRevision(
            paper_id=str(paper.id),
            source_version=f"v{i}",
            parser_version="test",
            quality_level=quality,
            source_format=fmt,
            content={"quality_level": quality, "sections": []},
            stats={},
        )
        db_session.add(rev)
        await db_session.flush()

    # PY-DB-06: quality_level が A/B 以外 → 拒否。
    await _rejects(
        db_session,
        DocumentRevision(
            paper_id=str(paper.id),
            source_version="vX",
            parser_version="test",
            quality_level="C",
            source_format="arxiv_html",
            content={"quality_level": "A", "sections": []},
            stats={},
        ),
    )
    # PY-DB-06: source_format が 3 値以外 → 拒否。
    await _rejects(
        db_session,
        DocumentRevision(
            paper_id=str(paper.id),
            source_version="vY",
            parser_version="test",
            quality_level="A",
            source_format="epub",
            content={"quality_level": "A", "sections": []},
            stats={},
        ),
    )
