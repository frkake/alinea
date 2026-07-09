"""再翻訳系ジョブの worker 実行(M1-22 (b)。plans/06 §11・§8.4)。

``run_translation_job`` の ``reason='retranslate'/'instructed'/'glossary_change'`` を検証する。

- retranslate / instructed: 結果は ``translation_units.proposal`` に保存する(直接上書き
  しない)。``placeholder_mismatch``(トークン検証失敗)の案は保存せず、ジョブを failed に
  する(壊れた訳を見せない。P3)。
- glossary_change: 対象ブロックを対象 ``TranslationSet``(訳語変更で確定済みの personal
  セット)へ直接 UPSERT する(plans/06 §8.4)。

LLM は決定的なプロバイダ(worker conftest の ``ScriptProvider`` / 本ファイルの
``_BrokenProvider``)で差し替え、実通信は発生させない。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from alinea_core.db.models import (
    DocumentRevision,
    LibraryItem,
    Paper,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.jobs.store import JobStore
from alinea_core.search.rebuild import rebuild_block_search_index
from alinea_llm.router import LLMRouter
from alinea_llm.types import LLMRequest, LLMResponse, StreamEvent
from alinea_worker.tasks.translate import run_translation_job
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _content_with_citation() -> DocumentContent:
    """CIT トークンを持つ段落(placeholder_mismatch 誘発用)+ 通常段落。"""
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(
                        id="blk-a",
                        type="paragraph",
                        inlines=[
                            Inline(t="text", v="Rectified flow follows "),
                            Inline(t="citation", ref="bib-1"),
                            Inline(t="text", v=" straightening the path."),
                        ],
                    ),
                    Block(
                        id="blk-b",
                        type="paragraph",
                        inlines=[
                            Inline(t="text", v="The model learns a velocity field over time.")
                        ],
                    ),
                ],
            )
        ],
    )


async def _seed(
    db: AsyncSession,
    *,
    content: DocumentContent,
    glossary_snapshot: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """private Paper + DocumentRevision + personal TranslationSet を作る。"""
    user = User(id=str(uuid.uuid4()), email=f"{_uid()}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=str(uuid.uuid4()), title="Mock Paper", visibility="private", owner_user_id=user.id
    )
    db.add(paper)
    await db.flush()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(),
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    await rebuild_block_search_index(db, str(revision.id), content)
    li = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(li)
    tset = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="natural",
        scope="personal",
        user_id=user.id,
        glossary_snapshot=glossary_snapshot or [],
        status="complete",
    )
    db.add(tset)
    await db.commit()
    return {"user": user, "paper": paper, "revision": revision, "tset": tset, "library_item": li}


async def _enqueue(
    db: AsyncSession,
    *,
    ctx_data: dict[str, Any],
    payload: dict[str, Any],
    priority: str = "interactive",
) -> str:
    store = JobStore(db)
    return await store.enqueue(
        kind="translation",
        payload=payload,
        priority=priority,
        user_id=str(ctx_data["user"].id),
        paper_id=str(ctx_data["paper"].id),
        library_item_id=str(ctx_data["library_item"].id),
    )


# ===========================================================================
# _BrokenProvider: CIT トークンを必ず脱落させる(placeholder_mismatch 誘発)
# ===========================================================================

_TARGET_ID_RE = re.compile(r"^\[([^\]]+)\]", re.MULTILINE)


class _BrokenProvider:
    name = "broken"

    def _ids(self, req: LLMRequest) -> list[str]:
        text = "".join(
            p.text or "" for msg in req.messages if msg.role == "user" for p in msg.parts
        )
        return _TARGET_ID_RE.findall(text)

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        data = {
            "translations": [{"id": bid, "ja": "壊れた訳(トークン脱落)"} for bid in self._ids(req)]
        }
        return LLMResponse(
            text=json.dumps(data, ensure_ascii=False),
            parsed=data,
            provider=self.name,
            model=req.model,
            stop_reason="end",
        )

    async def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(
        self, req: LLMRequest
    ) -> AsyncIterator[StreamEvent]:  # pragma: no cover
        raise NotImplementedError
        yield StreamEvent(type="end")

    async def count_tokens(self, req: LLMRequest) -> int:  # pragma: no cover
        return 1


# ===========================================================================
# retranslate / instructed: proposal に保存(直接上書きしない)
# ===========================================================================


async def test_retranslate_reason_saves_proposal_without_overwriting(
    db_session: AsyncSession, router: LLMRouter
) -> None:
    ctx_data = await _seed(db_session, content=_content_with_citation())
    unit = TranslationUnit(
        set_id=ctx_data["tset"].id,
        block_id="blk-a",
        source_hash="orig-hash",
        content_ja=[{"t": "text", "v": "旧訳"}],
        text_ja="旧訳",
        state="machine",
    )
    db_session.add(unit)
    await db_session.commit()

    job_id = await _enqueue(
        db_session,
        ctx_data=ctx_data,
        payload={
            "set_id": str(ctx_data["tset"].id),
            "block_ids": ["blk-a"],
            "unit_id": str(unit.id),
            "reason": "retranslate",
            "instruction": "",
        },
    )
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_translation_job({"router": router}, store, job)

    await db_session.refresh(unit)
    assert unit.text_ja == "旧訳"  # 直接上書きしない(plans/06 §11.1)
    assert unit.state == "machine"
    assert unit.proposal is not None
    assert unit.proposal["text_ja"]
    assert unit.proposal["model"]
    assert unit.proposal["generated_at"]

    job = await store.get(job_id)
    assert job is not None
    assert job.status == "succeeded"


async def test_instructed_reason_saves_proposal(
    db_session: AsyncSession, router: LLMRouter
) -> None:
    ctx_data = await _seed(db_session, content=_content_with_citation())
    unit = TranslationUnit(
        set_id=ctx_data["tset"].id,
        block_id="blk-b",
        source_hash="orig-hash-b",
        content_ja=[{"t": "text", "v": "旧訳b"}],
        text_ja="旧訳b",
        state="machine",
    )
    db_session.add(unit)
    await db_session.commit()

    job_id = await _enqueue(
        db_session,
        ctx_data=ctx_data,
        payload={
            "set_id": str(ctx_data["tset"].id),
            "block_ids": ["blk-b"],
            "unit_id": str(unit.id),
            "reason": "instructed",
            "instruction": "もっと簡潔に",
        },
    )
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_translation_job({"router": router}, store, job)

    await db_session.refresh(unit)
    assert unit.text_ja == "旧訳b"
    assert unit.proposal is not None

    job = await store.get(job_id)
    assert job is not None
    assert job.status == "succeeded"


async def test_retranslate_placeholder_mismatch_fails_job_without_saving_proposal(
    db_session: AsyncSession,
) -> None:
    ctx_data = await _seed(db_session, content=_content_with_citation())
    unit = TranslationUnit(
        set_id=ctx_data["tset"].id,
        block_id="blk-a",
        source_hash="orig-hash",
        content_ja=[{"t": "text", "v": "旧訳"}],
        text_ja="旧訳",
        state="machine",
    )
    db_session.add(unit)
    await db_session.commit()

    job_id = await _enqueue(
        db_session,
        ctx_data=ctx_data,
        payload={
            "set_id": str(ctx_data["tset"].id),
            "block_ids": ["blk-a"],
            "unit_id": str(unit.id),
            "reason": "retranslate",
            "instruction": "",
        },
    )
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    broken_router = LLMRouter([("broken", "broken-model", _BrokenProvider())])
    await run_translation_job({"router": broken_router}, store, job)

    await db_session.refresh(unit)
    assert unit.proposal is None  # 壊れた案は保存しない(P3)
    assert unit.text_ja == "旧訳"

    job = await store.get(job_id)
    assert job is not None
    assert job.status == "failed"
    error = json.loads(job.error or "{}")
    assert error["code"] == "placeholder_mismatch"


# ===========================================================================
# glossary_change: personal セットへ直接 UPSERT(plans/06 §8.4)
# ===========================================================================


async def test_glossary_change_reason_upserts_units_directly(
    db_session: AsyncSession, router: LLMRouter
) -> None:
    snapshot = [
        {
            "source_term": "Rectified flow",
            "target_term": "整流フロー",
            "policy": "translate",
            "origin": "user",
        }
    ]
    ctx_data = await _seed(db_session, content=_content_with_citation(), glossary_snapshot=snapshot)

    job_id = await _enqueue(
        db_session,
        ctx_data=ctx_data,
        payload={
            "set_id": str(ctx_data["tset"].id),
            "block_ids": ["blk-a", "blk-b"],
            "reason": "glossary_change",
            "term_id": "term-1",
        },
    )
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_translation_job({"router": router}, store, job)

    units = (
        (
            await db_session.execute(
                select(TranslationUnit).where(TranslationUnit.set_id == ctx_data["tset"].id)
            )
        )
        .scalars()
        .all()
    )
    by_block = {u.block_id: u for u in units}
    assert set(by_block) == {"blk-a", "blk-b"}
    assert all(u.state == "machine" for u in by_block.values())
    assert all(u.text_ja for u in by_block.values())

    job = await store.get(job_id)
    assert job is not None
    assert job.status == "succeeded"
    assert job.result["translated"] == 2


async def test_glossary_change_reason_upserts_into_existing_units(
    db_session: AsyncSession, router: LLMRouter
) -> None:
    """既存 unit があっても glossary_change は source_hash 一致で丸ごとスキップしない。

    translate_section の既訳スキップ規則(source_hash 一致でスキップ)を再利用しないことの
    回帰確認(訳語変更は原文不変でも再翻訳が必要。plans/06 §8.4)。
    """
    ctx_data = await _seed(db_session, content=_content_with_citation())
    existing = TranslationUnit(
        set_id=ctx_data["tset"].id,
        block_id="blk-a",
        source_hash="will-be-overwritten",
        content_ja=[{"t": "text", "v": "旧語訳"}],
        text_ja="旧語訳",
        state="machine",
    )
    db_session.add(existing)
    await db_session.commit()

    job_id = await _enqueue(
        db_session,
        ctx_data=ctx_data,
        payload={
            "set_id": str(ctx_data["tset"].id),
            "block_ids": ["blk-a"],
            "reason": "glossary_change",
            "term_id": "term-2",
        },
    )
    store = JobStore(db_session)
    job = await store.claim(job_id)
    assert job is not None
    await run_translation_job({"router": router}, store, job)

    await db_session.refresh(existing)
    assert existing.text_ja != "旧語訳"  # 再翻訳結果で上書きされている

    job = await store.get(job_id)
    assert job is not None
    assert job.status == "succeeded"
