"""DB 保存形(article_blocks.content、フラット)→ API/ジョブ結果向け wire 形への変換
(plans/03 §19.1)。

apps/api(GET article)と apps/worker(block rewrite の ``jobs.result``)の両方が同じ変換を
必要とするため py-core に置く(worker は apps/api に依存できない — pyproject 参照)。
画像 URL 導出は apps/api の ``schemas/viewer.asset_url`` と同一フォーマットをここで独立に
持つ(1 行の純粋な文字列組み立てのため複製の実害は小さい)。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from alinea_core.document.blocks import DocumentContent
from alinea_core.search.rebuild import BlockIndexRow, compute_index_rows

_PARAGRAPH_LIKE = frozenset({"paragraph", "list", "quote", "theorem", "algorithm", "footnote"})


def article_block_wire_id(pk: int) -> str:
    return f"ablk_{pk}"


def parse_article_block_pk(wire_id: str) -> int | None:
    if not wire_id.startswith("ablk_"):
        return None
    try:
        return int(wire_id[len("ablk_") :])
    except ValueError:
        return None


def derive_display(row: BlockIndexRow) -> str:
    """block_search_index 相当の 1 行から短縮表記を導出する。

    chat.evidence.derive_display と同型だが、apps/api への依存を避けるため独立に持つ
    (worker は apps/api に依存できない)。
    """
    if row.block_type == "equation" and row.element_label:
        return row.element_label
    if row.block_type in ("figure", "table") and row.element_label:
        return row.element_label
    if row.block_type in _PARAGRAPH_LIKE and row.paragraph_ordinal is not None:
        return f"{row.section_label} ¶{row.paragraph_ordinal}"
    return row.section_label


class EvidenceDisplayResolver:
    """revision の ``DocumentContent`` から block_id/sec_id -> display の辞書引きを提供する。"""

    def __init__(self, content: DocumentContent) -> None:
        rows = compute_index_rows(content)
        self._by_block: dict[str, BlockIndexRow] = {r.block_id: r for r in rows}
        self._section_label: dict[str, str] = {}
        for r in rows:
            parts = [p for p in r.section_path.split("/") if p]
            if parts:
                self._section_label.setdefault(parts[-1], r.section_label)
            for part in parts:
                self._section_label.setdefault(part, r.section_label)

    def display_for(self, block_id: str) -> str | None:
        row = self._by_block.get(block_id)
        if row is not None:
            return derive_display(row)
        return self._section_label.get(block_id)


@dataclass(frozen=True)
class ExplainerRef:
    """slot に対応する現行 ``ExplainerFigure``(M2-06 が生成)。未生成なら呼び出し側は渡さない。"""

    figure_id: str
    image_url: str
    caption: str


def _asset_url(storage_key: str | None) -> str:
    if not storage_key:
        return ""
    raw = storage_key.encode("utf-8")
    asset_id = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"/api/assets/{asset_id}"


def _anchor_wire(
    block_id: str, revision_id: str, resolver: EvidenceDisplayResolver
) -> dict[str, Any]:
    return {
        "revision_id": revision_id,
        "block_id": block_id,
        "start": None,
        "end": None,
        "quote": None,
        "side": "source",
        "display": resolver.display_for(block_id) or block_id,
    }


def build_evidence_wire(
    evidence_anchors: list[dict[str, Any]], resolver: EvidenceDisplayResolver
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, anchor in enumerate(evidence_anchors, start=1):
        block_id = str(anchor.get("block_id", ""))
        out.append(
            {
                "ref": i,
                "display": resolver.display_for(block_id) or block_id,
                "anchor": _anchor_wire(block_id, str(anchor.get("revision_id", "")), resolver),
            }
        )
    return out


def block_content_to_wire(
    type_: str,
    content: dict[str, Any],
    *,
    evidence_resolver: EvidenceDisplayResolver,
    explainer_lookup: dict[int, ExplainerRef] | None = None,
) -> dict[str, Any]:
    """DB content(フラット)→ ``ArticleBlock.content``(plans/03 §19.1、ネスト形)。"""
    if type_ == "heading":
        return {"heading": {"level": content.get("level"), "text": content.get("text", "")}}
    if type_ == "paragraph":
        return {"markdown": content.get("md", "")}
    if type_ == "quote_source":
        block_id = str(content.get("block_id", ""))
        revision_id = str(content.get("revision_id", ""))
        return {
            "quote": {
                "text_en": content.get("text_en", ""),
                "anchor": _anchor_wire(block_id, revision_id, evidence_resolver),
            }
        }
    if type_ == "figure_embed":
        if content.get("variant") == "figure_link_card":
            return {
                "figure_link_card": {
                    "figure_display": content.get("figure_display", ""),
                    "message": content.get("message", ""),
                }
            }
        return {
            "figure": {
                "figure_block_id": content.get("figure_block_id", ""),
                "image_url": _asset_url(content.get("asset_key")),
                "caption_ja": content.get("caption_ja", ""),
                "credit": content.get("credit", ""),
                "license_badge": content.get("license_badge", ""),
                # docs/09 §5.2: CC BY-ND はキャプションを図と分離、CC BY-SA は SA 表示を要する。
                # 現行 apps/api の FigureContentOut はこの 2 キーを未対応(followups 参照) —
                # 追加キーは Pydantic 既定の extra="ignore" で無害に無視される。
                "caption_separated": bool(content.get("caption_separated", False)),
                "share_alike": bool(content.get("share_alike", False)),
            }
        }
    if type_ == "explainer_figure":
        slot = int(content.get("slot", 0))
        ref = (explainer_lookup or {}).get(slot)
        return {
            "explainer": {
                "figure_id": ref.figure_id if ref else "",
                "image_url": ref.image_url if ref else "",
                "caption": ref.caption if ref else content.get("caption_ja", ""),
            }
        }
    if type_ == "discussion":
        items = content.get("items", [])
        return {
            "discussion": {
                "items": [
                    {"text": i.get("md", ""), "origin": i.get("origin", "ai")}
                    for i in items
                    if isinstance(i, dict)
                ]
            }
        }
    if type_ == "attribution":
        return {"attribution": {"text": content.get("text", "")}}
    return {}


def build_article_block_wire(
    *,
    pk: int,
    type_: str,
    content: dict[str, Any],
    evidence_anchors: list[dict[str, Any]],
    origin: str,
    resolver: EvidenceDisplayResolver,
    explainer_lookup: dict[int, ExplainerRef] | None = None,
) -> dict[str, Any]:
    """1 ブロックの ``ArticleBlock`` wire 形(plans/03 §19.1)。"""
    return {
        "id": article_block_wire_id(pk),
        "type": type_,
        "content": block_content_to_wire(
            type_, content, evidence_resolver=resolver, explainer_lookup=explainer_lookup
        ),
        "evidence": build_evidence_wire(evidence_anchors, resolver),
        "origin": origin,
        "locked": type_ == "attribution",
    }


__all__ = [
    "EvidenceDisplayResolver",
    "ExplainerRef",
    "article_block_wire_id",
    "block_content_to_wire",
    "build_article_block_wire",
    "build_evidence_wire",
    "derive_display",
    "parse_article_block_pk",
]
