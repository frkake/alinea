from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.main import app
from alinea_api.routers.translations import get_translations_job_wakeup
from alinea_api.services.session_service import COOKIE_NAME, create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import DocumentRevision, Job
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.translation import (
    TranslationSettings,
    build_ingest_translation_plan,
    build_translation_plan,
    resolve_translation_plan,
)
from factories import make_job, make_library_item, make_paper, make_translation_set, make_user
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _paragraph(block_id: str, text: str) -> Block:
    return Block(id=block_id, type="paragraph", inlines=[Inline(t="text", v=text)])


def _content() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[_paragraph("blk-1", "Introduction body")],
                sections=[
                    Section(
                        id="sec-1a",
                        heading=SectionHeading(number="1.1", title="Background"),
                        blocks=[_paragraph("blk-1a", "Background body")],
                    )
                ],
            ),
            Section(
                id="sec-2",
                heading=SectionHeading(number="2", title="Method"),
                blocks=[_paragraph("blk-2", "Method body")],
            ),
            Section(
                id="sec-A",
                heading=SectionHeading(number="A", title="Appendix"),
                blocks=[_paragraph("blk-A", "Appendix body")],
            ),
            Section(
                id="sec-ref",
                heading=SectionHeading(number="", title="References"),
                blocks=[Block(id="blk-ref", type="reference_entry", raw="Reference")],
            ),
        ],
    )


@pytest_asyncio.fixture
async def selection_ctx(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
) -> AsyncIterator[SimpleNamespace]:
    user = await make_user(db_session, email=f"selection-{uuid.uuid4().hex}@example.com")
    paper = await make_paper(db_session, owner=user, visibility="private")
    content = _content()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=str(paper.id),
        parser_version="selection-test-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(mode="json"),
        stats={"pages": 42},
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
        status="pending",
    )
    pending = build_ingest_translation_plan(
        content,
        TranslationSettings(suggest_section_selection_over_30_pages=True),
        pages=42,
    )
    tset.plan = pending.model_dump(mode="json")
    checkpoint = {
        "status": "pending",
        "set_id": str(tset.id),
        "revision_id": str(revision.id),
    }
    job = await make_job(
        db_session,
        kind="ingest",
        stage="selecting_sections",
        status="waiting_input",
        progress=52,
        user=user,
        paper=paper,
        library_item=item,
        payload={"_checkpoint": {"section_selection": checkpoint}},
    )
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
            job=job,
            content=content,
            pending=pending,
            wakeups=wakeups,
        )
    finally:
        app.dependency_overrides.pop(get_translations_job_wakeup, None)
        await db_session.rollback()
        await purge_user(db_session, user_id)
        await db_session.commit()


async def test_viewer_projects_required_long_paper_selection_without_false_completion(
    client: AsyncClient,
    selection_ctx: SimpleNamespace,
) -> None:
    response = await client.get(f"/api/library-items/{selection_ctx.item.id}/viewer")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["translation"]["status"] == "pending"
    assert body["translation"]["progress_pct"] == 0
    assert body["translation"]["section_selection"] == {
        "required": True,
        "selectable_section_ids": ["sec-1", "sec-1a", "sec-2", "sec-A"],
        "selected_section_ids": [],
    }
    toc_by_id = {
        node["section_id"]: node
        for top in body["toc"]
        for node in [top, *(top.get("children") or [])]
    }
    assert toc_by_id["sec-1"]["on_demand"] is True
    assert toc_by_id["sec-1a"]["on_demand"] is True
    assert toc_by_id["sec-2"]["on_demand"] is True
    assert toc_by_id["sec-A"]["on_demand"] is True
    assert toc_by_id["sec-ref"]["on_demand"] is False


@pytest.mark.parametrize("visibility", ["private", "public"])
async def test_viewer_exposes_owned_waiting_selection_after_default_style_changes(
    visibility: str,
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    selection_ctx.paper.visibility = visibility
    selection_ctx.user.settings = {
        "translation": {
            "default_style": "literal",
            "suggest_section_selection_over_30_pages": True,
        }
    }
    await db_session.commit()

    response = await client.get(f"/api/library-items/{selection_ctx.item.id}/viewer")

    assert response.status_code == 200, response.text
    translation = response.json()["translation"]
    assert translation["set_id"] == str(selection_ctx.tset.id)
    assert translation["style"] == "natural"
    assert translation["section_selection"]["required"] is True


async def test_viewer_uses_current_default_style_after_selection_wait_ends(
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    literal = await make_translation_set(
        db_session,
        revision=selection_ctx.revision,
        style="literal",
        scope="personal",
        user=selection_ctx.user,
        status="complete",
    )
    literal.plan = build_translation_plan(
        selection_ctx.content,
        TranslationSettings(),
        pages=42,
    ).model_dump(mode="json")
    selection_ctx.job.status = "succeeded"
    selection_ctx.user.settings = {"translation": {"default_style": "literal"}}
    await db_session.commit()

    response = await client.get(f"/api/library-items/{selection_ctx.item.id}/viewer")

    assert response.status_code == 200, response.text
    assert response.json()["translation"]["set_id"] == str(literal.id)
    assert response.json()["translation"]["style"] == "literal"


async def test_viewer_fails_closed_for_corrupt_owned_waiting_selection_checkpoint(
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    checkpoint = copy.deepcopy(selection_ctx.job.payload["_checkpoint"])
    checkpoint["section_selection"] = {
        **checkpoint["section_selection"],
        "set_id": str(uuid.uuid4()),
    }
    selection_ctx.job.payload = {**selection_ctx.job.payload, "_checkpoint": checkpoint}
    await db_session.commit()

    response = await client.get(f"/api/library-items/{selection_ctx.item.id}/viewer")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "conflict"


async def test_viewer_fails_closed_for_non_natural_public_waiting_selection_set(
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    selection_ctx.paper.visibility = "public"
    selection_ctx.tset.style = "literal"
    await db_session.commit()

    response = await client.get(f"/api/library-items/{selection_ctx.item.id}/viewer")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "conflict"


async def test_selection_accepts_subset_in_canonical_order_and_requeues_same_ingest(
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    response = await client.put(
        f"/api/translation-sets/{selection_ctx.tset.id}/section-selection",
        json={"section_ids": ["sec-2", "sec-1a"]},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "set_id": str(selection_ctx.tset.id),
        "job_id": str(selection_ctx.job.id),
        "section_ids": ["sec-1a", "sec-2"],
    }
    await db_session.refresh(selection_ctx.tset)
    await db_session.refresh(selection_ctx.job)
    selected = resolve_translation_plan(
        selection_ctx.content,
        selection_ctx.tset.plan,
        pages=42,
    )
    assert selected.target_section_ids == ["sec-1a", "sec-2"]
    assert selected.target_block_ids == ["blk-1a", "blk-2"]
    assert selection_ctx.job.status == "queued"
    checkpoint = selection_ctx.job.payload["_checkpoint"]["section_selection"]
    assert checkpoint["status"] == "accepted"
    assert checkpoint["set_id"] == str(selection_ctx.tset.id)
    assert checkpoint["revision_id"] == str(selection_ctx.revision.id)
    assert checkpoint["plan"] == selected.model_dump(mode="json")
    assert selection_ctx.wakeups == [(str(selection_ctx.job.id), "alinea:bulk")]


async def test_pending_selection_survives_toc_on_demand_request_unchanged(
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    before_plan = copy.deepcopy(selection_ctx.tset.plan)
    before_checkpoint = copy.deepcopy(selection_ctx.job.payload["_checkpoint"]["section_selection"])

    on_demand = await client.post(
        f"/api/translation-sets/{selection_ctx.tset.id}/sections/sec-2/translate",
        json={},
    )

    assert on_demand.status_code == 202, on_demand.text
    await db_session.refresh(selection_ctx.tset)
    await db_session.refresh(selection_ctx.job)
    assert selection_ctx.tset.plan == before_plan
    assert selection_ctx.job.payload["_checkpoint"]["section_selection"] == before_checkpoint
    assert selection_ctx.job.status == "waiting_input"

    accepted = await client.put(
        f"/api/translation-sets/{selection_ctx.tset.id}/section-selection",
        json={"section_ids": ["sec-2"]},
    )

    assert accepted.status_code == 202, accepted.text
    await db_session.refresh(selection_ctx.job)
    assert selection_ctx.job.status == "queued"
    assert selection_ctx.job.payload["_checkpoint"]["section_selection"]["status"] == "accepted"
    assert selection_ctx.wakeups == [
        (on_demand.json()["job_id"], "alinea:interactive"),
        (str(selection_ctx.job.id), "alinea:bulk"),
    ]


@pytest.mark.parametrize(
    "section_ids",
    [[], ["sec-1", "sec-1"], ["sec-missing"]],
)
async def test_selection_rejects_invalid_targets_without_mutation(
    section_ids: list[str],
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    before = copy.deepcopy(selection_ctx.tset.plan)

    response = await client.put(
        f"/api/translation-sets/{selection_ctx.tset.id}/section-selection",
        json={"section_ids": section_ids},
    )

    assert response.status_code == 422, response.text
    await db_session.refresh(selection_ctx.tset)
    await db_session.refresh(selection_ctx.job)
    assert selection_ctx.tset.plan == before
    assert selection_ctx.job.status == "waiting_input"
    assert selection_ctx.wakeups == []


async def test_identical_selection_retry_is_idempotent_but_different_retry_conflicts(
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    path = f"/api/translation-sets/{selection_ctx.tset.id}/section-selection"
    first = await client.put(path, json={"section_ids": ["sec-2"]})
    same = await client.put(path, json={"section_ids": ["sec-2"]})
    different = await client.put(path, json={"section_ids": ["sec-1"]})

    assert first.status_code == 202
    assert same.status_code == 202
    assert same.json() == first.json()
    assert different.status_code == 409
    assert different.json()["code"] == "conflict"
    assert await db_session.get(Job, selection_ctx.job.id) is not None


async def test_accepted_selection_retry_ignores_normal_auxiliary_plan_growth(
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    path = f"/api/translation-sets/{selection_ctx.tset.id}/section-selection"
    first = await client.put(path, json={"section_ids": ["sec-1"]})
    assert first.status_code == 202, first.text

    on_demand = await client.post(
        f"/api/translation-sets/{selection_ctx.tset.id}/sections/sec-2/translate",
        json={},
    )
    assert on_demand.status_code == 202, on_demand.text
    await db_session.refresh(selection_ctx.tset)
    grown = resolve_translation_plan(
        selection_ctx.content,
        selection_ctx.tset.plan,
        pages=42,
    )
    assert grown.target_section_ids == ["sec-1"]
    assert grown.auxiliary_block_ids == ["blk-2"]

    same = await client.put(path, json={"section_ids": ["sec-1"]})
    different = await client.put(path, json={"section_ids": ["sec-2"]})

    assert same.status_code == 202, same.text
    assert same.json() == first.json()
    assert different.status_code == 409, different.text
    assert different.json()["code"] == "conflict"


async def test_identical_selection_retry_after_ingest_completion_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    selection_ctx: SimpleNamespace,
) -> None:
    path = f"/api/translation-sets/{selection_ctx.tset.id}/section-selection"
    first = await client.put(path, json={"section_ids": ["sec-2"]})
    assert first.status_code == 202

    selection_ctx.job.status = "succeeded"
    await db_session.commit()
    selection_ctx.wakeups.clear()

    retry = await client.put(path, json={"section_ids": ["sec-2"]})

    assert retry.status_code == 202, retry.text
    assert retry.json() == first.json()
    assert selection_ctx.wakeups == []


async def test_selection_rejects_shared_or_foreign_set(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    selection_ctx: SimpleNamespace,
) -> None:
    shared = await make_translation_set(
        db_session,
        revision=selection_ctx.revision,
        style="literal",
        scope="shared",
    )
    shared.plan = selection_ctx.pending.model_dump(mode="json")
    foreign = await make_user(db_session, email=f"selection-foreign-{uuid.uuid4().hex}@example.com")
    await db_session.commit()

    shared_response = await client.put(
        f"/api/translation-sets/{shared.id}/section-selection",
        json={"section_ids": ["sec-1"]},
    )
    foreign_token = await create_session(redis_client, str(foreign.id))
    client.cookies.set(COOKIE_NAME, foreign_token)
    foreign_response = await client.put(
        f"/api/translation-sets/{selection_ctx.tset.id}/section-selection",
        json={"section_ids": ["sec-1"]},
    )

    assert shared_response.status_code == 409
    assert foreign_response.status_code == 404
    await purge_user(db_session, str(foreign.id))
    await db_session.commit()
