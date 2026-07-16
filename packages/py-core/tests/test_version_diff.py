"""S10: リビジョン間ブロック差分(version_diff)。

carryover が引き継いだブロック ID の一致関係を再利用して版間ブロックを整列し、
各ブロックを added / removed / changed / unchanged に決定的に分類する(LLM 不使用)。
"""

from __future__ import annotations

from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.parsing.carryover import carry_over_ids, flatten_blocks
from alinea_core.parsing.version_diff import diff_revisions


def _rev(blocks: list[tuple[str, str]], *, section_id: str = "sec-1") -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id=section_id,
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(id=bid, type="paragraph", inlines=[Inline(t="text", v=text)])
                    for bid, text in blocks
                ],
            )
        ],
    )


def test_identical_revisions_have_no_changes() -> None:
    old = _rev([("blk-a", "Alpha stays"), ("blk-b", "Beta stays")])
    new = _rev([("blk-a", "Alpha stays"), ("blk-b", "Beta stays")])
    diff = diff_revisions(old, new)
    assert diff.stats.added == 0
    assert diff.stats.removed == 0
    assert diff.stats.changed == 0
    assert diff.stats.unchanged == 2
    assert diff.changes == []


def test_added_removed_changed_classification() -> None:
    old = _rev(
        [
            ("blk-a", "Alpha unchanged"),
            ("blk-b", "Beta original text"),
            ("blk-c", "Gamma to be removed"),
        ]
    )
    new = _rev(
        [
            ("blk-a", "Alpha unchanged"),
            ("blk-b", "Beta rewritten text"),
            ("blk-d", "Delta freshly added"),
        ]
    )
    diff = diff_revisions(old, new)
    assert diff.stats.unchanged == 1
    assert diff.stats.changed == 1
    assert diff.stats.removed == 1
    assert diff.stats.added == 1

    by_status = {c.status: c for c in diff.changes}
    assert set(by_status) == {"changed", "removed", "added"}

    changed = by_status["changed"]
    assert changed.block_id == "blk-b"
    assert changed.block_type == "paragraph"
    assert changed.old_text == "Beta original text"
    assert changed.new_text == "Beta rewritten text"
    assert changed.section_id == "sec-1"

    removed = by_status["removed"]
    assert removed.block_id == "blk-c"
    assert removed.old_text == "Gamma to be removed"
    assert removed.new_text is None

    added = by_status["added"]
    assert added.block_id == "blk-d"
    assert added.new_text == "Delta freshly added"
    assert added.old_text is None


def test_changes_follow_document_order() -> None:
    # 中央のブロックが置換される(旧 blk-gone 削除・新 blk-fresh 追加)。
    old = _rev([("blk-a", "Head"), ("blk-gone", "Middle old"), ("blk-b", "Tail")])
    new = _rev([("blk-a", "Head"), ("blk-fresh", "Middle new"), ("blk-b", "Tail")])
    diff = diff_revisions(old, new)
    # opcode 順(=新版文書順の置換位置)で removed が added より先に並ぶ。
    assert [c.status for c in diff.changes] == ["removed", "added"]
    assert [c.block_id for c in diff.changes] == ["blk-gone", "blk-fresh"]


def test_diff_reuses_carryover_alignment() -> None:
    """carryover 実行後(存続ブロックが同一 ID を共有)の版どうしを整列できること。"""
    old = _rev(
        [
            ("blk-a", "First paragraph about rectified flow methods."),
            ("blk-b", "Second paragraph describing the ODE dynamics carefully."),
        ]
    )
    old_blocks = flatten_blocks(old.sections)

    # 新版は別 ID で始まるが、carryover が存続ブロックへ旧 ID を引き継ぐ。
    new = _rev(
        [
            ("tmp-1", "First paragraph about rectified flow methods."),
            ("tmp-2", "Second paragraph describing the ODE dynamics very carefully."),
        ]
    )
    carry_over_ids(old_blocks, new.sections)
    new_ids = [b.id for b in flatten_blocks(new.sections)]
    assert new_ids == ["blk-a", "blk-b"]  # 引き継がれている

    diff = diff_revisions(old, new)
    assert diff.stats.added == 0
    assert diff.stats.removed == 0
    assert diff.stats.unchanged == 1  # blk-a 同一
    assert diff.stats.changed == 1  # blk-b は同一 ID だが本文が変わった
    changed = next(c for c in diff.changes if c.status == "changed")
    assert changed.block_id == "blk-b"


def test_diff_across_sections_tracks_section_id() -> None:
    old = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Intro"),
                blocks=[Block(id="blk-a", type="paragraph", inlines=[Inline(t="text", v="Old A")])],
            ),
            Section(
                id="sec-2",
                heading=SectionHeading(number="2", title="Method"),
                blocks=[Block(id="blk-b", type="paragraph", inlines=[Inline(t="text", v="Old B")])],
            ),
        ],
    )
    new = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Intro"),
                blocks=[Block(id="blk-a", type="paragraph", inlines=[Inline(t="text", v="Old A")])],
            ),
            Section(
                id="sec-2",
                heading=SectionHeading(number="2", title="Method"),
                blocks=[Block(id="blk-b", type="paragraph", inlines=[Inline(t="text", v="New B")])],
            ),
        ],
    )
    diff = diff_revisions(old, new)
    changed = next(c for c in diff.changes if c.status == "changed")
    assert changed.block_id == "blk-b"
    assert changed.section_id == "sec-2"
