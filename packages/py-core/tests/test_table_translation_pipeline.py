"""Table pseudo-target integration through the verified translation pipeline."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest
from alinea_core.db.models import DocumentRevision, Job, Paper, TranslationSet, TranslationUnit
from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest.progress import finalize_ingest_if_body_complete
from alinea_core.translation import TranslationSettings, build_translation_plan, encode_block
from alinea_core.translation.pipeline import _refresh_set_status, translate_block, translate_section
from alinea_core.translation.placeholder import TOKEN_RE
from alinea_llm.router import LLMRouter
from alinea_llm.types import LLMRequest, LLMResponse, StreamEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_TARGET_RE = re.compile(r"^\[([^\]]+)\] \(([^)]+)\) (.*)$", re.MULTILINE)


def _echo(encoded: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in TOKEN_RE.finditer(encoded):
        if encoded[cursor : match.start()].strip():
            parts.append("訳文")
        parts.append(match.group(0))
        cursor = match.end()
    if encoded[cursor:].strip() or not parts:
        parts.append("訳文")
    return "".join(parts)


class _TableProvider:
    name = "table-test"

    def __init__(
        self,
        mode: Literal[
            "valid", "duplicate", "unknown", "omission", "broken_math", "recover_math"
        ] = "valid",
    ) -> None:
        self.mode = mode
        self.calls = 0
        self.target_batches: list[list[str]] = []

    def _targets(self, request: LLMRequest) -> list[tuple[str, str, str]]:
        text = "".join(
            part.text or ""
            for message in request.messages
            if message.role == "user"
            for part in message.parts
        )
        return _TARGET_RE.findall(text)

    async def generate_structured(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        targets = self._targets(request)
        self.target_batches.append([target[0] for target in targets])
        translations = []
        for target_id, _block_type, encoded in targets:
            if self.mode == "broken_math" and target_id.endswith("::r1c0"):
                translated = "数式を落とした訳"
            elif self.mode == "recover_math" and self.calls == 1 and target_id.endswith("::r1c0"):
                translated = "数式を落とした訳"
            else:
                translated = _echo(encoded)
            translations.append({"id": target_id, "ja": translated})
        if self.mode == "duplicate" and translations:
            translations.append(dict(translations[0]))
        elif self.mode == "unknown":
            translations.append({"id": "blk-table::unknown", "ja": "不明"})
        elif self.mode == "omission" and translations:
            translations.pop()
        data = {"translations": translations}
        return LLMResponse(
            text=json.dumps(data, ensure_ascii=False),
            parsed=data,
            provider=self.name,
            model=request.model,
            stop_reason="end",
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(
        self, request: LLMRequest
    ) -> AsyncIterator[StreamEvent]:  # pragma: no cover
        raise NotImplementedError
        yield StreamEvent(type="end")

    async def count_tokens(self, request: LLMRequest) -> int:  # pragma: no cover
        return 1


def _router(provider: _TableProvider) -> LLMRouter:
    return LLMRouter([("table-test", "deepseek-v4-flash", provider)])


def _table(*, raw: str | None = None, caption: bool = True) -> dict[str, Any]:
    return {
        "id": "blk-table",
        "type": "table",
        "caption": [{"t": "text", "v": "Benchmark results"}] if caption else [],
        "raw": raw
        or (
            "<table><tr><th>Method label</th><th>$F_1$</th></tr>"
            "<tr><td>Accuracy $x^2$ after training</td><td>91.2</td></tr></table>"
        ),
    }


def _content(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "quality_level": "A",
        "sections": [
            {
                "id": "sec-1",
                "heading": {"number": "1", "title": "Results"},
                "blocks": blocks,
                "sections": [],
            }
        ],
    }


async def _make_set(
    db: AsyncSession,
    *,
    content: dict[str, Any],
    translate_cells: bool,
) -> TranslationSet:
    paper = Paper(
        id=str(uuid.uuid4()),
        title="Table test",
        authors=[],
        visibility="public",
    )
    db.add(paper)
    await db.flush()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        parser_version="table-test",
        quality_level="A",
        source_format="arxiv_html",
        content=content,
        stats={"pages": 1},
    )
    db.add(revision)
    await db.flush()
    plan = build_translation_plan(
        content,
        TranslationSettings(translate_table_cells=translate_cells),
        pages=1,
    )
    tset = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="natural",
        scope="shared",
        glossary_snapshot=[],
        plan=plan.model_dump(mode="json"),
        status="pending",
    )
    db.add(tset)
    await db.commit()
    return tset


async def _unit(db: AsyncSession, set_id: str) -> TranslationUnit:
    return (
        await db.execute(
            select(TranslationUnit).where(
                TranslationUnit.set_id == set_id,
                TranslationUnit.block_id == "blk-table",
            )
        )
    ).scalar_one()


async def test_caption_only_plan_persists_typed_one_unit(
    db_session: AsyncSession,
) -> None:
    content = _content([_table()])
    tset = await _make_set(db_session, content=content, translate_cells=False)
    provider = _TableProvider()

    result = await translate_section(db_session, tset.id, "sec-1", _router(provider))
    row = await _unit(db_session, tset.id)

    assert provider.target_batches == [["blk-table::caption"]]
    assert result.translated == 1
    assert result.progress_pct == 100
    assert row.content_ja == {
        "kind": "table",
        "version": 1,
        "caption": [{"t": "text", "v": "訳文"}],
        "cells": None,
    }
    assert row.text_ja == "訳文"


async def test_translate_block_table_uses_typed_atomic_path() -> None:
    provider = _TableProvider()

    unit = await translate_block(_table(), _router(provider))

    assert provider.target_batches == [["blk-table::caption", "blk-table::r0c0", "blk-table::r1c0"]]
    assert unit.content_ja == {
        "kind": "table",
        "version": 1,
        "caption": [{"t": "text", "v": "訳文"}],
        "cells": [["訳文", None], ["訳文$x^2$訳文", None]],
    }


async def test_cells_enabled_plan_uses_exact_physical_ids_and_preserves_math(
    db_session: AsyncSession,
) -> None:
    content = _content([_table()])
    tset = await _make_set(db_session, content=content, translate_cells=True)
    provider = _TableProvider()

    result = await translate_section(db_session, tset.id, "sec-1", _router(provider))
    row = await _unit(db_session, tset.id)

    assert provider.target_batches == [["blk-table::caption", "blk-table::r0c0", "blk-table::r1c0"]]
    assert result.translated == 1
    assert row.content_ja["kind"] == "table"
    assert row.content_ja["caption"] == [{"t": "text", "v": "訳文"}]
    assert row.content_ja["cells"] == [["訳文", None], ["訳文$x^2$訳文", None]]
    assert "$x^2$" in row.text_ja
    assert "number_mismatch" not in row.quality_flags


async def test_explicit_table_reason_overrides_caption_only_plan_and_hash(
    db_session: AsyncSession,
) -> None:
    table = _table()
    content = _content([table])
    tset = await _make_set(db_session, content=content, translate_cells=False)
    provider = _TableProvider()
    router = _router(provider)

    await translate_section(db_session, tset.id, "sec-1", router)
    caption_hash = (await _unit(db_session, tset.id)).source_hash
    calls_after_caption = provider.calls
    result = await translate_section(
        db_session,
        tset.id,
        "sec-1",
        router,
        block_ids=["blk-table"],
        reason="table",
    )
    row = await _unit(db_session, tset.id)

    assert result.skipped == 0
    assert result.translated == 1
    assert provider.calls > calls_after_caption
    assert provider.target_batches[-1] == [
        "blk-table::caption",
        "blk-table::r0c0",
        "blk-table::r1c0",
    ]
    assert row.source_hash != caption_hash
    assert row.content_ja["cells"] is not None


async def test_explicit_table_reason_does_not_reuse_legacy_caption_unit(
    db_session: AsyncSession,
) -> None:
    table = _table()
    content = _content([table])
    tset = await _make_set(db_session, content=content, translate_cells=False)
    legacy_hash = encode_block(table).source_hash
    db_session.add(
        TranslationUnit(
            set_id=tset.id,
            block_id="blk-table",
            source_hash=legacy_hash,
            content_ja=[{"t": "text", "v": "旧キャプション"}],
            text_ja="旧キャプション",
            state="machine",
            quality_flags=[],
        )
    )
    await db_session.commit()
    provider = _TableProvider()

    result = await translate_section(
        db_session,
        tset.id,
        "sec-1",
        _router(provider),
        block_ids=["blk-table"],
        reason="table",
    )
    row = await _unit(db_session, tset.id)

    assert result.skipped == 0
    assert provider.calls == 1
    assert row.source_hash != legacy_hash
    assert isinstance(row.content_ja, dict)
    assert row.content_ja["cells"] is not None


@pytest.mark.parametrize("state", ["edited", "protected"])
async def test_explicit_table_augments_manual_caption_without_silent_skip(
    state: Literal["edited", "protected"],
    db_session: AsyncSession,
) -> None:
    table = _table()
    content = _content([table])
    tset = await _make_set(db_session, content=content, translate_cells=False)
    db_session.add(
        TranslationUnit(
            set_id=tset.id,
            block_id="blk-table",
            source_hash="manual-caption",
            content_ja=[{"t": "text", "v": "手動キャプション"}],
            text_ja="手動キャプション",
            state=state,
            quality_flags=[],
        )
    )
    await db_session.commit()
    provider = _TableProvider()

    result = await translate_section(
        db_session,
        tset.id,
        "sec-1",
        _router(provider),
        block_ids=["blk-table"],
        reason="table",
    )
    row = await _unit(db_session, tset.id)

    assert result.skipped == 0
    assert result.translated == 1
    assert provider.target_batches == [["blk-table::r0c0", "blk-table::r1c0"]]
    assert row.state == state
    assert row.content_ja["caption"] == [{"t": "text", "v": "手動キャプション"}]
    assert row.content_ja["cells"] == [["訳文", None], ["訳文$x^2$訳文", None]]


@pytest.mark.parametrize(
    ("raw", "expected_cells"),
    [
        ("<table><tr><td colspan='0'>bad</td></tr></table>", None),
        ("<table><tr><td>91.2</td><td>$x$</td></tr></table>", [[None, None]]),
        ("x" * 1_000_000, None),
    ],
)
async def test_no_target_or_unsupported_empty_table_still_persists_primary_unit_without_llm(
    raw: str,
    expected_cells: object,
    db_session: AsyncSession,
) -> None:
    content = _content([_table(raw=raw, caption=False)])
    tset = await _make_set(db_session, content=content, translate_cells=True)
    provider = _TableProvider()

    result = await translate_section(db_session, tset.id, "sec-1", _router(provider))
    row = await _unit(db_session, tset.id)

    assert provider.calls == 0
    assert result.translated == 1
    assert result.progress_pct == 100
    assert row.quality_flags == []
    assert row.text_ja == ""
    assert row.content_ja == {
        "kind": "table",
        "version": 1,
        "caption": None,
        "cells": expected_cells,
    }


@pytest.mark.parametrize("mode", ["duplicate", "unknown", "omission"])
async def test_inexact_structured_ids_fail_the_whole_table_atomically(
    mode: Literal["duplicate", "unknown", "omission"],
    db_session: AsyncSession,
) -> None:
    content = _content([_table()])
    tset = await _make_set(db_session, content=content, translate_cells=True)
    provider = _TableProvider(mode)

    result = await translate_section(db_session, tset.id, "sec-1", _router(provider))
    row = await _unit(db_session, tset.id)

    assert provider.calls >= 3
    assert result.fallback == 1
    assert row.quality_flags == ["placeholder_mismatch"]
    assert row.content_ja == []
    assert row.text_ja == ""


async def test_one_broken_cell_never_persists_partial_typed_table(
    db_session: AsyncSession,
) -> None:
    content = _content([_table()])
    tset = await _make_set(db_session, content=content, translate_cells=True)
    provider = _TableProvider("broken_math")

    result = await translate_section(db_session, tset.id, "sec-1", _router(provider))
    row = await _unit(db_session, tset.id)

    assert result.fallback == 1
    assert row.content_ja == []
    assert row.quality_flags == ["placeholder_mismatch"]


async def test_broken_cell_retry_can_recover_before_atomic_aggregation(
    db_session: AsyncSession,
) -> None:
    content = _content([_table()])
    tset = await _make_set(db_session, content=content, translate_cells=True)
    provider = _TableProvider("recover_math")

    result = await translate_section(db_session, tset.id, "sec-1", _router(provider))
    row = await _unit(db_session, tset.id)

    assert provider.calls == 2
    assert result.fallback == 0
    assert row.content_ja["cells"] == [["訳文", None], ["訳文$x^2$訳文", None]]


async def test_table_cells_remain_one_primary_progress_item(
    db_session: AsyncSession,
) -> None:
    content = _content(
        [
            _table(),
            {
                "id": "blk-paragraph",
                "type": "paragraph",
                "inlines": [{"t": "text", "v": "Other prose"}],
            },
        ]
    )
    tset = await _make_set(db_session, content=content, translate_cells=True)

    result = await translate_section(
        db_session,
        tset.id,
        "sec-1",
        _router(_TableProvider()),
        block_ids=["blk-table"],
    )
    rows = (
        (await db_session.execute(select(TranslationUnit).where(TranslationUnit.set_id == tset.id)))
        .scalars()
        .all()
    )

    assert result.translated == 1
    assert result.progress_pct == 50
    assert [row.block_id for row in rows] == ["blk-table"]


@pytest.mark.parametrize(
    "incomplete_content",
    [
        [{"t": "text", "v": "旧キャプション"}],
        {
            "kind": "table",
            "version": 1,
            "caption": [{"t": "text", "v": "キャプション"}],
            "cells": None,
        },
    ],
)
async def test_refresh_status_excludes_incomplete_table_cell_work(
    incomplete_content: object,
    db_session: AsyncSession,
) -> None:
    content_dict = _content(
        [
            _table(),
            {
                "id": "blk-paragraph",
                "type": "paragraph",
                "inlines": [{"t": "text", "v": "Other prose"}],
            },
        ]
    )
    tset = await _make_set(db_session, content=content_dict, translate_cells=True)
    db_session.add_all(
        [
            TranslationUnit(
                set_id=tset.id,
                block_id="blk-paragraph",
                source_hash="paragraph",
                content_ja=[{"t": "text", "v": "本文"}],
                text_ja="本文",
                state="machine",
                quality_flags=[],
            ),
            TranslationUnit(
                set_id=tset.id,
                block_id="blk-table",
                source_hash="table",
                content_ja=incomplete_content,
                text_ja="キャプション",
                state="machine",
                quality_flags=[],
            ),
        ]
    )
    await db_session.commit()

    status, progress = await _refresh_set_status(
        db_session,
        tset,
        DocumentContent.model_validate(content_dict),
    )

    assert status == "partial"
    assert progress == 50


async def test_ingest_does_not_finalize_with_caption_only_table_when_cells_are_required(
    db_session: AsyncSession,
) -> None:
    content_dict = _content([_table()])
    content = DocumentContent.model_validate(content_dict)
    tset = await _make_set(db_session, content=content_dict, translate_cells=True)
    ingest_job = Job(
        kind="ingest",
        status="running",
        stage="translating_body",
        progress=55,
        payload={},
    )
    db_session.add_all(
        [
            ingest_job,
            TranslationUnit(
                set_id=tset.id,
                block_id="blk-table",
                source_hash="caption-only",
                content_ja={
                    "kind": "table",
                    "version": 1,
                    "caption": [{"t": "text", "v": "キャプション"}],
                    "cells": None,
                },
                text_ja="キャプション",
                state="machine",
                quality_flags=[],
            ),
        ]
    )
    await db_session.commit()

    completed = await finalize_ingest_if_body_complete(
        db_session,
        set_id=str(tset.id),
        ingest_job_id=str(ingest_job.id),
        content=content,
        style="natural",
        source_version="v1",
        appendix_untranslated=False,
    )

    assert completed is False
    await db_session.refresh(tset)
    await db_session.refresh(ingest_job)
    assert tset.status == "partial"
    assert ingest_job.status == "running"


async def test_ingest_coverage_still_finalizes_an_ordinary_blocking_fallback(
    db_session: AsyncSession,
) -> None:
    content_dict = _content(
        [
            {
                "id": "blk-paragraph",
                "type": "paragraph",
                "inlines": [{"t": "text", "v": "Other prose"}],
            }
        ]
    )
    content = DocumentContent.model_validate(content_dict)
    tset = await _make_set(db_session, content=content_dict, translate_cells=True)
    ingest_job = Job(
        kind="ingest",
        status="running",
        stage="translating_body",
        progress=55,
        payload={},
    )
    db_session.add_all(
        [
            ingest_job,
            TranslationUnit(
                set_id=tset.id,
                block_id="blk-paragraph",
                source_hash="fallback",
                content_ja=[],
                text_ja="",
                state="machine",
                quality_flags=["placeholder_mismatch"],
            ),
        ]
    )
    await db_session.commit()

    completed = await finalize_ingest_if_body_complete(
        db_session,
        set_id=str(tset.id),
        ingest_job_id=str(ingest_job.id),
        content=content,
        style="natural",
        source_version="v1",
        appendix_untranslated=False,
    )

    assert completed is True
    await db_session.refresh(ingest_job)
    assert ingest_job.status == "succeeded"
