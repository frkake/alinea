"""worker 側 translation_complete 発火(plans/05 §12.1)。

- 単体: 同一 job_id で 1 回限り・opt-out(users.settings.notifications.translation_complete
  が明示 false)で発火しない。
- 配線: インライン完全経路(ingest_paper → complete)で通知行が 1 件作られる。
"""

from __future__ import annotations

import uuid
from typing import Any

from alinea_core.db.models import Notification, User
from alinea_core.jobs.store import JobStore
from alinea_worker.notify import fire_translation_complete
from alinea_worker.tasks.ingest import ingest_paper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _make_user(db: AsyncSession, settings: dict[str, Any] | None = None) -> User:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test", settings=settings or {})
    db.add(user)
    await db.commit()
    return user


async def _notes_for(db: AsyncSession, user_id: str) -> list[Notification]:
    rows = await db.execute(
        select(Notification).where(
            Notification.user_id == user_id, Notification.kind == "translation_complete"
        )
    )
    return list(rows.scalars().all())


async def test_fire_translation_complete_once_per_job(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    job_id = str(uuid.uuid4())

    first = await fire_translation_complete(
        db_session,
        None,
        user_id=user.id,
        library_item_id=str(uuid.uuid4()),
        paper_title="Mock Paper",
        job_id=job_id,
    )
    assert first is not None
    # 同一 job_id の再発火は no-op(§2.3「1 回限り保証」)。
    second = await fire_translation_complete(
        db_session,
        None,
        user_id=user.id,
        library_item_id=str(uuid.uuid4()),
        paper_title="Mock Paper",
        job_id=job_id,
    )
    assert second is None
    assert len(await _notes_for(db_session, user.id)) == 1


async def test_fire_translation_complete_respects_opt_out(db_session: AsyncSession) -> None:
    user = await _make_user(db_session, {"notifications": {"translation_complete": False}})
    note = await fire_translation_complete(
        db_session,
        None,
        user_id=user.id,
        library_item_id=str(uuid.uuid4()),
        paper_title="Mock Paper",
        job_id=str(uuid.uuid4()),
    )
    assert note is None
    assert await _notes_for(db_session, user.id) == []


async def test_full_pipeline_fires_translation_complete(
    db_session: AsyncSession, worker_ctx: dict[str, Any], seed_ingest_job: Any
) -> None:
    """インライン完全経路(arq プール無し)で complete 到達時に通知が 1 件入る。"""
    arxiv_id = f"{1001 + uuid.uuid4().int % 1900}.{uuid.uuid4().int % 100_000:05d}"
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    user_id = str(job.user_id)
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None and job.stage == "complete"

    notes = await _notes_for(db_session, user_id)
    assert len(notes) == 1
    assert notes[0].payload["job_id"] == ids["job_id"]
    assert notes[0].payload["library_item_id"] == ids["library_item_id"]
