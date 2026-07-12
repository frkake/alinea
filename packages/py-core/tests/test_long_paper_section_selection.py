from __future__ import annotations

import pytest
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.translation import (
    TranslationSettings,
    build_ingest_translation_plan,
    select_translation_plan_sections,
    selectable_translation_section_ids,
    translation_plan_awaits_section_selection,
)


def _paragraph(block_id: str, text: str = "Body") -> Block:
    return Block(id=block_id, type="paragraph", inlines=[Inline(t="text", v=text)])


def _section(
    section_id: str,
    number: str,
    title: str,
    block_id: str,
    *,
    children: list[Section] | None = None,
) -> Section:
    return Section(
        id=section_id,
        heading=SectionHeading(number=number, title=title),
        blocks=[_paragraph(block_id)],
        sections=children or [],
    )


def _content() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            _section(
                "sec-1",
                "1",
                "Introduction",
                "blk-1",
                children=[_section("sec-1a", "1.1", "Background", "blk-1a")],
            ),
            _section("sec-2", "2", "Method", "blk-2"),
            _section("sec-A", "A", "Appendix", "blk-A"),
            _section("sec-ref", "", "References", "blk-ref"),
        ],
    )


@pytest.mark.parametrize("pages", [None, 0, 30, True])
def test_ingest_plan_does_not_wait_at_or_below_threshold_or_unknown(pages: int | None) -> None:
    plan = build_ingest_translation_plan(
        _content(),
        TranslationSettings(suggest_section_selection_over_30_pages=True),
        pages=pages,
    )

    assert plan.target_section_ids == ["sec-1", "sec-1a", "sec-2", "sec-A"]
    assert translation_plan_awaits_section_selection(_content(), plan) is False


def test_ingest_plan_waits_over_threshold_only_when_explicitly_enabled() -> None:
    content = _content()
    enabled = build_ingest_translation_plan(
        content,
        TranslationSettings(suggest_section_selection_over_30_pages=True),
        pages=31,
    )
    disabled = build_ingest_translation_plan(content, TranslationSettings(), pages=64)

    assert enabled.target_section_ids == []
    assert enabled.target_block_ids == []
    assert enabled.pages == 31
    assert enabled.suggest_section_selection_over_30_pages is True
    assert translation_plan_awaits_section_selection(content, enabled) is True
    assert disabled.target_section_ids == ["sec-1", "sec-1a", "sec-2", "sec-A"]


def test_selectable_sections_preserve_appendix_policy_and_exclude_references() -> None:
    content = _content()
    plan = build_ingest_translation_plan(
        content,
        TranslationSettings(
            auto_translate_appendix=False,
            suggest_section_selection_over_30_pages=True,
        ),
        pages=42,
    )

    assert selectable_translation_section_ids(content, plan) == ["sec-1", "sec-1a", "sec-2"]


def test_selected_sections_are_canonicalized_with_exact_direct_blocks() -> None:
    content = _content()
    pending = build_ingest_translation_plan(
        content,
        TranslationSettings(suggest_section_selection_over_30_pages=True),
        pages=42,
    )

    selected = select_translation_plan_sections(
        content,
        pending,
        ["sec-2", "sec-1a"],
    )

    assert selected.target_section_ids == ["sec-1a", "sec-2"]
    assert selected.target_block_ids == ["blk-1a", "blk-2"]
    assert selected.include_appendix is pending.include_appendix
    assert selected.translate_table_cells is pending.translate_table_cells
    assert selected.pages == 42
    assert translation_plan_awaits_section_selection(content, selected) is False


@pytest.mark.parametrize(
    "section_ids, message",
    [
        ([], "at least one"),
        (["sec-1", "sec-1"], "duplicate"),
        (["sec-missing"], "not selectable"),
    ],
)
def test_section_selection_rejects_empty_duplicate_and_unknown_ids(
    section_ids: list[str],
    message: str,
) -> None:
    content = _content()
    pending = build_ingest_translation_plan(
        content,
        TranslationSettings(suggest_section_selection_over_30_pages=True),
        pages=42,
    )

    with pytest.raises(ValueError, match=message):
        select_translation_plan_sections(content, pending, section_ids)


def test_section_selection_rejects_appendix_excluded_by_pending_plan() -> None:
    content = _content()
    pending = build_ingest_translation_plan(
        content,
        TranslationSettings(
            auto_translate_appendix=False,
            suggest_section_selection_over_30_pages=True,
        ),
        pages=42,
    )

    with pytest.raises(ValueError, match="not selectable"):
        select_translation_plan_sections(content, pending, ["sec-A"])


def test_empty_document_never_waits_for_impossible_input() -> None:
    content = DocumentContent(quality_level="A", sections=[])
    plan = build_ingest_translation_plan(
        content,
        TranslationSettings(suggest_section_selection_over_30_pages=True),
        pages=80,
    )

    assert plan.target_section_ids == []
    assert plan.target_block_ids == []
    assert translation_plan_awaits_section_selection(content, plan) is False
