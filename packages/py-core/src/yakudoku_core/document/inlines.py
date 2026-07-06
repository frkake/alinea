"""インライン要素(docs/01 §4.2)。text 以外は翻訳時にプレースホルダ化して保護する。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# docs/01 §4.2 の 8 種
INLINE_TYPES = (
    "text",
    "math_inline",
    "citation",
    "ref",
    "footnote_ref",
    "url",
    "emphasis",
    "code_inline",
)

InlineType = Literal[
    "text",
    "math_inline",
    "citation",
    "ref",
    "footnote_ref",
    "url",
    "emphasis",
    "code_inline",
]


class Inline(BaseModel):
    """インライン要素。docs/01 §4.4 の JSON 契約(`t` がタグ、`v` が値)。

    - text: v にプレーンテキスト
    - math_inline: v に LaTeX
    - citation: ref に reference_entry の id
    - ref: kind(figure|table|equation|section)+ ref に対象 label/id
    - footnote_ref: ref に脚注 id
    - url: v に表示テキスト、href に URL
    - emphasis: v にテキスト(強調)
    - code_inline: v にコード文字列
    """

    t: InlineType
    v: str = ""
    ref: str | None = None
    kind: str | None = None
    href: str | None = None

    model_config = {"extra": "allow"}
