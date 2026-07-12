"""Auxiliary translation work scheduling and fail-closed API integration."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.main import app
from alinea_api.routers.translations import (
    _work_request_key,
    get_translations_job_wakeup,
)
from alinea_api.services.session_service import COOKIE_NAME, create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import DocumentRevision, Job, TranslationSet
from alinea_core.db.session import get_sessionmaker
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.jobs.store import JobStore
from alinea_core.translation.pipeline import TranslationPlan, resolve_translation_plan
from factories import (
    make_job,
    make_library_item,
    make_paper,
    make_translation_set,
    make_translation_unit,
    make_user,
)
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession


def _paragraph(block_id: str, text: str) -> Block:
    return Block(id=block_id, type="paragraph", inlines=[Inline(t="text", v=text)])


_TRANSLATABLE_TABLE_RAW = (
    "<table><tr><td>We improve image generation quality.</td><td>99.1</td></tr></table>"
)
_COMPLETE_TABLE_CONTENT: dict[str, Any] = {
    "kind": "table",
    "version": 1,
    "caption": [{"t": "text", "v": "補助表"}],
    "cells": [["画像生成の品質を改善します。", None]],
}


def test_work_request_key_is_unambiguous_and_uses_128_bit_digest() -> None:
    comma_left = _work_request_key(
        "set:part",
        "section",
        "retry",
        ["a,b", "c"],
    )
    comma_right = _work_request_key(
        "set:part",
        "section",
        "retry",
        ["a", "b,c"],
    )
    boundary_shifted = _work_request_key(
        "set",
        "part:section",
        "retry",
        ["a,b", "c"],
    )

    assert len({comma_left, comma_right, boundary_shifted}) == 3
    assert comma_left.startswith("xlate:work:v1:")
    digest = comma_left.removeprefix("xlate:work:v1:")
    assert len(digest) == 32
    int(digest, 16)


async def test_translation_work_expression_indexes_are_installed(
    db_session: AsyncSession,
) -> None:
    request_key_definition = await db_session.scalar(
        text("SELECT pg_get_indexdef(to_regclass('public.ix_jobs_translation_request_key'))")
    )
    legacy_definition = await db_session.scalar(
        text("SELECT pg_get_indexdef(to_regclass('public.ix_jobs_translation_legacy_work'))")
    )

    assert request_key_definition is not None
    assert "payload ->> 'request_key'" in request_key_definition
    assert "kind = 'translation'" in request_key_definition
    assert "IS NOT NULL" in request_key_definition
    assert "status" in request_key_definition
    assert legacy_definition is not None
    assert "payload ->> 'set_id'" in legacy_definition
    assert "payload ->> 'section_id'" in legacy_definition
    assert "payload ->> 'reason'" in legacy_definition
    assert "md5" in legacy_definition
    assert "payload -> 'block_ids'" in legacy_definition
    assert "payload ->> 'table_block_id'" in legacy_definition
    assert "created_at DESC" in legacy_definition
    assert "kind = 'translation'" in legacy_definition
    assert "IS NULL" in legacy_definition

    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    exact_plan = "\n".join(
        row[0]
        for row in (
            await db_session.execute(
                text(
                    "EXPLAIN SELECT id FROM jobs "
                    "WHERE kind = 'translation' "
                    "AND payload->>'request_key' = 'xlate:work:v1:index-probe' "
                    "AND status IN ('queued', 'running', 'waiting_quota') "
                    "ORDER BY created_at DESC, id DESC LIMIT 32 FOR UPDATE"
                )
            )
        ).all()
    )
    legacy_plan = "\n".join(
        row[0]
        for row in (
            await db_session.execute(
                text(
                    "EXPLAIN SELECT id FROM jobs "
                    "WHERE kind = 'translation' "
                    "AND payload->>'request_key' IS NULL "
                    "AND payload->>'set_id' = 'index-probe-set' "
                    "AND payload->>'section_id' = 'index-probe-section' "
                    "AND payload->>'reason' = 'literal' "
                    "AND md5((payload->'block_ids')::text) = "
                    "md5(('[\"index-probe-block\"]'::jsonb)::text) "
                    "AND payload->'block_ids' = '[\"index-probe-block\"]'::jsonb "
                    "AND payload->>'table_block_id' IS NULL "
                    "ORDER BY created_at DESC, id DESC LIMIT 32 FOR UPDATE"
                )
            )
        ).all()
    )
    assert "ix_jobs_translation_request_key" in exact_plan
    assert "ix_jobs_translation_legacy_work" in legacy_plan


def _document() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-main",
                heading=SectionHeading(number="1", title="Main"),
                blocks=[_paragraph("blk-main", "Primary text.")],
            ),
            Section(
                id="sec-aux",
                heading=SectionHeading(number="A", title="Appendix"),
                blocks=[
                    _paragraph("blk-aux", "Auxiliary text."),
                    Block(
                        id="blk-table",
                        type="table",
                        caption=[Inline(t="text", v="Auxiliary table")],
                        raw=_TRANSLATABLE_TABLE_RAW,
                        cells=[["A", "B"]],
                    ),
                ],
            ),
            Section(
                id="sec-other",
                heading=SectionHeading(number="2", title="Other"),
                blocks=[
                    _paragraph("blk-other", "Other text."),
                    Block(id="blk-other-table", type="table", cells=[["X"]]),
                ],
            ),
            Section(
                id="sec-reference",
                heading=SectionHeading(number="R", title="References"),
                blocks=[Block(id="blk-reference", type="reference_entry", raw="Reference")],
            ),
            Section(
                id="sec-empty",
                heading=SectionHeading(number="3", title="Equations"),
                blocks=[Block(id="blk-equation", type="equation", latex="x=y")],
            ),
        ],
    )


@pytest_asyncio.fixture
async def phase_ctx(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
) -> AsyncIterator[SimpleNamespace]:
    user = await make_user(db_session, email=f"aux-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, owner=user, visibility="private")
    content = _document()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=str(paper.id),
        parser_version="test-aux-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(mode="json"),
        stats={"pages": 40},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id
    item = await make_library_item(db_session, user=user, paper=paper, status="reading")
    tset = await make_translation_set(
        db_session,
        revision=revision,
        style="natural",
        scope="personal",
        user=user,
        status="complete",
    )
    primary_plan = TranslationPlan(
        include_appendix=False,
        translate_table_cells=True,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=["sec-main"],
        target_block_ids=["blk-main"],
        pages=40,
    )
    tset.plan = primary_plan.model_dump(mode="json")
    await db_session.commit()

    user_id = str(user.id)
    token = await create_session(redis_client, user_id)
    client.cookies.set(COOKIE_NAME, token)
    wakeups: list[tuple[str, str]] = []

    async def wakeup(job_id: str, queue_name: str) -> None:
        wakeups.append((job_id, queue_name))

    app.dependency_overrides[get_translations_job_wakeup] = lambda: wakeup
    try:
        yield SimpleNamespace(
            user=user,
            user_id=user_id,
            paper=paper,
            revision=revision,
            item=item,
            tset=tset,
            content=content,
            wakeups=wakeups,
        )
    finally:
        app.dependency_overrides.pop(get_translations_job_wakeup, None)
        await db_session.rollback()
        await purge_user(db_session, user_id)
        await db_session.commit()


def _ambiguous_content(content: DocumentContent, duplicate_kind: str) -> dict[str, Any]:
    raw = content.model_dump(mode="json")
    if duplicate_kind == "section":
        duplicate = copy.deepcopy(raw["sections"][2])
        duplicate["id"] = "sec-main"
        raw["sections"].append(duplicate)
    else:
        raw["sections"][2]["blocks"][0]["id"] = "blk-main"
    return raw


@pytest.mark.parametrize("duplicate_kind", ["section", "block"])
@pytest.mark.parametrize(
    "route_kind",
    ["sets", "units", "literal", "section", "retry", "position"],
)
async def test_translation_routes_reject_revision_global_duplicate_ids(
    duplicate_kind: str,
    route_kind: str,
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    phase_ctx.revision.content = _ambiguous_content(phase_ctx.content, duplicate_kind)
    await db_session.commit()
    base = f"/api/revisions/{phase_ctx.revision.id}"
    if route_kind == "sets":
        response = await client.get(f"{base}/translations")
    elif route_kind == "units":
        response = await client.get(
            f"{base}/translations/natural/units", params={"section_id": "sec-main"}
        )
    elif route_kind == "literal":
        response = await client.post(f"{base}/translations", json={"style": "literal"})
    elif route_kind == "section":
        response = await client.post(
            f"/api/translation-sets/{phase_ctx.tset.id}/sections/sec-aux/translate",
            json={},
        )
    elif route_kind == "retry":
        response = await client.post(
            f"/api/translation-sets/{phase_ctx.tset.id}/retry-failed", json={}
        )
    else:
        response = await client.put(
            f"/api/library-items/{phase_ctx.item.id}/position",
            json={
                "revision_id": str(phase_ctx.revision.id),
                "block_id": "blk-main",
                "mode": "translation",
            },
        )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert phase_ctx.wakeups == []
    job_count = await db_session.scalar(
        select(func.count()).select_from(Job).where(Job.paper_id == phase_ctx.paper.id)
    )
    assert job_count == 0
    await db_session.refresh(phase_ctx.item)
    assert phase_ctx.item.reading_position is None


async def _jobs_for_set(db: AsyncSession, set_id: str) -> list[Job]:
    return list(
        (
            await db.execute(
                select(Job)
                .where(Job.kind == "translation", Job.payload["set_id"].astext == set_id)
                .order_by(Job.created_at, Job.id)
            )
        )
        .scalars()
        .all()
    )


async def test_full_section_adds_canonical_auxiliary_without_changing_primary_progress(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-main",
        text_ja="主対象の訳",
    )
    await db_session.commit()
    before = await client.get(f"/api/revisions/{phase_ctx.revision.id}/translations")
    assert before.status_code == 200, before.text
    before_item = next(item for item in before.json()["items"] if item["style"] == "natural")

    response = await client.post(
        f"/api/translation-sets/{phase_ctx.tset.id}/sections/sec-aux/translate",
        json={},
    )

    assert response.status_code == 202, response.text
    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.target_section_ids == ["sec-main"]
    assert plan.target_block_ids == ["blk-main"]
    assert plan.auxiliary_block_ids == ["blk-aux", "blk-table"]
    assert phase_ctx.tset.status == "complete"
    jobs = await _jobs_for_set(db_session, str(phase_ctx.tset.id))
    assert len(jobs) == 1
    assert jobs[0].payload["reason"] == "on_demand"
    assert jobs[0].payload["block_ids"] == ["blk-aux", "blk-table"]

    after = await client.get(f"/api/revisions/{phase_ctx.revision.id}/translations")
    assert after.status_code == 200, after.text
    after_item = next(item for item in after.json()["items"] if item["style"] == "natural")
    assert after_item["status"] == before_item["status"] == "complete"
    assert after_item["progress_pct"] == before_item["progress_pct"] == 100


async def test_table_request_adds_only_direct_table_as_auxiliary(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    response = await client.post(
        f"/api/translation-sets/{phase_ctx.tset.id}/sections/sec-aux/translate",
        json={"block_id": "blk-table"},
    )

    assert response.status_code == 202, response.text
    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.target_block_ids == ["blk-main"]
    assert plan.auxiliary_block_ids == ["blk-table"]
    jobs = await _jobs_for_set(db_session, str(phase_ctx.tset.id))
    assert len(jobs) == 1
    assert jobs[0].payload["reason"] == "table"
    assert jobs[0].payload["block_ids"] == ["blk-table"]
    assert jobs[0].payload["table_block_id"] == "blk-table"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "<table><tr><td>unterminated",
        "<table><tr><td>99.1</td><td>$x^2$</td></tr></table>",
    ],
)
async def test_explicit_table_request_rejects_unsupported_or_targetless_grid_atomically(
    raw: str | None,
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    document = copy.deepcopy(phase_ctx.revision.content)
    table = document["sections"][1]["blocks"][1]
    if raw is None:
        table.pop("raw", None)
    else:
        table["raw"] = raw
    phase_ctx.revision.content = document
    await db_session.commit()

    response = await _request_table(client, phase_ctx.tset)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert await _jobs_for_set(db_session, str(phase_ctx.tset.id)) == []
    assert phase_ctx.wakeups == []
    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(
        DocumentContent.model_validate(document),
        phase_ctx.tset.plan,
        pages=40,
    )
    assert plan.auxiliary_block_ids == []


@pytest.mark.parametrize(
    ("section_id", "payload"),
    [
        ("sec-aux", {"block_id": "blk-other-table"}),
        ("sec-aux", {"block_id": "blk-aux"}),
        ("sec-aux", {"block_id": "blk-unknown"}),
        ("sec-reference", {}),
        ("sec-empty", {}),
    ],
)
async def test_section_translate_rejects_invalid_or_empty_work(
    section_id: str,
    payload: dict[str, str],
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    response = await client.post(
        f"/api/translation-sets/{phase_ctx.tset.id}/sections/{section_id}/translate",
        json=payload,
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert await _jobs_for_set(db_session, str(phase_ctx.tset.id)) == []
    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.auxiliary_block_ids == []


async def _request_full(client: AsyncClient, tset: TranslationSet) -> Any:
    return await client.post(
        f"/api/translation-sets/{tset.id}/sections/sec-aux/translate",
        json={},
    )


async def _request_table(client: AsyncClient, tset: TranslationSet) -> Any:
    return await client.post(
        f"/api/translation-sets/{tset.id}/sections/sec-aux/translate",
        json={"block_id": "blk-table"},
    )


def _exact_work_payload(
    phase_ctx: SimpleNamespace,
    *,
    request_key: str,
    generation: Any,
) -> dict[str, Any]:
    return {
        "set_id": str(phase_ctx.tset.id),
        "section_id": "sec-aux",
        "block_ids": ["blk-aux", "blk-table"],
        "reason": "on_demand",
        "table_block_id": None,
        "request_key": request_key,
        "generation": generation,
    }


async def _make_exact_work_job(
    db: AsyncSession,
    phase_ctx: SimpleNamespace,
    *,
    request_key: str,
    generation: Any,
    status: str,
) -> Job:
    return await make_job(
        db,
        kind="translation",
        status=status,
        user=phase_ctx.user,
        paper=phase_ctx.paper,
        library_item=phase_ctx.item,
        payload=_exact_work_payload(
            phase_ctx,
            request_key=request_key,
            generation=generation,
        ),
    )


async def _add_newer_invalid_exact_jobs(
    db: AsyncSession,
    phase_ctx: SimpleNamespace,
    *,
    request_key: str,
    generations: list[Any] | None = None,
) -> None:
    invalid = generations or [f"bad-{index}" for index in range(32)]
    for generation in invalid:
        await _make_exact_work_job(
            db,
            phase_ctx,
            request_key=request_key,
            generation=generation,
            status="failed",
        )


async def test_full_and_table_requests_have_distinct_stable_work_keys(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    table_response = await _request_table(client, phase_ctx.tset)
    full_response = await _request_full(client, phase_ctx.tset)

    assert table_response.status_code == 202, table_response.text
    assert full_response.status_code == 202, full_response.text
    assert table_response.json()["job_id"] != full_response.json()["job_id"]
    jobs = await _jobs_for_set(db_session, str(phase_ctx.tset.id))
    assert len(jobs) == 2
    by_reason = {job.payload["reason"]: job for job in jobs}
    assert set(by_reason) == {"table", "on_demand"}
    assert by_reason["table"].payload["generation"] == 0
    assert by_reason["on_demand"].payload["generation"] == 0
    assert (
        by_reason["table"].payload["request_key"] != by_reason["on_demand"].payload["request_key"]
    )
    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.auxiliary_block_ids == ["blk-aux", "blk-table"]


async def test_failed_work_advances_generation_more_than_once(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    first_response = await _request_full(client, phase_ctx.tset)
    assert first_response.status_code == 202, first_response.text
    first = await db_session.get(Job, first_response.json()["job_id"])
    assert first is not None
    assert first.payload["generation"] == 0
    request_key = first.payload["request_key"]
    active_reuse = await _request_full(client, phase_ctx.tset)
    assert active_reuse.status_code == 202, active_reuse.text
    assert active_reuse.json()["job_id"] == str(first.id)

    first.status = "failed"
    first.finished_at = dt.datetime.now(dt.UTC)
    await db_session.commit()
    second_response = await _request_full(client, phase_ctx.tset)
    assert second_response.status_code == 202, second_response.text
    assert second_response.json()["job_id"] != str(first.id)
    second = await db_session.get(Job, second_response.json()["job_id"])
    assert second is not None
    assert second.payload["request_key"] == request_key
    assert second.payload["generation"] == 1

    second.status = "failed"
    second.finished_at = dt.datetime.now(dt.UTC)
    await db_session.commit()
    third_response = await _request_full(client, phase_ctx.tset)
    assert third_response.status_code == 202, third_response.text
    third = await db_session.get(Job, third_response.json()["job_id"])
    assert third is not None
    assert third.payload["request_key"] == request_key
    assert third.payload["generation"] == 2
    assert len({str(first.id), str(second.id), str(third.id)}) == 3


async def test_succeeded_work_reuses_only_when_every_block_is_displayable(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    first_response = await _request_full(client, phase_ctx.tset)
    first = await db_session.get(Job, first_response.json()["job_id"])
    assert first is not None
    first.status = "succeeded"
    first.progress = 100
    first.finished_at = dt.datetime.now(dt.UTC)
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-aux",
        text_ja="補助本文",
    )
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-table",
        text_ja="補助表",
        content_ja=_COMPLETE_TABLE_CONTENT,
    )
    await db_session.commit()

    reused = await _request_full(client, phase_ctx.tset)

    assert reused.status_code == 202, reused.text
    assert reused.json()["job_id"] == str(first.id)
    assert len(await _jobs_for_set(db_session, str(phase_ctx.tset.id))) == 1


async def test_succeeded_full_with_legacy_table_caption_advances_when_cells_enabled(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    first_response = await _request_full(client, phase_ctx.tset)
    first = await db_session.get(Job, first_response.json()["job_id"])
    assert first is not None
    first.status = "succeeded"
    first.progress = 100
    first.finished_at = dt.datetime.now(dt.UTC)
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-aux",
        text_ja="補助本文",
    )
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-table",
        text_ja="従来形式の表キャプション",
    )
    await db_session.commit()

    retried = await _request_full(client, phase_ctx.tset)

    assert retried.status_code == 202, retried.text
    assert retried.json()["job_id"] != str(first.id)
    second = await db_session.get(Job, retried.json()["job_id"])
    assert second is not None
    assert second.payload["generation"] == 1


async def test_succeeded_full_with_typed_caption_only_advances_when_cells_enabled(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    first_response = await _request_full(client, phase_ctx.tset)
    first = await db_session.get(Job, first_response.json()["job_id"])
    assert first is not None
    first.status = "succeeded"
    first.progress = 100
    first.finished_at = dt.datetime.now(dt.UTC)
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-aux",
        text_ja="補助本文",
    )
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-table",
        text_ja="翻訳済みキャプション",
        content_ja={
            "kind": "table",
            "version": 1,
            "caption": [{"t": "text", "v": "翻訳済みキャプション"}],
            "cells": None,
        },
    )
    await db_session.commit()

    retried = await _request_full(client, phase_ctx.tset)

    assert retried.status_code == 202, retried.text
    assert retried.json()["job_id"] != str(first.id)
    second = await db_session.get(Job, retried.json()["job_id"])
    assert second is not None
    assert second.payload["generation"] == 1


async def test_succeeded_full_reuses_legacy_table_caption_when_cells_disabled(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    phase_ctx.tset.plan = {
        **phase_ctx.tset.plan,
        "translate_table_cells": False,
    }
    await db_session.commit()
    first_response = await _request_full(client, phase_ctx.tset)
    first = await db_session.get(Job, first_response.json()["job_id"])
    assert first is not None
    first.status = "succeeded"
    first.progress = 100
    first.finished_at = dt.datetime.now(dt.UTC)
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-aux",
        text_ja="補助本文",
    )
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-table",
        text_ja="従来形式の表キャプション",
    )
    await db_session.commit()

    reused = await _request_full(client, phase_ctx.tset)

    assert reused.status_code == 202, reused.text
    assert reused.json()["job_id"] == str(first.id)
    assert len(await _jobs_for_set(db_session, str(phase_ctx.tset.id))) == 1


async def test_succeeded_explicit_table_requires_cells_even_when_plan_disables_them(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    phase_ctx.tset.plan = {
        **phase_ctx.tset.plan,
        "translate_table_cells": False,
    }
    await db_session.commit()
    first_response = await _request_table(client, phase_ctx.tset)
    first = await db_session.get(Job, first_response.json()["job_id"])
    assert first is not None
    first.status = "succeeded"
    first.progress = 100
    first.finished_at = dt.datetime.now(dt.UTC)
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-table",
        text_ja="従来形式の表キャプション",
    )
    await db_session.commit()

    retried = await _request_table(client, phase_ctx.tset)

    assert retried.status_code == 202, retried.text
    assert retried.json()["job_id"] != str(first.id)
    second = await db_session.get(Job, retried.json()["job_id"])
    assert second is not None
    assert second.payload["generation"] == 1


async def test_succeeded_explicit_table_reuses_complete_typed_cells(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    first_response = await _request_table(client, phase_ctx.tset)
    first = await db_session.get(Job, first_response.json()["job_id"])
    assert first is not None
    first.status = "succeeded"
    first.progress = 100
    first.finished_at = dt.datetime.now(dt.UTC)
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-table",
        text_ja="補助表 画像生成の品質を改善します。",
        content_ja=_COMPLETE_TABLE_CONTENT,
    )
    await db_session.commit()

    reused = await _request_table(client, phase_ctx.tset)

    assert reused.status_code == 202, reused.text
    assert reused.json()["job_id"] == str(first.id)
    assert len(await _jobs_for_set(db_session, str(phase_ctx.tset.id))) == 1


async def test_succeeded_incomplete_or_blocked_work_advances_generation(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    first_response = await _request_full(client, phase_ctx.tset)
    first = await db_session.get(Job, first_response.json()["job_id"])
    assert first is not None
    first.status = "succeeded"
    first.progress = 100
    first.finished_at = dt.datetime.now(dt.UTC)
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-aux",
        text_ja="配信停止中",
        quality_flags=["placeholder_mismatch"],
    )
    await db_session.commit()

    retried = await _request_full(client, phase_ctx.tset)

    assert retried.status_code == 202, retried.text
    assert retried.json()["job_id"] != str(first.id)
    second = await db_session.get(Job, retried.json()["job_id"])
    assert second is not None
    assert second.payload["generation"] == 1


async def test_exact_active_legacy_job_is_reused_and_plan_is_persisted(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    legacy = await make_job(
        db_session,
        kind="translation",
        status="queued",
        user=phase_ctx.user,
        paper=phase_ctx.paper,
        library_item=phase_ctx.item,
        idempotency_key=f"xlate:{phase_ctx.tset.id}:sec-aux",
        payload={
            "set_id": str(phase_ctx.tset.id),
            "section_id": "sec-aux",
            "block_ids": ["blk-aux", "blk-table"],
            "reason": "on_demand",
            "table_block_id": None,
        },
    )
    await db_session.commit()

    response = await _request_full(client, phase_ctx.tset)

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == str(legacy.id)
    assert len(await _jobs_for_set(db_session, str(phase_ctx.tset.id))) == 1
    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.auxiliary_block_ids == ["blk-aux", "blk-table"]


async def test_mismatched_legacy_job_is_not_reused(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    legacy = await make_job(
        db_session,
        kind="translation",
        status="queued",
        user=phase_ctx.user,
        paper=phase_ctx.paper,
        library_item=phase_ctx.item,
        idempotency_key=f"xlate:{phase_ctx.tset.id}:sec-aux",
        payload={
            "set_id": str(phase_ctx.tset.id),
            "section_id": "sec-aux",
            "block_ids": ["blk-table"],
            "reason": "table",
            "table_block_id": "blk-table",
        },
    )
    await db_session.commit()

    response = await _request_full(client, phase_ctx.tset)

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] != str(legacy.id)
    jobs = await _jobs_for_set(db_session, str(phase_ctx.tset.id))
    assert len(jobs) == 2
    new_job = next(job for job in jobs if str(job.id) == response.json()["job_id"])
    assert new_job.payload["generation"] == 0
    assert new_job.payload["request_key"]


async def test_legacy_table_marker_must_match_requested_table(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    legacy = await make_job(
        db_session,
        kind="translation",
        status="queued",
        user=phase_ctx.user,
        paper=phase_ctx.paper,
        library_item=phase_ctx.item,
        payload={
            "set_id": str(phase_ctx.tset.id),
            "section_id": "sec-aux",
            "block_ids": ["blk-table"],
            "reason": "table",
            "table_block_id": "blk-other-table",
        },
    )
    await db_session.commit()

    response = await _request_table(client, phase_ctx.tset)

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] != str(legacy.id)
    jobs = await _jobs_for_set(db_session, str(phase_ctx.tset.id))
    assert len(jobs) == 2
    created = next(job for job in jobs if str(job.id) == response.json()["job_id"])
    assert created.payload["generation"] == 0


async def test_legacy_non_table_marker_must_be_null_or_absent(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-main",
        text_ja="blocking",
        quality_flags=["provider_refusal"],
    )
    legacy = await make_job(
        db_session,
        kind="translation",
        status="queued",
        user=phase_ctx.user,
        paper=phase_ctx.paper,
        library_item=phase_ctx.item,
        payload={
            "set_id": str(phase_ctx.tset.id),
            "section_id": "sec-main",
            "block_ids": ["blk-main"],
            "reason": "retry_failed",
            "table_block_id": "blk-main",
        },
    )
    await db_session.commit()

    response = await client.post(
        f"/api/translation-sets/{phase_ctx.tset.id}/retry-failed",
        json={},
    )

    assert response.status_code == 202, response.text
    assert response.json()["job_ids"] != [str(legacy.id)]
    jobs = await _jobs_for_set(db_session, str(phase_ctx.tset.id))
    assert len(jobs) == 2
    created = next(job for job in jobs if str(job.id) in response.json()["job_ids"])
    assert created.payload["generation"] == 0


async def test_scheduler_does_not_lock_unrelated_same_set_job(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    unrelated = await make_job(
        db_session,
        kind="translation",
        status="queued",
        user=phase_ctx.user,
        paper=phase_ctx.paper,
        library_item=phase_ctx.item,
        payload={
            "set_id": str(phase_ctx.tset.id),
            "section_id": "sec-other",
            "block_ids": ["blk-other"],
            "reason": "literal",
            "table_block_id": None,
        },
    )
    await db_session.commit()

    maker = get_sessionmaker()
    async with maker() as locker:
        await locker.execute(select(Job).where(Job.id == unrelated.id).with_for_update())
        try:
            response = await asyncio.wait_for(_request_full(client, phase_ctx.tset), timeout=3)
        finally:
            await locker.rollback()

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] != str(unrelated.id)


async def test_malformed_generation_does_not_hide_maximum_valid_generation(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    block_ids = ["blk-aux", "blk-table"]
    request_key = _work_request_key(
        str(phase_ctx.tset.id),
        "sec-aux",
        "full",
        block_ids,
    )
    for generation in (7, "bad"):
        await make_job(
            db_session,
            kind="translation",
            status="failed",
            user=phase_ctx.user,
            paper=phase_ctx.paper,
            library_item=phase_ctx.item,
            payload={
                "set_id": str(phase_ctx.tset.id),
                "section_id": "sec-aux",
                "block_ids": block_ids,
                "reason": "on_demand",
                "table_block_id": None,
                "request_key": request_key,
                "generation": generation,
            },
        )
    await db_session.commit()

    response = await _request_full(client, phase_ctx.tset)

    assert response.status_code == 202, response.text
    created = await db_session.get(Job, response.json()["job_id"])
    assert created is not None
    assert created.payload["request_key"] == request_key
    assert created.payload["generation"] == 8


async def test_exact_generation_aggregate_sees_valid_history_before_32_invalid_rows(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    block_ids = ["blk-aux", "blk-table"]
    request_key = _work_request_key(
        str(phase_ctx.tset.id),
        "sec-aux",
        "full",
        block_ids,
    )
    older = await _make_exact_work_job(
        db_session,
        phase_ctx,
        request_key=request_key,
        generation=100,
        status="failed",
    )
    older.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    await db_session.commit()
    invalid_values: list[Any] = [
        "101",
        True,
        -1,
        1.5,
        2_147_483_648,
        10**100,
        None,
        [],
        {},
    ]
    await _add_newer_invalid_exact_jobs(
        db_session,
        phase_ctx,
        request_key=request_key,
        generations=[invalid_values[index % len(invalid_values)] for index in range(32)],
    )
    await db_session.commit()

    response = await _request_full(client, phase_ctx.tset)

    assert response.status_code == 202, response.text
    created = await db_session.get(Job, response.json()["job_id"])
    assert created is not None
    assert created.payload["request_key"] == request_key
    assert created.payload["generation"] == 101


async def test_exact_active_job_is_found_before_32_newer_terminal_rows(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    request_key = _work_request_key(
        str(phase_ctx.tset.id),
        "sec-aux",
        "full",
        ["blk-aux", "blk-table"],
    )
    active = await _make_exact_work_job(
        db_session,
        phase_ctx,
        request_key=request_key,
        generation=100,
        status="queued",
    )
    active.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    await db_session.commit()
    await _add_newer_invalid_exact_jobs(
        db_session,
        phase_ctx,
        request_key=request_key,
    )
    invalid_active = await _make_exact_work_job(
        db_session,
        phase_ctx,
        request_key=request_key,
        generation=2_147_483_648,
        status="queued",
    )
    invalid_active.created_at = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    await db_session.commit()

    response = await _request_full(client, phase_ctx.tset)

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == str(active.id)


async def test_exact_succeeded_job_is_found_before_32_newer_terminal_rows(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    request_key = _work_request_key(
        str(phase_ctx.tset.id),
        "sec-aux",
        "full",
        ["blk-aux", "blk-table"],
    )
    succeeded = await _make_exact_work_job(
        db_session,
        phase_ctx,
        request_key=request_key,
        generation=100,
        status="succeeded",
    )
    succeeded.progress = 100
    succeeded.finished_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    succeeded.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    await db_session.commit()
    await _add_newer_invalid_exact_jobs(
        db_session,
        phase_ctx,
        request_key=request_key,
    )
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-aux",
        text_ja="displayable auxiliary",
    )
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-table",
        text_ja="displayable table",
        content_ja=_COMPLETE_TABLE_CONTENT,
    )
    await db_session.commit()

    response = await _request_full(client, phase_ctx.tset)

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == str(succeeded.id)


async def test_legacy_exact_job_is_found_before_33_newer_distractors(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    legacy = await make_job(
        db_session,
        kind="translation",
        status="queued",
        user=phase_ctx.user,
        paper=phase_ctx.paper,
        library_item=phase_ctx.item,
        payload={
            "set_id": str(phase_ctx.tset.id),
            "section_id": "sec-aux",
            "block_ids": ["blk-aux", "blk-table"],
            "reason": "on_demand",
            "table_block_id": None,
        },
    )
    legacy.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    await db_session.commit()
    for index in range(33):
        await make_job(
            db_session,
            kind="translation",
            status="queued",
            user=phase_ctx.user,
            paper=phase_ctx.paper,
            library_item=phase_ctx.item,
            payload={
                "set_id": str(phase_ctx.tset.id),
                "section_id": "sec-aux",
                "block_ids": [f"distractor-{index}"],
                "reason": "on_demand",
                "table_block_id": None,
            },
        )
    await db_session.commit()

    response = await _request_full(client, phase_ctx.tset)

    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == str(legacy.id)


async def test_enqueue_failure_rolls_back_auxiliary_plan_and_job(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_enqueue(_store: JobStore, **_kwargs: Any) -> str:
        raise RuntimeError("injected enqueue failure")

    monkeypatch.setattr(JobStore, "enqueue_uncommitted", fail_enqueue)

    with pytest.raises(RuntimeError, match="injected enqueue failure"):
        await _request_full(client, phase_ctx.tset)

    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.auxiliary_block_ids == []
    assert await _jobs_for_set(db_session, str(phase_ctx.tset.id)) == []
    assert phase_ctx.wakeups == []


async def test_concurrent_identical_requests_create_one_plan_addition_and_job(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    first, second = await asyncio.gather(
        _request_full(client, phase_ctx.tset),
        _request_full(client, phase_ctx.tset),
    )

    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    jobs = await _jobs_for_set(db_session, str(phase_ctx.tset.id))
    assert len(jobs) == 1
    assert jobs[0].payload["generation"] == 0
    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.auxiliary_block_ids == ["blk-aux", "blk-table"]


async def test_retry_backfills_valid_legacy_blocking_units_across_sections(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    for block_id in (
        "blk-main",
        "blk-aux",
        "blk-other",
        "blk-equation",
        "blk-reference",
        "blk-unknown",
    ):
        await make_translation_unit(
            db_session,
            translation_set=phase_ctx.tset,
            block_id=block_id,
            text_ja="blocking",
            quality_flags=["placeholder_mismatch"],
        )
    await db_session.commit()

    response = await client.post(f"/api/translation-sets/{phase_ctx.tset.id}/retry-failed", json={})

    assert response.status_code == 202, response.text
    assert response.json()["block_count"] == 3
    assert len(response.json()["job_ids"]) == 3
    jobs = await _jobs_for_set(db_session, str(phase_ctx.tset.id))
    jobs_by_id = {str(job.id): job for job in jobs}
    ordered_jobs = [jobs_by_id[job_id] for job_id in response.json()["job_ids"]]
    assert [job.payload["section_id"] for job in ordered_jobs] == [
        "sec-main",
        "sec-aux",
        "sec-other",
    ]
    assert [job.payload["block_ids"] for job in ordered_jobs] == [
        ["blk-main"],
        ["blk-aux"],
        ["blk-other"],
    ]
    assert all(job.payload["generation"] == 0 for job in ordered_jobs)
    assert all(job.payload["request_key"] for job in ordered_jobs)
    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.target_block_ids == ["blk-main"]
    assert plan.auxiliary_block_ids == ["blk-aux", "blk-other"]


async def test_retry_failed_job_advances_generation_repeatedly(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    await make_translation_unit(
        db_session,
        translation_set=phase_ctx.tset,
        block_id="blk-main",
        text_ja="blocking",
        quality_flags=["context_overflow"],
    )
    await db_session.commit()

    generations: list[int] = []
    job_ids: list[str] = []
    for _attempt in range(3):
        response = await client.post(
            f"/api/translation-sets/{phase_ctx.tset.id}/retry-failed", json={}
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["job_ids"][0]
        job = await db_session.get(Job, job_id)
        assert job is not None
        generations.append(job.payload["generation"])
        job_ids.append(job_id)
        job.status = "failed"
        job.finished_at = dt.datetime.now(dt.UTC)
        await db_session.commit()

    assert generations == [0, 1, 2]
    assert len(set(job_ids)) == 3


async def test_personal_retry_uses_valid_base_unit_without_duplicate_own_auxiliary(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
) -> None:
    base = await make_translation_set(
        db_session,
        revision=phase_ctx.revision,
        style="natural",
        scope="shared",
        status="complete",
    )
    base.plan = TranslationPlan(
        include_appendix=False,
        translate_table_cells=True,
        suggest_section_selection_over_30_pages=False,
        target_section_ids=["sec-main"],
        target_block_ids=["blk-main"],
        auxiliary_block_ids=["blk-aux"],
        pages=40,
    ).model_dump(mode="json")
    phase_ctx.tset.base_set_id = base.id
    await make_translation_unit(
        db_session,
        translation_set=base,
        block_id="blk-aux",
        text_ja="base blocking",
        quality_flags=["provider_refusal"],
    )
    await db_session.commit()

    response = await client.post(f"/api/translation-sets/{phase_ctx.tset.id}/retry-failed", json={})

    assert response.status_code == 202, response.text
    assert response.json()["block_count"] == 1
    job = await db_session.get(Job, response.json()["job_ids"][0])
    assert job is not None
    assert job.payload["set_id"] == str(phase_ctx.tset.id)
    assert job.payload["section_id"] == "sec-aux"
    assert job.payload["block_ids"] == ["blk-aux"]
    await db_session.refresh(phase_ctx.tset)
    personal_plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert personal_plan.auxiliary_block_ids == []


async def test_retry_multiple_sections_rolls_back_all_jobs_and_plan_on_enqueue_failure(
    client: AsyncClient,
    db_session: AsyncSession,
    phase_ctx: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for block_id in ("blk-main", "blk-aux"):
        await make_translation_unit(
            db_session,
            translation_set=phase_ctx.tset,
            block_id=block_id,
            text_ja="blocking",
            quality_flags=["placeholder_mismatch"],
        )
    await db_session.commit()
    calls = 0
    original = JobStore.enqueue_uncommitted

    async def fail_second(store: JobStore, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second section enqueue failed")
        return await original(store, **kwargs)

    monkeypatch.setattr(JobStore, "enqueue_uncommitted", fail_second)

    with pytest.raises(RuntimeError, match="second section enqueue failed"):
        await client.post(f"/api/translation-sets/{phase_ctx.tset.id}/retry-failed", json={})

    await db_session.refresh(phase_ctx.tset)
    plan = resolve_translation_plan(phase_ctx.content, phase_ctx.tset.plan, pages=40)
    assert plan.auxiliary_block_ids == []
    assert await _jobs_for_set(db_session, str(phase_ctx.tset.id)) == []
    assert phase_ctx.wakeups == []
