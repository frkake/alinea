"""PY-DB-13/14: ドキュメント IR・安定 ID・平文導出・検索索引再構築。"""

from __future__ import annotations

import pytest
from alinea_core.document.anchor import AnchorJson
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.document.plaintext import block_to_plain, inline_to_plain, strip_markdown
from alinea_core.document.stable_id import derive_block_id
from alinea_core.search.rebuild import compute_index_rows, rebuild_block_search_index
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---- 安定 ID(docs/01 §4.3) ----
def test_block_id_is_deterministic() -> None:
    a = derive_block_id(section_idx=3, para_idx=2, content="Rectified flow is a method")
    b = derive_block_id(section_idx=3, para_idx=2, content="Rectified flow is a method")
    assert a == b
    assert a.startswith("blk-")


def test_block_id_changes_with_content() -> None:
    a = derive_block_id(section_idx=3, para_idx=2, content="X")
    b = derive_block_id(section_idx=3, para_idx=2, content="Y")
    assert a != b


def test_block_id_matches_docs_shape() -> None:
    # docs/01 §4.4 例: blk-3-p2-<hash>
    bid = derive_block_id(section_idx=3, para_idx=2, content="foo", block_type="paragraph")
    assert bid.startswith("blk-3-p2-")


# ---- Anchor(docs/01 §5) ----
def test_anchor_truncates_quote() -> None:
    a = AnchorJson(revision_id="r", block_id="blk-1", quote="x" * 900)
    assert len(a.quote) == 500


def test_anchor_defaults_side_source() -> None:
    a = AnchorJson(revision_id="r", block_id="blk-1")
    assert a.side == "source"
    assert a.start is None and a.end is None


# ---- 平文導出(plans/11 §9.1) ----
def test_inline_to_plain_keeps_text_and_math() -> None:
    inlines = [
        Inline(t="text", v="We train with "),
        Inline(t="citation", ref="ref-12"),
        Inline(t="text", v=" using "),
        Inline(t="ref", kind="equation", ref="eq-5", v="式(5)"),
    ]
    plain = inline_to_plain(inlines)
    assert "We train with" in plain
    assert "[ref-12]" in plain
    assert "式(5)" in plain


def test_strip_markdown_removes_emphasis_and_evidence() -> None:
    assert strip_markdown("**bold** and ⟦A:0⟧ [link](http://x)") == "bold and link"


def test_block_to_plain_equation_uses_latex() -> None:
    blk = Block(id="blk-1", type="equation", latex=r"\mathcal{L}=x", number="5")
    assert block_to_plain(blk) == r"\mathcal{L}=x"


# ---- 検索索引の構築(純関数部分) ----
def _sample_doc() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(
                        id="blk-1-p1-aaaa",
                        type="paragraph",
                        inlines=[Inline(t="text", v="Rectified flow.")],
                    ),
                    Block(id="blk-1-eq5-bbbb", type="equation", latex=r"\mathcal{L}", number="5"),
                ],
            )
        ],
    )


def test_compute_index_rows_assigns_ordinals_and_labels() -> None:
    rows = compute_index_rows(_sample_doc())
    assert len(rows) == 2
    para, eq = rows
    assert para.section_label == "§1"
    assert para.paragraph_ordinal == 1
    assert para.in_translation_scope is True
    assert eq.element_label == "式(5)"
    assert eq.in_translation_scope is False  # equation は翻訳対象外


@pytest.mark.asyncio
async def test_py_db_14_rebuild_block_search_index_roundtrip(db_session: AsyncSession) -> None:
    # 論文とリビジョンを作る
    pid = (
        await db_session.execute(text("INSERT INTO papers (title) VALUES ('RF') RETURNING id"))
    ).scalar_one()
    rid = (
        await db_session.execute(
            text(
                """
                INSERT INTO document_revisions
                  (paper_id, parser_version, quality_level, source_format, content)
                VALUES (:p, 'html-1', 'A', 'arxiv_html', '{}') RETURNING id
                """
            ),
            {"p": pid},
        )
    ).scalar_one()
    await db_session.flush()

    inserted = await rebuild_block_search_index(db_session, str(rid), _sample_doc())
    assert inserted == 2

    cnt = await db_session.scalar(
        text("SELECT count(*) FROM block_search_index WHERE revision_id = :r"), {"r": rid}
    )
    assert cnt == 2

    # 再実行しても DELETE→INSERT で件数は変わらない(冪等)
    inserted2 = await rebuild_block_search_index(db_session, str(rid), _sample_doc())
    assert inserted2 == 2
    cnt2 = await db_session.scalar(
        text("SELECT count(*) FROM block_search_index WHERE revision_id = :r"), {"r": rid}
    )
    assert cnt2 == 2

    # PGroonga 索引が引ける(英語原文ヒット)
    hit = await db_session.scalar(
        text(
            "SELECT count(*) FROM block_search_index WHERE revision_id = :r AND source_text &@~ :q"
        ),
        {"r": rid, "q": "rectified"},
    )
    assert hit == 1
    await db_session.rollback()
