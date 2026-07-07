"""PY-ART-03: 記事内 figure_embed のライセンス連動判定(plans/07 §4.5、docs/09 §5.2)。

``yakudoku_core.licenses.classify_license`` の判定結果(PY-LIC-01 が単体で検証済み)が、記事の
後処理(:func:`yakudoku_core.article.postprocess.normalize_article`)を通じて figure_embed
ブロックへ正しく反映されることを検証する(統合: licenses → sources → postprocess の接続)。

対象は docs/09 §5.2 マトリクスの全 8 ライセンス値(``yakudoku_core.licenses.LicenseId`` の
9 値のうち、表に現れない ``cc-by-nc-nd-4.0`` を除く)。arxiv-nonexclusive / unknown は
figure_link_card へブロックされ代替提示、cc-by-4.0 はクレジット自動付記+ライセンスバッジ、
cc-by-nd はキャプション分離、cc-by-sa 系はクレジットに加え SA 表示フラグが立つ。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from yakudoku_core.article.postprocess import normalize_article
from yakudoku_core.article.sources import ArticleSources, FigureInfo
from yakudoku_core.db.models import DocumentRevision, LibraryItem, Paper
from yakudoku_core.document.blocks import DocumentContent
from yakudoku_core.licenses import classify_license

# docs/09 §5.2 マトリクス全 8 行(cc-by-nc-nd-4.0 は表に無いため対象外)。
LICENSE_MATRIX_ROWS: list[tuple[str, str]] = [
    ("cc-by-4.0", "allow"),
    ("cc-by-sa-4.0", "allow"),
    ("cc-by-nc-4.0", "allow"),
    ("cc-by-nc-sa-4.0", "allow"),
    ("cc-by-nd-4.0", "caption_separate"),
    ("cc0", "allow"),
    ("arxiv-nonexclusive", "link_card"),
    ("unknown", "link_card"),
]


def _sources_for(license_id: str) -> ArticleSources:
    policy = classify_license(license_id)
    paper = Paper(
        id="p-1",
        title="Flow Straight and Fast",
        authors=[{"name": "Xingchao Liu"}, {"name": "Qiang Liu"}],
        arxiv_id="2209.03003",
        venue="ICLR 2023",
        published_on=dt.date(2023, 1, 1),
        license=license_id,
    )
    library_item = LibraryItem(id="li-1", user_id="u-1", paper_id="p-1")
    revision = DocumentRevision(
        id="rev-1",
        paper_id="p-1",
        parser_version="t",
        quality_level="A",
        source_format="latex",
        content={},
    )
    figures = [
        FigureInfo(
            block_id="blk-fig1",
            kind="figure",
            display="図1",
            caption_en="Straightened trajectories.",
            caption_ja=None,
            asset_key="fig-1.png",
            policy=policy.figure_embed,
        )
    ]
    return ArticleSources(
        library_item=library_item,
        paper=paper,
        revision=revision,
        content=DocumentContent(quality_level="A", sections=[]),
        style="natural",
        license_policy=policy,
        bibliography_text="",
        summary_text="",
        body_text="",
        figures=figures,
        figures_text="",
        notes_text="",
        annotations_text="",
        annotation_refs=[],
        chat_text="",
        block_ids={"blk-fig1"},
        section_ids=set(),
        block_source_text={},
    )


def _raw_article() -> dict[str, Any]:
    return {
        "title": "整流フローを読む",
        "blocks": [
            {"type": "heading", "heading": {"level": 2, "text": "概要"}},
            {
                "type": "figure_embed",
                "figure": {"block_id": "blk-fig1", "caption_ja": "軌道の直線化。"},
            },
            {
                "type": "discussion",
                "discussion": {
                    "items": [
                        {"text": "疑問点1", "origin": "ai"},
                        {"text": "疑問点2", "origin": "ai"},
                    ]
                },
            },
        ],
    }


@pytest.mark.parametrize("license_id,expected_policy", LICENSE_MATRIX_ROWS)
def test_figure_embed_license_matrix(license_id: str, expected_policy: str) -> None:
    sources = _sources_for(license_id)
    normalized = normalize_article(_raw_article(), sources)
    fig_block = next(b for b in normalized.blocks if b.type == "figure_embed")

    if expected_policy == "link_card":
        # arxiv-nonexclusive / unknown: 転載不可 → リンクカードへ変換(§4.5 step4)。
        assert fig_block.content["variant"] == "figure_link_card"
        assert fig_block.content["figure_display"] == "図1"
        assert "転載できません" in fig_block.content["message"]
        return

    assert fig_block.content["variant"] == "figure"
    assert "出典" in fig_block.content["credit"]
    assert "転載可" in fig_block.content["license_badge"]
    assert fig_block.content["caption_separated"] == (expected_policy == "caption_separate")
    assert fig_block.content["share_alike"] == classify_license(license_id).share_alike


def test_cc_by_credits_without_caption_separation_or_share_alike() -> None:
    sources = _sources_for("cc-by-4.0")
    normalized = normalize_article(_raw_article(), sources)
    fig_block = next(b for b in normalized.blocks if b.type == "figure_embed")
    assert fig_block.content["variant"] == "figure"
    assert fig_block.content["caption_separated"] is False
    assert fig_block.content["share_alike"] is False
    assert "CC BY 4.0" in fig_block.content["license_badge"]


def test_cc_by_sa_flags_share_alike_notice() -> None:
    sources = _sources_for("cc-by-sa-4.0")
    normalized = normalize_article(_raw_article(), sources)
    fig_block = next(b for b in normalized.blocks if b.type == "figure_embed")
    assert fig_block.content["variant"] == "figure"
    assert fig_block.content["share_alike"] is True


def test_cc_by_nd_separates_caption_from_figure() -> None:
    sources = _sources_for("cc-by-nd-4.0")
    normalized = normalize_article(_raw_article(), sources)
    fig_block = next(b for b in normalized.blocks if b.type == "figure_embed")
    assert fig_block.content["variant"] == "figure"
    assert fig_block.content["caption_separated"] is True


def test_arxiv_nonexclusive_blocks_embed_with_link_card_alternative() -> None:
    sources = _sources_for("arxiv-nonexclusive")
    normalized = normalize_article(_raw_article(), sources)
    fig_block = next(b for b in normalized.blocks if b.type == "figure_embed")
    assert fig_block.content["variant"] == "figure_link_card"


def test_unknown_license_blocks_embed_with_link_card_alternative() -> None:
    sources = _sources_for("unknown")
    normalized = normalize_article(_raw_article(), sources)
    fig_block = next(b for b in normalized.blocks if b.type == "figure_embed")
    assert fig_block.content["variant"] == "figure_link_card"
