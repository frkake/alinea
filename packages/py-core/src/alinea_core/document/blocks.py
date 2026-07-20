"""ブロック要素と構造化ドキュメント(docs/01 §4.1・§4.4)。

DocumentContent は document_revisions.content(JSONB)に格納する形と同型
(plans/02 §3.2 DocumentContentJson)。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from alinea_core.document.inlines import Inline

# docs/01 §4.1 の 12 種
BLOCK_TYPES = (
    "paragraph",
    "heading",
    "figure",
    "table",
    "equation",
    "code",
    "list",
    "quote",
    "theorem",
    "algorithm",
    "footnote",
    "reference_entry",
)

BlockType = Literal[
    "paragraph",
    "heading",
    "figure",
    "table",
    "equation",
    "code",
    "list",
    "quote",
    "theorem",
    "algorithm",
    "footnote",
    "reference_entry",
]


class Block(BaseModel):
    """構造化ドキュメントの最小単位。安定 ID(`blk-...`)を持つ。

    type ごとに使うフィールドが異なる(docs/01 §4.1):
    - paragraph/list item/quote/theorem/algorithm/footnote: inlines
    - heading: level(1-4) / number / title
    - figure: asset_key / caption(inlines) / label
    - table: cells or asset_key / caption / label
    - equation: latex / number / label
    - code: language / code
    - reference_entry: raw / structured(authors/year/title/url)
    品質 B(PDF 由来)は追加で page / bbox を持つ。
    """

    id: str
    type: BlockType
    inlines: list[Inline] = Field(default_factory=list)
    # heading
    level: int | None = None
    number: str | None = None
    title: str | None = None
    # figure / table / equation の label と参照
    label: str | None = None
    asset_key: str | None = None
    caption: list[Inline] = Field(default_factory=list)
    # equation / code
    latex: str | None = None
    language: str | None = None
    code: str | None = None
    # list
    ordered: bool | None = None
    items: list[list[Inline]] = Field(default_factory=list)
    # reference_entry
    raw: str | None = None
    structured: dict[str, Any] | None = None
    # 品質 B の位置情報
    page: int | None = None
    bbox: list[float] | None = None

    model_config = {"extra": "allow"}


class SectionHeading(BaseModel):
    number: str = ""
    title: str = ""


class Section(BaseModel):
    id: str
    heading: SectionHeading = Field(default_factory=SectionHeading)
    blocks: list[Block] = Field(default_factory=list)
    # 入れ子セクション(見出しツリー)
    sections: list[Section] = Field(default_factory=list)


class DocumentContent(BaseModel):
    """document_revisions.content の中身(plans/02 §3.2)。"""

    quality_level: Literal["A", "B"]
    sections: list[Section] = Field(default_factory=list)

    def iter_blocks(self) -> list[tuple[Section, Block]]:
        """全ブロックを (所属セクション, ブロック) の列で走査する(入れ子対応)。"""
        result: list[tuple[Section, Block]] = []

        def walk(sec: Section) -> None:
            for blk in sec.blocks:
                result.append((sec, blk))
            for sub in sec.sections:
                walk(sub)

        for s in self.sections:
            walk(s)
        return result


Section.model_rebuild()


def flatten_serialized_blocks(content: Mapping[str, object]) -> Iterator[Mapping[str, object]]:
    """Serialize された DocumentContent の JSONB dict からすべての leaf ブロックを yield する。

    Section は入れ子になる可能性があるため再帰的に走査する。
    content が期待外の型(非 dict・非 list)の場合は安全にスキップする。
    """
    sections = content.get("sections") if isinstance(content, Mapping) else None
    if not isinstance(sections, list):
        return
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        blocks = section.get("blocks")
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, Mapping):
                    yield block
        # 入れ子セクションを再帰処理
        sub_sections = section.get("sections")
        if isinstance(sub_sections, list):
            yield from flatten_serialized_blocks({"sections": sub_sections})


def iter_document_asset_keys(content: Mapping[str, object]) -> Iterator[str]:
    """DocumentContent の JSONB dict 内のすべての asset_key / thumbnail_key を yield する。"""
    for block in flatten_serialized_blocks(content):
        for field in ("asset_key", "thumbnail_key"):
            key = block.get(field)
            if isinstance(key, str) and key:
                yield key
