"""B→A 昇格提案の apply 実配線 + ingest ジョブの SSE 進捗発行(M1-22/M1-07 の残接続)。

plans/03 §16.4(通知 action=apply は adopt-revision と同一の内部処理)・plans/05 §4.5・§12.3。

- ``IngestJobPayload.adopt_on_complete``: 通知「変更する」経由の reingest にのみ立つフラグ。
  structuring で新リビジョンが確定した時点で adopt-revision と同一の内部処理
  (``papers.latest_revision_id`` 切替+``reanchor_paper``)を同一ジョブ内トランザクションで
  実行する(§4.5「別ジョブにしない」)。立っていない通常の reingest ではリアンカーが走らない
  ことを回帰テストで固定する(P6: 自動適用はしない)。
- ``IngestDeps.publish`` があれば段階遷移ごとに ``job.progress`` 形の SSE イベントを発行する
  (``routers/jobs.py`` の ``GET /api/jobs/{job_id}/events`` が読む形。InfoPanel.tsx の
  再取り込み進捗トースト用)。

arXiv は worker conftest の ASGI スタブ(``worker_ctx``)を再利用する(実通信なし)。
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Any

import pytest
from alinea_core.db.models import Annotation, DocumentRevision, LibraryItem, Paper, User
from alinea_core.jobs.store import JobStore
from alinea_worker.pipeline import IngestRun
from alinea_worker.tasks.ingest import ingest_paper
from sqlalchemy.ext.asyncio import AsyncSession

# FIXTURE_HTML(conftest)の S1 段落テキスト。旧(B)リビジョンの段落にも同一文言を仕込み、
# reanchor の quote 探索(§4.5 パス 2)が新リビジョンの該当ブロックへ一致することを確認する。
_S1_TEXT = "We present a mock method for testing the ingest pipeline end to end."


def _arxiv_id() -> str:
    n = (int(time.time() * 1000) + random.randint(0, 9999)) % 100000
    return f"{random.randint(1001, 2912)}.{n:05d}"


async def _seed_promotable_paper(db: AsyncSession, *, arxiv_id: str) -> dict[str, Any]:
    """quality B の旧リビジョン + 注釈 1 件を持つ public Paper + LibraryItem を作る。"""
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()

    paper = Paper(
        id=str(uuid.uuid4()),
        arxiv_id=arxiv_id,
        title="B Quality Promotable Paper",
        visibility="public",
    )
    db.add(paper)
    await db.flush()

    old_rev = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version="v1",
        parser_version="pdf-1.0.0",
        quality_level="B",
        source_format="pdf",
        content={
            "quality_level": "B",
            "sections": [
                {
                    "id": "sec-old-1",
                    "heading": {"number": "1", "title": "Introduction"},
                    "blocks": [
                        {
                            "id": "blk-old",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": _S1_TEXT}],
                        }
                    ],
                }
            ],
        },
    )
    db.add(old_rev)
    await db.flush()
    paper.latest_revision_id = old_rev.id

    li = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(li)
    await db.flush()

    ann = Annotation(
        id=str(uuid.uuid4()),
        library_item_id=li.id,
        kind="highlight",
        color="important",
        anchor={
            "revision_id": str(old_rev.id),
            "block_id": "blk-old",
            "start": 0,
            "end": len(_S1_TEXT),
            "quote": _S1_TEXT,
            "side": "source",
        },
    )
    db.add(ann)
    await db.commit()

    return {
        "user_id": str(user.id),
        "paper_id": str(paper.id),
        "old_revision_id": str(old_rev.id),
        "library_item_id": str(li.id),
        "annotation_id": str(ann.id),
    }


class _PublishSpy:
    """``IngestDeps.publish`` の代替。呼び出しをそのまま記録する。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, data: dict[str, Any]) -> None:
        self.calls.append(dict(data))


# ===========================================================================
# adopt_on_complete=True: papers.latest_revision_id 切替 + reanchor_paper(§6.8 と同一処理)
# ===========================================================================


async def test_reingest_adopt_on_complete_switches_and_reanchors(
    db_session: AsyncSession, worker_ctx: dict[str, Any]
) -> None:
    arxiv_id = _arxiv_id()
    seed = await _seed_promotable_paper(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "reingest",
            "source": "arxiv",
            "arxiv_id": arxiv_id,
            "url": None,
            "library_item_id": seed["library_item_id"],
            "adopt_on_complete": True,
        },
        priority="bulk",
        user_id=seed["user_id"],
        paper_id=seed["paper_id"],
        library_item_id=seed["library_item_id"],
    )

    job = await store.claim(job_id)
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(job_id)
    assert job is not None
    assert job.stage == "complete"
    assert job.status == "succeeded"

    paper = await db_session.get(Paper, seed["paper_id"])
    assert paper is not None
    new_revision_id = str(paper.latest_revision_id)
    assert new_revision_id != seed["old_revision_id"]  # 新リビジョンへ切替済み

    new_rev = await db_session.get(DocumentRevision, new_revision_id)
    assert new_rev is not None
    assert new_rev.quality_level == "A"  # arXiv HTML 経路で再取り込み = 品質 A

    ann = await db_session.get(Annotation, seed["annotation_id"])
    assert ann is not None
    assert ann.orphaned is False
    assert ann.anchor["revision_id"] == new_revision_id
    assert ann.anchor["block_id"] != "blk-old"  # quote 探索で新ブロックへ移動(§4.5 パス 2)

    timeline_logs = [entry for entry in (job.log or []) if entry.get("level") == "info"]
    assert any("リアンカー" in str(entry.get("message", "")) for entry in timeline_logs)


async def test_reingest_resume_after_revision_commit_finishes_adoption(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arxiv_id = _arxiv_id()
    seed = await _seed_promotable_paper(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "reingest",
            "source": "arxiv",
            "arxiv_id": arxiv_id,
            "url": None,
            "library_item_id": seed["library_item_id"],
            "adopt_on_complete": True,
        },
        priority="bulk",
        user_id=seed["user_id"],
        paper_id=seed["paper_id"],
        library_item_id=seed["library_item_id"],
    )

    original_reanchor = IngestRun._reanchor_after_adopt

    async def crash_before_reanchor(self: IngestRun, old_revision_id: str) -> None:
        raise RuntimeError(f"simulated crash before reanchor from {old_revision_id}")

    monkeypatch.setattr(IngestRun, "_reanchor_after_adopt", crash_before_reanchor)
    job = await store.claim(job_id)
    assert job is not None
    with pytest.raises(RuntimeError, match="simulated crash"):
        await ingest_paper(worker_ctx, store, job)

    paper = await db_session.get(Paper, seed["paper_id"])
    assert paper is not None
    new_revision_id = str(paper.latest_revision_id)
    assert new_revision_id != seed["old_revision_id"]
    ann = await db_session.get(Annotation, seed["annotation_id"])
    assert ann is not None
    assert ann.anchor["revision_id"] == seed["old_revision_id"]

    monkeypatch.setattr(IngestRun, "_reanchor_after_adopt", original_reanchor)
    await store.fail_with_retry(job_id, {"stage": "structuring", "message": "crash"})
    job = await store.claim(job_id)
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    ann = await db_session.get(Annotation, seed["annotation_id"])
    assert ann is not None
    assert ann.anchor["revision_id"] == new_revision_id

    job = await store.get(job_id)
    assert job is not None
    job.status = "queued"
    job.finished_at = None
    await db_session.commit()
    job = await store.claim(job_id)
    assert job is not None
    await ingest_paper(worker_ctx, store, job)
    ann = await db_session.get(Annotation, seed["annotation_id"])
    assert ann is not None
    assert ann.anchor["revision_id"] == new_revision_id


# ===========================================================================
# adopt_on_complete 無指定(既定 False): 自動適用しない(P6)の回帰固定
# ===========================================================================


async def test_reingest_without_adopt_on_complete_does_not_reanchor(
    db_session: AsyncSession, worker_ctx: dict[str, Any]
) -> None:
    arxiv_id = _arxiv_id()
    seed = await _seed_promotable_paper(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "reingest",
            "source": "arxiv",
            "arxiv_id": arxiv_id,
            "url": None,
            "library_item_id": seed["library_item_id"],
            # adopt_on_complete を渡さない(既定 False)。
        },
        priority="bulk",
        user_id=seed["user_id"],
        paper_id=seed["paper_id"],
        library_item_id=seed["library_item_id"],
    )

    job = await store.claim(job_id)
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(job_id)
    assert job is not None
    assert job.status == "succeeded"

    # リアンカーが走っていない: 注釈は旧リビジョンを指したまま(orphaned にもしない。P3+P6)。
    ann = await db_session.get(Annotation, seed["annotation_id"])
    assert ann is not None
    assert ann.orphaned is False
    assert ann.anchor["revision_id"] == seed["old_revision_id"]
    assert ann.anchor["block_id"] == "blk-old"

    timeline_logs = [entry for entry in (job.log or []) if entry.get("level") == "info"]
    assert not any("リアンカー" in str(entry.get("message", "")) for entry in timeline_logs)


# ===========================================================================
# ingest ジョブの SSE 段階遷移 publish(InfoPanel の再取り込み進捗トースト)
# ===========================================================================


async def test_ingest_publishes_stage_progress_events(
    db_session: AsyncSession, worker_ctx: dict[str, Any], seed_ingest_job: Any
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    spy = _PublishSpy()
    ctx = {**worker_ctx, "publish": spy}
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(ctx, store, job)

    stage_events = [c for c in spy.calls if c.get("type") == "job.progress"]
    assert stage_events, "job.progress イベントが 1 件も publish されていない"
    for event in stage_events:
        assert event["job_id"] == ids["job_id"]
        assert event["user_id"] == ids["user_id"]
        assert "stage" in event and "status" in event and "progress_pct" in event

    stages_seen = [e["stage"] for e in stage_events]
    for expected in ("fetching", "parsing", "structuring", "translating_abstract", "readable"):
        assert expected in stages_seen

    # 完了ナッジ(§21.2)。job_events はこれを受けて DB の succeeded を再確認し done を組む。
    assert any(e["stage"] == "complete" and e["status"] == "succeeded" for e in stage_events)


async def test_ingest_publishes_waiting_quota_status(
    db_session: AsyncSession, worker_ctx: dict[str, Any], seed_ingest_job: Any
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    spy = _PublishSpy()
    ctx = {**worker_ctx, "publish": spy, "translation_quota_limit": 0}
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "waiting_quota"

    waiting_events = [c for c in spy.calls if c.get("status") == "waiting_quota"]
    assert waiting_events
    assert waiting_events[-1]["job_id"] == ids["job_id"]
