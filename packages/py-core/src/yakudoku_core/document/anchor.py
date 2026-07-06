"""アンカー仕様(docs/01 §5・plans/02 §3.1)。

注釈・チャット根拠・語彙・記事引用・リソースメモの位置参照を 1 形式に統一する。
表示用短縮表記(§2.2 ¶3・式(5))は保存せず block_search_index から導出する。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

QUOTE_MAX = 500


class AnchorJson(BaseModel):
    """共通位置参照。正は原文ブロック(訳文側で作られても block_id は原文ブロック)。"""

    revision_id: str
    block_id: str
    start: int | None = None
    end: int | None = None
    quote: str = ""
    side: Literal["source", "translation"] = "source"
    # 訳文側で作られた場合の補助オフセット(任意)
    translation_start: int | None = None
    translation_end: int | None = None

    @field_validator("quote")
    @classmethod
    def _truncate_quote(cls, v: str) -> str:
        return v[:QUOTE_MAX]
