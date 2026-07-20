"""記事公開スナップショットのサニタイザ(Task 24・plans §4)。

生成記事(``article_blocks`` のフラット保存形)から、公開しても情報漏えいのない安全な
部分集合だけを切り出す純ロジック。apps/api の公開ルータがこれを使ってスナップショットを作る
(worker は apps/api に依存できないため py-core に置く。wire.py と同方針)。

**許可リスト(allow-list。これ以外は一切出力しない)**:
- ``heading``   : 見出しレベル + テキスト(記事側の AI 生成テキスト)。
- ``paragraph`` : AI が書いた解説本文(markdown)。
- ``attribution``: 出典表記(「元論文とは別物」ディスクレーマ)。
- ``explainer_figure``: ライセンス確認済みの AI 生成解説図(画像 URL + キャプション)。
- ``overview_figure``: AI 生成の全体概要図(DSL。原論文の図ではない)。

**除去(公開スナップショットには決して含めない)**:
- ``quote_source``: 原文の逐語引用本文。
- ``figure_embed``: 原論文の図・表(ライセンスと帰属が公開転載の対象外)。
- ``discussion`` : 議論ブロック(ユーザーハイライト由来を含み得る)。
- evidence anchor の ``quote`` 本文・``block source text``・訳文。
  → evidence は「論文タイトル + セクションラベル」だけに縮約する。
- メモ・チャット・注釈・翻訳など読書資産(そもそも記事ブロックに無いが、
  スナップショットに混ぜない)。
"""

from __future__ import annotations

from typing import Any

from alinea_core.article.wire import EvidenceDisplayResolver, ExplainerRef

# 記事から公開スナップショットへ持ち出せるブロック種別(これ以外は落とす)。
PUBLISHABLE_BLOCK_TYPES: frozenset[str] = frozenset(
    {"heading", "paragraph", "attribution", "explainer_figure"}
)

# 公開転載できるライセンス(AI 生成図は元論文ライセンスに縛られないが、原図由来の
# figure_embed は常に除外する。overview/explainer のみ許可)。


def _sanitize_evidence(
    evidence_anchors: list[dict[str, Any]] | None,
    *,
    resolver: EvidenceDisplayResolver | None,
    paper_title: str,
) -> list[dict[str, Any]]:
    """evidence を「論文タイトル + セクションラベル」だけに縮約する。

    quote 本文・block 原文・start/end オフセット・revision_id は一切残さない
    (それらは原文再構成の手がかりになるため公開しない)。
    """
    out: list[dict[str, Any]] = []
    for i, anchor in enumerate(evidence_anchors or [], start=1):
        if not isinstance(anchor, dict):
            continue
        block_id = str(anchor.get("block_id", ""))
        section = None
        if resolver is not None:
            section = resolver.display_for(block_id)
        out.append(
            {
                "ref": i,
                "paper_title": paper_title,
                "section": section or "",
            }
        )
    return out


def _sanitize_heading(content: dict[str, Any]) -> dict[str, Any]:
    return {"heading": {"level": content.get("level"), "text": content.get("text", "")}}


def _sanitize_paragraph(content: dict[str, Any]) -> dict[str, Any]:
    return {"markdown": content.get("md", "")}


def _sanitize_attribution(content: dict[str, Any]) -> dict[str, Any]:
    return {"attribution": {"text": content.get("text", "")}}


def _sanitize_explainer(
    content: dict[str, Any], explainer_lookup: dict[int, ExplainerRef] | None
) -> dict[str, Any] | None:
    """AI 生成の解説図のみ出力。対応する現行 ExplainerFigure が無ければ落とす。"""
    slot = int(content.get("slot", 0))
    ref = (explainer_lookup or {}).get(slot)
    if ref is None:
        return None
    return {
        "explainer": {
            "figure_id": ref.figure_id,
            "image_url": ref.image_url,
            "caption": ref.caption,
        }
    }


def sanitize_article_blocks(
    blocks: list[dict[str, Any]],
    *,
    resolver: EvidenceDisplayResolver | None,
    explainer_lookup: dict[int, ExplainerRef] | None,
    paper_title: str,
) -> list[dict[str, Any]]:
    """フラットな DB ブロック列 → 公開スナップショットのブロック列(allow-list 適用)。

    各要素は ``{"type": str, "content": dict[str, Any], "evidence": [...]}``。
    許可外の種別・空になった explainer は結果から除外する。
    """
    out: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        type_ = str(block.get("type", ""))
        if type_ not in PUBLISHABLE_BLOCK_TYPES:
            continue
        content = block.get("content") or {}
        if not isinstance(content, dict):
            content = {}
        wire_content: dict[str, Any] | None
        if type_ == "heading":
            wire_content = _sanitize_heading(content)
        elif type_ == "paragraph":
            wire_content = _sanitize_paragraph(content)
        elif type_ == "attribution":
            wire_content = _sanitize_attribution(content)
        elif type_ == "explainer_figure":
            wire_content = _sanitize_explainer(content, explainer_lookup)
        else:  # pragma: no cover — PUBLISHABLE_BLOCK_TYPES と同期
            wire_content = None
        if wire_content is None:
            continue
        evidence = _sanitize_evidence(
            block.get("evidence_anchors"), resolver=resolver, paper_title=paper_title
        )
        out.append({"type": type_, "content": wire_content, "evidence": evidence})
    return out


def sanitize_overview_figure(overview: dict[str, Any] | None) -> dict[str, Any] | None:
    """AI 生成の全体概要図(DSL)を公開ブロックへ縮約する。原論文の図ではない。"""
    if not overview:
        return None
    return {
        "type": "overview_figure",
        "content": {
            "dsl": overview.get("dsl") or {},
            "svg_url": overview.get("svg_url"),
            "raster_url": overview.get("raster_url"),
        },
        "evidence": [],
    }


def build_paper_meta(
    *,
    title: str,
    authors: list[str] | None = None,
    arxiv_id: str | None = None,
    doi: str | None = None,
    venue: str | None = None,
    published_on: str | None = None,
    license: str | None = None,
) -> dict[str, Any]:
    """公開スナップショットに載せる論文書誌(公開情報のみ)。本文・訳文は含めない。"""
    return {
        "title": title,
        "authors": list(authors or []),
        "arxiv_id": arxiv_id,
        "doi": doi,
        "venue": venue,
        "published_on": published_on,
        "license": license,
    }


__all__ = [
    "PUBLISHABLE_BLOCK_TYPES",
    "build_paper_meta",
    "sanitize_article_blocks",
    "sanitize_overview_figure",
]
