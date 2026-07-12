from __future__ import annotations

import uuid

from alinea_core.db.models import DocumentRevision, LibraryItem, Paper, User
from alinea_core.db.revisions import (
    get_latest_paper_revision,
    get_paper_revision,
    get_paper_revisions,
    get_preferred_item_revision,
)
from sqlalchemy.ext.asyncio import AsyncSession


def _id() -> str:
    return str(uuid.uuid4())


async def test_revision_resolvers_enforce_paper_membership_and_reject_invalid_uuid(
    db_session: AsyncSession,
) -> None:
    user = User(id=_id(), email=f"revision-{uuid.uuid4().hex}@example.test")
    db_session.add(user)
    await db_session.flush()
    own_paper = Paper(id=_id(), title="Owned", visibility="private", owner_user_id=user.id)
    foreign_paper = Paper(id=_id(), title="Foreign", visibility="private", owner_user_id=user.id)
    db_session.add_all([own_paper, foreign_paper])
    await db_session.flush()
    own_revision = DocumentRevision(
        id=_id(),
        paper_id=own_paper.id,
        parser_version="test",
        quality_level="B",
        source_format="pdf",
        content={"quality_level": "B", "sections": []},
    )
    foreign_revision = DocumentRevision(
        id=_id(),
        paper_id=foreign_paper.id,
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add_all([own_revision, foreign_revision])
    await db_session.flush()
    own_paper.latest_revision_id = own_revision.id
    foreign_paper.latest_revision_id = foreign_revision.id
    item = LibraryItem(
        id=_id(),
        user_id=user.id,
        paper_id=own_paper.id,
        reading_position={"revision_id": foreign_revision.id, "block_id": "blk-secret"},
    )
    db_session.add(item)
    await db_session.flush()

    assert (
        await get_paper_revision(db_session, paper_id=own_paper.id, revision_id=own_revision.id)
        is own_revision
    )
    assert (
        await get_paper_revision(db_session, paper_id=own_paper.id, revision_id=foreign_revision.id)
        is None
    )
    assert (
        await get_paper_revision(db_session, paper_id=own_paper.id, revision_id="not-a-uuid")
        is None
    )
    assert await get_preferred_item_revision(db_session, item=item, paper=own_paper) is own_revision

    item.reading_position = {"revision_id": "not-a-uuid", "block_id": "blk-invalid"}
    assert await get_preferred_item_revision(db_session, item=item, paper=own_paper) is own_revision
    assert await get_preferred_item_revision(db_session, item=item, paper=foreign_paper) is None

    loaded = await get_paper_revisions(
        db_session,
        [
            (own_paper.id, own_revision.id),
            (own_paper.id, foreign_revision.id),
            (own_paper.id, "not-a-uuid"),
        ],
    )
    assert set(loaded) == {(str(own_paper.id), str(own_revision.id))}

    own_paper.latest_revision_id = foreign_revision.id
    assert await get_latest_paper_revision(db_session, own_paper) is None
