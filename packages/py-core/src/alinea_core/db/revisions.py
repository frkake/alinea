"""Paper と DocumentRevision の所属境界を保つ読み込みヘルパ。"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, cast

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_core.db.models import DocumentRevision, LibraryItem, Paper


def normalize_uuid(value: object) -> str | None:
    """UUID を正規形へ変換する。不正値は DB に渡さず ``None`` に閉じる。"""
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def reading_position_revision_id(reading_position: Mapping[str, Any] | None) -> str | None:
    """読書位置から検証済み revision_id を得る。"""
    if not isinstance(reading_position, Mapping):
        return None
    return normalize_uuid(reading_position.get("revision_id"))


async def get_paper_revision(
    session: AsyncSession,
    *,
    paper_id: object,
    revision_id: object,
) -> DocumentRevision | None:
    """指定リビジョンが指定論文に属するときだけ返す。"""
    normalized_paper_id = normalize_uuid(paper_id)
    normalized_revision_id = normalize_uuid(revision_id)
    if normalized_paper_id is None or normalized_revision_id is None:
        return None
    return cast(
        DocumentRevision | None,
        await session.scalar(
            select(DocumentRevision).where(
                DocumentRevision.id == normalized_revision_id,
                DocumentRevision.paper_id == normalized_paper_id,
            )
        ),
    )


async def get_latest_paper_revision(session: AsyncSession, paper: Paper) -> DocumentRevision | None:
    """``paper.latest_revision_id`` を同一論文所属まで含めて検証する。"""
    return await get_paper_revision(
        session,
        paper_id=paper.id,
        revision_id=paper.latest_revision_id,
    )


async def get_preferred_item_revision(
    session: AsyncSession,
    *,
    item: LibraryItem,
    paper: Paper,
) -> DocumentRevision | None:
    """有効な読書位置リビジョンを優先し、なければ同一論文の最新版へ戻す。"""
    item_paper_id = normalize_uuid(item.paper_id)
    paper_id = normalize_uuid(paper.id)
    if item_paper_id is None or paper_id is None or item_paper_id != paper_id:
        return None
    reading_revision_id = reading_position_revision_id(item.reading_position)
    if reading_revision_id is not None:
        reading_revision = await get_paper_revision(
            session,
            paper_id=paper.id,
            revision_id=reading_revision_id,
        )
        if reading_revision is not None:
            return reading_revision
    return await get_latest_paper_revision(session, paper)


async def get_paper_revisions(
    session: AsyncSession,
    pairs: Iterable[tuple[object, object]],
) -> dict[tuple[str, str], DocumentRevision]:
    """複数の ``(paper_id, revision_id)`` を所属検証つきで一括取得する。"""
    normalized = {
        (paper_id, revision_id)
        for raw_paper_id, raw_revision_id in pairs
        if (paper_id := normalize_uuid(raw_paper_id)) is not None
        and (revision_id := normalize_uuid(raw_revision_id)) is not None
    }
    if not normalized:
        return {}
    revisions = (
        (
            await session.execute(
                select(DocumentRevision).where(
                    tuple_(DocumentRevision.paper_id, DocumentRevision.id).in_(normalized)
                )
            )
        )
        .scalars()
        .all()
    )
    return {(str(revision.paper_id), str(revision.id)): revision for revision in revisions}
