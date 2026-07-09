"""PY-ART-03: 記事内 figure_embed のライセンス連動判定(plans/07 §4.5、docs/09 §5.2)。

``alinea_core.licenses.classify_license`` の判定結果(PY-LIC-01 が単体で検証済み)が、記事の
後処理(:func:`alinea_core.article.postprocess.normalize_article`)を通じて figure_embed
ブロックへ正しく反映されることを検証する(統合: licenses → sources → postprocess の接続)。

対象は docs/09 §5.2 マトリクスの全 8 ライセンス値(``alinea_core.licenses.LicenseId`` の
9 値のうち、表に現れない ``cc-by-nc-nd-4.0`` を除く)。arxiv-nonexclusive / unknown は
figure_link_card へブロックされ代替提示、cc-by-4.0 はクレジット自動付記+ライセンスバッジ、
cc-by-nd はキャプション分離、cc-by-sa 系はクレジットに加え SA 表示フラグが立つ。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from alinea_core.article.postprocess import (
    ArticleGenerationError,
    BlockTypeMismatchError,
    normalize_article,
    normalize_rewritten_block,
    verify_quote,
)
from alinea_core.article.schema import ARTICLE_V1_JSON_SCHEMA
from alinea_core.article.sources import AnnotationRef, ArticleSources, FigureInfo
from alinea_core.db.models import DocumentRevision, LibraryItem, Paper
from alinea_core.document.blocks import DocumentContent
from alinea_core.licenses import classify_license

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


def _assert_openai_strict_objects(schema: dict[str, Any]) -> None:
    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        assert set(schema.get("required", [])) == set(properties)
        for child in properties.values():
            if isinstance(child, dict):
                _assert_openai_strict_objects(child)
    for child in schema.get("anyOf", []):
        if isinstance(child, dict):
            _assert_openai_strict_objects(child)
    items = schema.get("items")
    if isinstance(items, dict):
        _assert_openai_strict_objects(items)


def test_article_schema_is_openai_strict_compatible() -> None:
    _assert_openai_strict_objects(ARTICLE_V1_JSON_SCHEMA)


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


# ---------------------------------------------------------------------------
# verify_quote(§4.5 step3 逐語検証。plans/12 の分岐網羅)
# ---------------------------------------------------------------------------
def test_verify_quote_exact_match_after_whitespace_normalization() -> None:
    assert verify_quote("a  straight   map", "We learn a straight map.") == "a straight map"


def test_verify_quote_empty_inputs_return_none() -> None:
    assert verify_quote("", "some source text") is None
    assert verify_quote("some quote", "") is None


def test_verify_quote_fuzzy_match_above_threshold_returns_source_substring() -> None:
    # 1 文字だけ違う(タイプミス相当)が ratio>=0.8 の最良一致部分文字列に補正される。
    source = "Rectified flow learns a straight transport map between two distributions."
    quote = "Rectified flow learns a straigt transport map"  # "straight" のタイプミス
    result = verify_quote(quote, source)
    assert result is not None
    assert result in source


def test_verify_quote_below_threshold_returns_none() -> None:
    source = "Rectified flow learns a straight transport map."
    quote = "Completely unrelated text with no overlap at all here"
    assert verify_quote(quote, source) is None


def test_verify_quote_no_common_substring_returns_none() -> None:
    assert verify_quote("abc", "xyz") is None


# ---------------------------------------------------------------------------
# normalize_article: 各ブロック型の content 欠落→ドロップ(§4.3 対応必須化)
# ---------------------------------------------------------------------------
def _make_sources(
    *,
    figures: list[FigureInfo] | None = None,
    annotation_refs: list[AnnotationRef] | None = None,
    block_ids: set[str] | None = None,
    section_ids: set[str] | None = None,
    block_source_text: dict[str, str] | None = None,
) -> ArticleSources:
    policy = classify_license("cc-by-4.0")
    paper = Paper(
        id="p-1",
        title="Flow Straight and Fast",
        authors=[{"name": "Xingchao Liu"}],
        arxiv_id="2209.03003",
        venue="ICLR 2023",
        published_on=dt.date(2023, 1, 1),
        license="cc-by-4.0",
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
        figures=figures or [],
        figures_text="",
        notes_text="",
        annotations_text="",
        annotation_refs=annotation_refs or [],
        chat_text="",
        block_ids=block_ids or {"blk-p1"},
        section_ids=section_ids or {"sec-1"},
        block_source_text=block_source_text or {},
    )


def _minimal_discussion_block() -> dict[str, Any]:
    return {
        "type": "discussion",
        "discussion": {"items": [{"text": "疑問点1", "origin": "ai"}]},
    }


def test_normalize_article_drops_blocks_with_missing_content_field() -> None:
    """type と対応 content フィールドが噛み合わないブロックはログに残してドロップする。"""
    raw = {
        "title": "タイトル",
        "blocks": [
            {"type": "heading"},  # heading フィールド無し → ドロップ
            {"type": "paragraph"},  # markdown 無し → ドロップ
            {"type": "quote_source"},  # quote 無し → ドロップ
            {"type": "figure_embed"},  # figure 無し → ドロップ
            {"type": "explainer_figure"},  # explainer 無し → ドロップ
            {"type": "discussion"},  # discussion 無し(has_required_field で先にドロップ)
            _minimal_discussion_block(),
        ],
    }
    normalized = normalize_article(raw, _make_sources())
    assert [b.type for b in normalized.blocks] == ["discussion"]
    assert len(normalized.log) == 6
    assert all(entry["reason"] == "type/content field mismatch" for entry in normalized.log)


def test_normalize_article_quote_source_unknown_block_id_is_dropped_and_logged() -> None:
    raw = {
        "title": "タイトル",
        "blocks": [
            {
                "type": "quote_source",
                "quote": {"block_id": "blk-does-not-exist", "text_en": "some text"},
            },
            _minimal_discussion_block(),
        ],
    }
    normalized = normalize_article(raw, _make_sources(block_source_text={}))
    assert [b.type for b in normalized.blocks] == ["discussion"]
    assert normalized.log[0]["reason"] == "quote_source references unknown block_id"


def test_normalize_article_quote_source_verbatim_mismatch_is_dropped_and_logged() -> None:
    raw = {
        "title": "タイトル",
        "blocks": [
            {
                "type": "quote_source",
                "quote": {"block_id": "blk-p1", "text_en": "totally unrelated made-up text"},
            },
            _minimal_discussion_block(),
        ],
    }
    sources = _make_sources(block_source_text={"blk-p1": "The training objective is simple."})
    normalized = normalize_article(raw, sources)
    assert [b.type for b in normalized.blocks] == ["discussion"]
    assert normalized.log[0]["reason"] == "quote_source verbatim check failed"


def test_normalize_article_figure_embed_unknown_block_id_is_dropped_and_logged() -> None:
    raw = {
        "title": "タイトル",
        "blocks": [
            {
                "type": "figure_embed",
                "figure": {"block_id": "blk-no-such-figure", "caption_ja": "説明"},
            },
            _minimal_discussion_block(),
        ],
    }
    normalized = normalize_article(raw, _make_sources(figures=[]))
    assert [b.type for b in normalized.blocks] == ["discussion"]
    assert normalized.log[0]["reason"] == "figure_embed references unknown block_id"


# ---------------------------------------------------------------------------
# _evidence_anchors: blk-/sec- 以外や未知参照は除外(evidence の健全性)
# ---------------------------------------------------------------------------
def test_normalize_article_evidence_keeps_known_refs_and_drops_unknown_ones() -> None:
    raw = {
        "title": "タイトル",
        "blocks": [
            {
                "type": "paragraph",
                "markdown": "本文。",
                "evidence": ["blk-p1", "sec-1", "blk-unknown", "sec-unknown", "not-a-ref"],
            },
            _minimal_discussion_block(),
        ],
    }
    normalized = normalize_article(raw, _make_sources(block_ids={"blk-p1"}, section_ids={"sec-1"}))
    para = next(b for b in normalized.blocks if b.type == "paragraph")
    anchors = para.evidence_anchors
    assert {a["block_id"] for a in anchors} == {"blk-p1", "sec-1"}


# ---------------------------------------------------------------------------
# discussion: user_highlight の継続保持・降格・重複ブロック抑止(§4.8)
# ---------------------------------------------------------------------------
def test_normalize_article_discussion_resolves_valid_question_annotation() -> None:
    raw = {
        "title": "タイトル",
        "blocks": [
            {
                "type": "discussion",
                "discussion": {
                    "items": [
                        {"text": "疑問点", "origin": "user_highlight", "annotation_id": "ann_01"}
                    ]
                },
            }
        ],
    }
    refs = [
        AnnotationRef(ref="ann_01", annotation_id="uuid-ann-1", color="question", is_question=True)
    ]
    normalized = normalize_article(raw, _make_sources(annotation_refs=refs))
    item = normalized.blocks[0].content["items"][0]
    assert item["origin"] == "user_highlight"
    assert item["annotation_id"] == "uuid-ann-1"


def test_normalize_article_discussion_keeps_previous_highlight_when_annotation_gone() -> None:
    """§4.8: 書き直し前から続く紐付けは、参照先の注釈が消えても保持する。"""
    raw = {
        "title": "タイトル",
        "blocks": [
            {
                "type": "discussion",
                "discussion": {
                    "items": [
                        {
                            "text": "疑問点",
                            "origin": "user_highlight",
                            "annotation_id": "uuid-ann-gone",
                        }
                    ]
                },
            }
        ],
    }
    normalized = normalize_article(
        raw,
        _make_sources(annotation_refs=[]),
        previous_user_highlight_ids=frozenset({"uuid-ann-gone"}),
    )
    item = normalized.blocks[0].content["items"][0]
    assert item["origin"] == "user_highlight"
    assert item["annotation_id"] == "uuid-ann-gone"


def test_normalize_article_discussion_demotes_bogus_annotation_to_ai() -> None:
    raw = {
        "title": "タイトル",
        "blocks": [
            {
                "type": "discussion",
                "discussion": {
                    "items": [
                        {"text": "疑問点", "origin": "user_highlight", "annotation_id": "ann_99"}
                    ]
                },
            }
        ],
    }
    normalized = normalize_article(raw, _make_sources(annotation_refs=[]))
    item = normalized.blocks[0].content["items"][0]
    assert item["origin"] == "ai"
    assert item["annotation_id"] is None
    assert normalized.log[0]["reason"] == "discussion item annotation_id invalid; demoted to ai"


def test_normalize_article_second_discussion_block_is_dropped() -> None:
    raw = {
        "title": "タイトル",
        "blocks": [_minimal_discussion_block(), _minimal_discussion_block()],
    }
    normalized = normalize_article(raw, _make_sources())
    assert len(normalized.blocks) == 1
    assert normalized.log[0]["reason"] == "extra discussion block dropped"


def test_normalize_article_raises_when_discussion_missing_entirely() -> None:
    raw = {"title": "タイトル", "blocks": [{"type": "paragraph", "markdown": "本文のみ。"}]}
    with pytest.raises(ArticleGenerationError):
        normalize_article(raw, _make_sources())


# ---------------------------------------------------------------------------
# explainer_figure: slot 重複・上限超過はドロップ(MAX_EXPLAINER_FIGURES=2。§4.3)
# ---------------------------------------------------------------------------
def test_normalize_article_drops_duplicate_and_over_limit_explainer_slots() -> None:
    def _explainer(slot: int) -> dict[str, Any]:
        return {
            "type": "explainer_figure",
            "explainer": {"slot": slot, "image_brief_en": "a diagram", "caption_ja": "説明"},
        }

    raw = {
        "title": "タイトル",
        "blocks": [
            _explainer(0),
            _explainer(0),  # 重複 slot → ドロップ
            _explainer(1),
            _explainer(0),  # 上限(2 個)超過 → ドロップ(このケースは重複でもある)
            _minimal_discussion_block(),
        ],
    }
    normalized = normalize_article(raw, _make_sources())
    explainer_blocks = [b for b in normalized.blocks if b.type == "explainer_figure"]
    assert len(explainer_blocks) == 2
    assert {b.content["slot"] for b in explainer_blocks} == {0, 1}
    dropped_reasons = [
        entry["reason"]
        for entry in normalized.log
        if entry["reason"] == "duplicate or over-limit explainer_figure slot dropped"
    ]
    assert len(dropped_reasons) == 2


# ---------------------------------------------------------------------------
# normalize_rewritten_block(§4.8): type 不一致・content 欠落の異常系
# ---------------------------------------------------------------------------
def test_normalize_rewritten_block_type_mismatch_raises() -> None:
    raw = {"type": "paragraph", "markdown": "書き直した本文。"}
    with pytest.raises(BlockTypeMismatchError):
        normalize_rewritten_block(raw, _make_sources(), expected_type="heading")


def test_normalize_rewritten_block_missing_content_raises_generation_error() -> None:
    raw = {"type": "paragraph"}  # markdown 無し → _normalize_block が None を返す
    with pytest.raises(ArticleGenerationError):
        normalize_rewritten_block(raw, _make_sources(), expected_type="paragraph")


def test_normalize_rewritten_block_succeeds_for_valid_paragraph() -> None:
    raw = {"type": "paragraph", "markdown": "書き直した本文です。"}
    normalized = normalize_rewritten_block(raw, _make_sources(), expected_type="paragraph")
    assert normalized.content["md"] == "書き直した本文です。"
