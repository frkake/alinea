"""記事 wire 変換のテスト(plans/03 §19.1)。

``yakudoku_core.article.wire`` は DB 保存形(``article_blocks.content``。フラット)を
API/ジョブ結果向けの wire 形(ネスト)へ変換する純関数群。DB・非同期は不要(unit)。
"""

from __future__ import annotations

from yakudoku_core.article.wire import (
    EvidenceDisplayResolver,
    ExplainerRef,
    article_block_wire_id,
    block_content_to_wire,
    build_article_block_wire,
    build_evidence_wire,
    derive_display,
    parse_article_block_pk,
)
from yakudoku_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from yakudoku_core.document.inlines import Inline
from yakudoku_core.search.rebuild import BlockIndexRow


def _content() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(id="blk-p1", type="paragraph", inlines=[Inline(t="text", v="本文。")]),
                    Block(id="blk-eq1", type="equation", number="1", latex="x=1"),
                    Block(
                        id="blk-fig1",
                        type="figure",
                        number="1",
                        asset_key="fig-1.png",
                        caption=[Inline(t="text", v="キャプション。")],
                    ),
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# article_block_wire_id / parse_article_block_pk(往復。§19.1)
# ---------------------------------------------------------------------------
def test_article_block_wire_id_and_parse_round_trip() -> None:
    assert article_block_wire_id(42) == "ablk_42"
    assert parse_article_block_pk("ablk_42") == 42


def test_parse_article_block_pk_rejects_missing_prefix() -> None:
    assert parse_article_block_pk("42") is None


def test_parse_article_block_pk_rejects_non_numeric_suffix() -> None:
    assert parse_article_block_pk("ablk_not-a-number") is None


# ---------------------------------------------------------------------------
# derive_display: block_type 別の分岐(equation/figure・table/段落系/既定)
# ---------------------------------------------------------------------------
def _row(**overrides: object) -> BlockIndexRow:
    base: dict[str, object] = {
        "block_id": "blk-1",
        "block_type": "paragraph",
        "section_path": "sec-1",
        "section_label": "§1",
        "paragraph_ordinal": None,
        "element_label": None,
        "position": 0,
        "source_text": "",
        "in_translation_scope": True,
        "page": None,
        "bbox": None,
    }
    base.update(overrides)
    return BlockIndexRow(**base)  # type: ignore[arg-type]


def test_derive_display_equation_uses_element_label() -> None:
    row = _row(block_type="equation", element_label="式(5)")
    assert derive_display(row) == "式(5)"


def test_derive_display_figure_or_table_uses_element_label() -> None:
    assert derive_display(_row(block_type="figure", element_label="図2")) == "図2"
    assert derive_display(_row(block_type="table", element_label="表1")) == "表1"


def test_derive_display_paragraph_like_uses_paragraph_ordinal() -> None:
    row = _row(block_type="paragraph", paragraph_ordinal=4, section_label="§2.1")
    assert derive_display(row) == "§2.1 ¶4"


def test_derive_display_falls_back_to_section_label() -> None:
    # equation だが element_label 無し、段落系でも paragraph_ordinal 無し → 節ラベルのみ。
    assert derive_display(_row(block_type="equation", element_label=None)) == "§1"
    assert (
        derive_display(_row(block_type="reference_entry", section_label="参考文献")) == "参考文献"
    )


# ---------------------------------------------------------------------------
# EvidenceDisplayResolver: block_id 直接一致 / セクションパスへのフォールバック
# ---------------------------------------------------------------------------
def test_evidence_display_resolver_resolves_known_block_id() -> None:
    resolver = EvidenceDisplayResolver(_content())
    assert resolver.display_for("blk-p1") == "§1 ¶1"


def test_evidence_display_resolver_falls_back_to_section_label_for_section_id() -> None:
    resolver = EvidenceDisplayResolver(_content())
    assert resolver.display_for("sec-1") == "§1"


def test_evidence_display_resolver_returns_none_for_unknown_id() -> None:
    resolver = EvidenceDisplayResolver(_content())
    assert resolver.display_for("blk-does-not-exist") is None


def test_build_evidence_wire_uses_resolver_and_falls_back_to_block_id() -> None:
    resolver = EvidenceDisplayResolver(_content())
    anchors = [
        {"block_id": "blk-p1", "revision_id": "rev-1"},
        {"block_id": "blk-unknown", "revision_id": "rev-1"},
    ]
    wire = build_evidence_wire(anchors, resolver)
    assert wire[0]["ref"] == 1
    assert wire[0]["display"] == "§1 ¶1"
    assert wire[0]["anchor"]["display"] == "§1 ¶1"
    assert wire[1]["display"] == "blk-unknown"  # 未知 block_id は id 自体を表示に使う


# ---------------------------------------------------------------------------
# block_content_to_wire: 型別のネスト変換(figure_link_card / explainer 未生成 / 既定)
# ---------------------------------------------------------------------------
def test_block_content_to_wire_figure_link_card_variant() -> None:
    resolver = EvidenceDisplayResolver(_content())
    content = {
        "variant": "figure_link_card",
        "figure_display": "図1",
        "message": "原論文の図1を参照(ライセンス上、転載できません)",
    }
    wire = block_content_to_wire("figure_embed", content, evidence_resolver=resolver)
    assert wire == {
        "figure_link_card": {
            "figure_display": "図1",
            "message": "原論文の図1を参照(ライセンス上、転載できません)",
        }
    }


def test_block_content_to_wire_figure_variant_includes_asset_url_and_flags() -> None:
    resolver = EvidenceDisplayResolver(_content())
    content = {
        "figure_block_id": "blk-fig1",
        "asset_key": "fig-1.png",
        "caption_ja": "キャプション。",
        "credit": "出典: ...",
        "license_badge": "CC BY-ND 4.0 — 転載可",
        "caption_separated": True,
        "share_alike": False,
    }
    wire = block_content_to_wire("figure_embed", content, evidence_resolver=resolver)
    assert wire["figure"]["image_url"] == "/api/assets/fig-1.png"
    assert wire["figure"]["caption_separated"] is True
    assert wire["figure"]["share_alike"] is False


def test_block_content_to_wire_figure_variant_empty_asset_key_yields_empty_image_url() -> None:
    resolver = EvidenceDisplayResolver(_content())
    wire = block_content_to_wire(
        "figure_embed", {"figure_block_id": "blk-fig1"}, evidence_resolver=resolver
    )
    assert wire["figure"]["image_url"] == ""


def test_block_content_to_wire_explainer_without_ref_falls_back_to_stored_caption() -> None:
    resolver = EvidenceDisplayResolver(_content())
    wire = block_content_to_wire(
        "explainer_figure",
        {"slot": 0, "caption_ja": "保存済みキャプション"},
        evidence_resolver=resolver,
    )
    assert wire == {
        "explainer": {"figure_id": "", "image_url": "", "caption": "保存済みキャプション"}
    }


def test_block_content_to_wire_explainer_with_ref_uses_generated_figure() -> None:
    resolver = EvidenceDisplayResolver(_content())
    lookup = {
        0: ExplainerRef(figure_id="ef-1", image_url="/api/assets/ef-1.png", caption="生成済み")
    }
    wire = block_content_to_wire(
        "explainer_figure",
        {"slot": 0, "caption_ja": "保存済みキャプション"},
        evidence_resolver=resolver,
        explainer_lookup=lookup,
    )
    assert wire == {
        "explainer": {
            "figure_id": "ef-1",
            "image_url": "/api/assets/ef-1.png",
            "caption": "生成済み",
        }
    }


def test_block_content_to_wire_unknown_type_returns_empty_dict() -> None:
    resolver = EvidenceDisplayResolver(_content())
    assert block_content_to_wire("unknown_type", {}, evidence_resolver=resolver) == {}


# ---------------------------------------------------------------------------
# build_article_block_wire: 1 ブロックの完全な wire 形(id/locked を含む)
# ---------------------------------------------------------------------------
def test_build_article_block_wire_marks_attribution_as_locked() -> None:
    resolver = EvidenceDisplayResolver(_content())
    wire = build_article_block_wire(
        pk=7,
        type_="attribution",
        content={"text": "出典: ..."},
        evidence_anchors=[],
        origin="ai",
        resolver=resolver,
    )
    assert wire["id"] == "ablk_7"
    assert wire["locked"] is True
    assert wire["content"] == {"attribution": {"text": "出典: ..."}}


def test_build_article_block_wire_non_attribution_is_not_locked() -> None:
    resolver = EvidenceDisplayResolver(_content())
    wire = build_article_block_wire(
        pk=1,
        type_="paragraph",
        content={"md": "本文。"},
        evidence_anchors=[{"block_id": "blk-p1", "revision_id": "rev-1"}],
        origin="ai",
        resolver=resolver,
    )
    assert wire["locked"] is False
    assert wire["evidence"][0]["display"] == "§1 ¶1"
