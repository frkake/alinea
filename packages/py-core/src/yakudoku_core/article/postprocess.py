"""記事生成のサーバー後処理(plans/07 §4.5、stage=generating の後半〜rendering)。

モデル出力(:mod:`yakudoku_core.article.schema` の ``ArticleV1Model``)を検証・正規化し、
DB 保存形(:mod:`yakudoku_core.document.plaintext` の ``article_block_to_plain`` が要求する
フラットなキー名)に変換する。ライセンス判定は :mod:`yakudoku_core.licenses` の既存マトリクスを
そのまま使う(PY-ART-03 の詳細検証は M2-08 の担当。ここでは figure_embed / figure_link_card への
振り分けのみ実装する)。
"""

from __future__ import annotations

import datetime as dt
import difflib
from dataclasses import dataclass, field
from typing import Any

from yakudoku_llm.types import JsonSchemaSpec

from yakudoku_core.article.schema import (
    ARTICLE_BLOCK_V1_JSON_SCHEMA,
    ARTICLE_BLOCK_V1_SCHEMA_NAME,
    ARTICLE_V1_JSON_SCHEMA,
    ARTICLE_V1_SCHEMA_NAME,
    ArticleBlockModel,
    ArticleV1Model,
)
from yakudoku_core.article.sources import ArticleSources, authors_all
from yakudoku_core.db.models import Paper

MAX_EXPLAINER_FIGURES = 2
_QUOTE_MATCH_RATIO = 0.8

ARTICLE_SCHEMA_SPEC = JsonSchemaSpec(
    name=ARTICLE_V1_SCHEMA_NAME, json_schema=ARTICLE_V1_JSON_SCHEMA
)
ARTICLE_BLOCK_SCHEMA_SPEC = JsonSchemaSpec(
    name=ARTICLE_BLOCK_V1_SCHEMA_NAME, json_schema=ARTICLE_BLOCK_V1_JSON_SCHEMA
)

# ライセンス表示ラベル(出典・バッジ文言用。apps/api の viewer 表示とは独立に保つ — 別レーン所有)。
_LICENSE_LABEL: dict[str, str] = {
    "cc-by-4.0": "CC BY 4.0",
    "cc-by-sa-4.0": "CC BY-SA 4.0",
    "cc-by-nc-4.0": "CC BY-NC 4.0",
    "cc-by-nc-sa-4.0": "CC BY-NC-SA 4.0",
    "cc-by-nd-4.0": "CC BY-ND 4.0",
    "cc-by-nc-nd-4.0": "CC BY-NC-ND 4.0",
    "cc0": "CC0",
    "arxiv-nonexclusive": "arXiv 非独占ライセンス",
    "unknown": "ライセンス不明",
}


class ArticleGenerationError(Exception):
    """記事全体の構造検証に失敗(discussion 欠落など。§4.3)。呼び出し側で 1 回だけ再試行する。"""


class BlockTypeMismatchError(Exception):
    """ブロック書き直しで type が変わった(§4.8。許可しない)。"""


@dataclass
class NormalizedBlock:
    type: str
    content: dict[str, Any]
    evidence_anchors: list[dict[str, Any]] = field(default_factory=list)
    origin: str = "ai"
    locked: bool = False


@dataclass
class NormalizedArticle:
    title: str
    blocks: list[NormalizedBlock]
    log: list[dict[str, Any]] = field(default_factory=list)


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def verify_quote(text_en: str, source_text: str) -> str | None:
    """quote_source.text_en の逐語検証(§4.5 step3)。

    空白正規化で部分一致すればそのまま、無ければ SequenceMatcher の最良一致部分文字列
    (ratio >= 0.8)に置換、それ未満は None(呼び出し側でブロック破棄)。
    """
    norm_quote = _normalize_ws(text_en)
    norm_source = _normalize_ws(source_text)
    if not norm_quote or not norm_source:
        return None
    if norm_quote in norm_source:
        return norm_quote
    matcher = difflib.SequenceMatcher(None, norm_source, norm_quote, autojunk=False)
    match = matcher.find_longest_match(0, len(norm_source), 0, len(norm_quote))
    if match.size == 0:
        return None
    candidate = norm_source[match.a : match.a + match.size]
    ratio = difflib.SequenceMatcher(None, candidate, norm_quote, autojunk=False).ratio()
    if ratio >= _QUOTE_MATCH_RATIO:
        return candidate
    return None


def _evidence_anchors(
    evidence: list[str], sources: ArticleSources, revision_id: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ref in evidence:
        if ref.startswith("blk-"):
            if ref not in sources.block_ids:
                continue
        elif ref.startswith("sec-"):
            if ref not in sources.section_ids:
                continue
        else:
            continue
        out.append(
            {
                "revision_id": revision_id,
                "block_id": ref,
                "start": None,
                "end": None,
                "quote": None,
                "side": "source",
            }
        )
    return out


def _normalize_heading(block: ArticleBlockModel) -> dict[str, Any] | None:
    if block.heading is None:
        return None
    return {"level": block.heading.level, "text": block.heading.text}


def _normalize_paragraph(block: ArticleBlockModel) -> dict[str, Any] | None:
    if block.markdown is None:
        return None
    return {"md": block.markdown}


def _normalize_quote(
    block: ArticleBlockModel, sources: ArticleSources, log: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if block.quote is None:
        return None
    block_id = block.quote.block_id
    source_text = sources.block_source_text.get(block_id)
    if source_text is None:
        log.append(
            {
                "level": "partial_failure",
                "reason": "quote_source references unknown block_id",
                "block_id": block_id,
            }
        )
        return None
    verified = verify_quote(block.quote.text_en, source_text)
    if verified is None:
        log.append(
            {
                "level": "partial_failure",
                "reason": "quote_source verbatim check failed",
                "block_id": block_id,
            }
        )
        return None
    return {
        "text_en": verified,
        "block_id": block_id,
        "revision_id": str(sources.revision.id),
    }


def _figure_credit(paper: Paper) -> str:
    arxiv = paper.arxiv_id or "不明"
    return f"出典: {authors_all(paper.authors or [])[:60]}, *{paper.title}* (arXiv:{arxiv})"


def _figure_license_badge(paper: Paper) -> str:
    label = _LICENSE_LABEL.get(paper.license, paper.license)
    return f"{label} — 転載可"


def _normalize_figure(
    block: ArticleBlockModel, sources: ArticleSources, log: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if block.figure is None:
        return None
    block_id = block.figure.block_id
    fig = next((f for f in sources.figures if f.block_id == block_id), None)
    if fig is None:
        log.append(
            {
                "level": "partial_failure",
                "reason": "figure_embed references unknown block_id",
                "block_id": block_id,
            }
        )
        return None
    revision_id = str(sources.revision.id)
    if fig.policy == "link_card":
        # ライセンス上転載不可 → リンクカードへ変換(§4.5 step4)。
        return {
            "variant": "figure_link_card",
            "caption_ja": block.figure.caption_ja,
            "figure_block_id": block_id,
            "revision_id": revision_id,
            "figure_display": fig.display,
            "message": f"原論文の{fig.display}を参照(ライセンス上、転載できません)",
        }
    return {
        "variant": "figure",
        "caption_ja": block.figure.caption_ja,
        "figure_block_id": block_id,
        "revision_id": revision_id,
        "asset_key": fig.asset_key,
        "credit": _figure_credit(sources.paper),
        "license_badge": _figure_license_badge(sources.paper),
        "caption_separated": fig.policy == "caption_separate",
    }


def _normalize_explainer(block: ArticleBlockModel) -> dict[str, Any] | None:
    if block.explainer is None:
        return None
    return {
        "slot": block.explainer.slot,
        "image_brief_en": block.explainer.image_brief_en,
        "caption_ja": block.explainer.caption_ja,
    }


def _normalize_discussion(
    block: ArticleBlockModel,
    sources: ArticleSources,
    log: list[dict[str, Any]],
    *,
    previous_user_highlight_ids: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    if block.discussion is None:
        return None
    items_out: list[dict[str, Any]] = []
    for item in block.discussion.items:
        origin = item.origin
        annotation_id: str | None = None
        if origin == "user_highlight":
            ref = sources.resolve_ref(item.annotation_id or "")
            if ref is not None and ref.is_question:
                annotation_id = ref.annotation_id
            elif item.annotation_id in previous_user_highlight_ids:
                # §4.8: 参照先の注釈が消えていても、書き直し前から続く紐付けは保持する。
                annotation_id = item.annotation_id
            else:
                origin = "ai"
                log.append(
                    {
                        "level": "partial_failure",
                        "reason": "discussion item annotation_id invalid; demoted to ai",
                        "annotation_id": item.annotation_id,
                    }
                )
        items_out.append({"md": item.text, "origin": origin, "annotation_id": annotation_id})
    return {"items": items_out}


def _normalize_block(
    block: ArticleBlockModel,
    sources: ArticleSources,
    revision_id: str,
    log: list[dict[str, Any]],
    *,
    previous_user_highlight_ids: frozenset[str] = frozenset(),
) -> NormalizedBlock | None:
    if not block.has_required_field():
        log.append(
            {
                "level": "partial_failure",
                "reason": "type/content field mismatch",
                "type": block.type,
            }
        )
        return None

    content: dict[str, Any] | None
    if block.type == "heading":
        content = _normalize_heading(block)
    elif block.type == "paragraph":
        content = _normalize_paragraph(block)
    elif block.type == "quote_source":
        content = _normalize_quote(block, sources, log)
    elif block.type == "figure_embed":
        content = _normalize_figure(block, sources, log)
    elif block.type == "explainer_figure":
        content = _normalize_explainer(block)
    elif block.type == "discussion":
        content = _normalize_discussion(
            block, sources, log, previous_user_highlight_ids=previous_user_highlight_ids
        )
    else:  # pragma: no cover — スキーマの enum で到達しない
        content = None

    if content is None:
        return None
    evidence_anchors = _evidence_anchors(block.evidence, sources, revision_id)
    return NormalizedBlock(type=block.type, content=content, evidence_anchors=evidence_anchors)


def normalize_article(
    raw: dict[str, Any],
    sources: ArticleSources,
    *,
    previous_user_highlight_ids: frozenset[str] = frozenset(),
) -> NormalizedArticle:
    """記事全体を検証・正規化する(§4.5 step1〜3)。

    discussion ブロックが 0 個になった場合は :class:`ArticleGenerationError` を送出する
    (呼び出し側で 1 回だけ再試行し、それでも欠落ならジョブ失敗 — §4.3)。
    """
    model = ArticleV1Model.model_validate(raw)
    revision_id = str(sources.revision.id)
    log: list[dict[str, Any]] = []
    blocks: list[NormalizedBlock] = []
    seen_slots: set[int] = set()
    discussion_found = False

    for block in model.blocks:
        normalized = _normalize_block(
            block,
            sources,
            revision_id,
            log,
            previous_user_highlight_ids=previous_user_highlight_ids,
        )
        if normalized is None:
            continue
        if normalized.type == "discussion":
            if discussion_found:
                log.append({"level": "partial_failure", "reason": "extra discussion block dropped"})
                continue
            discussion_found = True
        if normalized.type == "explainer_figure":
            slot = int(normalized.content.get("slot", 0))
            if slot in seen_slots or len(seen_slots) >= MAX_EXPLAINER_FIGURES:
                log.append(
                    {
                        "level": "partial_failure",
                        "reason": "duplicate or over-limit explainer_figure slot dropped",
                        "slot": slot,
                    }
                )
                continue
            seen_slots.add(slot)
        blocks.append(normalized)

    if not discussion_found:
        raise ArticleGenerationError("discussion block missing after normalization")

    return NormalizedArticle(title=model.title[:60], blocks=blocks, log=log)


def normalize_rewritten_block(
    raw: dict[str, Any],
    sources: ArticleSources,
    *,
    expected_type: str,
    previous_user_highlight_ids: frozenset[str] = frozenset(),
) -> NormalizedBlock:
    """ブロック単体の書き直し結果を検証・正規化する(§4.8)。

    type の変更は許可しない(:class:`BlockTypeMismatchError` を送出し、呼び出し側で 1 回
    再試行してもなお不一致ならジョブ失敗)。
    """
    block = ArticleBlockModel.model_validate(raw)
    if block.type != expected_type:
        raise BlockTypeMismatchError(f"expected {expected_type}, got {block.type}")
    log: list[dict[str, Any]] = []
    normalized = _normalize_block(
        block,
        sources,
        str(sources.revision.id),
        log,
        previous_user_highlight_ids=previous_user_highlight_ids,
    )
    if normalized is None:
        raise ArticleGenerationError(f"block rewrite produced invalid content for {expected_type}")
    return normalized


def build_attribution_block(paper: Paper) -> NormalizedBlock:
    """出典ブロック(§4.5 step5)。削除不可(locked=True)。"""
    year = paper.published_on.year if paper.published_on else "年不明"
    venue = f"{paper.venue}. " if paper.venue else ""
    arxiv = paper.arxiv_id or "不明"
    label = _LICENSE_LABEL.get(paper.license, paper.license)
    text = (
        f'出典: {authors_all(paper.authors or [])}. "{paper.title}." {venue}'
        f"arXiv:{arxiv} ({year}) · ライセンス {label}"
    )
    return NormalizedBlock(type="attribution", content={"text": text}, origin="ai", locked=True)


def build_disclaimer(generated_at: dt.datetime) -> str:
    """免責文言(§4.5 step9 逐語)。保存はせず API 応答時に組み立てる。"""
    return (
        "訳文・メモ・チャット履歴から自動構成 · "
        f"{generated_at:%Y-%m-%d} · 元の論文とは別物です — 根拠チップから原文へ"
    )


__all__ = [
    "ARTICLE_BLOCK_SCHEMA_SPEC",
    "ARTICLE_SCHEMA_SPEC",
    "MAX_EXPLAINER_FIGURES",
    "ArticleGenerationError",
    "BlockTypeMismatchError",
    "NormalizedArticle",
    "NormalizedBlock",
    "build_attribution_block",
    "build_disclaimer",
    "normalize_article",
    "normalize_rewritten_block",
    "verify_quote",
]
