"""平文導出関数(plans/11 §9.1)。api / worker 共有の純関数。

同一入力→同一出力(プロパティテスト対象)。検索インデックスの source_text/text_plain の源。
"""

from __future__ import annotations

import re
from typing import Any

from yakudoku_core.document.blocks import Block
from yakudoku_core.document.inlines import Inline

_WS = re.compile(r"\s+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_EMPH = re.compile(r"[*_`]+")
_MD_HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_EVIDENCE = re.compile(r"⟦[A-Za-z]:\d+⟧")


def _collapse_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


def inline_to_plain(inlines: list[Inline]) -> str:
    """インライン列を検索用平文へ。text はそのまま、math は LaTeX、citation/ref はラベル。"""
    parts: list[str] = []
    for il in inlines:
        if il.t == "text" or il.t == "emphasis":
            parts.append(il.v)
        elif il.t == "math_inline":
            parts.append(il.v)
        elif il.t == "citation":
            parts.append(f"[{il.ref}]" if il.ref else "")
        elif il.t == "ref":
            # 表示ラベルがあれば使う。なければ ref をそのまま。
            parts.append(il.v or (il.ref or ""))
        elif il.t == "code_inline":
            parts.append(il.v)
        elif il.t == "url":
            parts.append(il.v or il.href or "")
        elif il.t == "footnote_ref":
            parts.append("")
        else:
            parts.append(il.v)
    return _collapse_ws(" ".join(p for p in parts if p))


def block_to_plain(block: Block) -> str:
    """ブロックを検索用平文へ。figure/table はキャプション、equation は LaTeX。"""
    if block.type == "heading":
        return _collapse_ws(f"{block.number or ''} {block.title or ''}")
    if block.type == "equation":
        return _collapse_ws(block.latex or "")
    if block.type == "code":
        return _collapse_ws(block.code or "")
    if block.type in ("figure", "table"):
        return inline_to_plain(block.caption)
    if block.type == "reference_entry":
        return _collapse_ws(block.raw or "")
    if block.type == "list":
        return _collapse_ws(" ".join(inline_to_plain(item) for item in block.items))
    return inline_to_plain(block.inlines)


def strip_markdown(md: str) -> str:
    """Markdown 記号・リンク・見出し・根拠プレースホルダを除去して平文化する。"""
    s = _EVIDENCE.sub("", md)
    s = _MD_LINK.sub(r"\1", s)
    s = _MD_HEADING.sub("", s)
    s = _MD_EMPH.sub("", s)
    s = s.replace("\n", " ")
    return _collapse_ws(s)


def chat_content_to_plain(content: dict[str, Any]) -> str:
    """ChatContentJson の segments[].md を種別問わず連結して平文化する。"""
    segments = content.get("segments", []) if isinstance(content, dict) else []
    joined = " ".join(str(seg.get("md", "")) for seg in segments if isinstance(seg, dict))
    return strip_markdown(joined)


def article_block_to_plain(type_: str, content: dict[str, Any]) -> str:
    """article_blocks の type 別平文導出(plans/11 §9.1)。"""
    if type_ == "heading":
        return _collapse_ws(str(content.get("text", "")))
    if type_ == "paragraph":
        return strip_markdown(str(content.get("md", "")))
    if type_ == "quote_source":
        return _collapse_ws(str(content.get("text_en", "")))
    if type_ == "figure_embed":
        return _collapse_ws(str(content.get("caption_ja", "")))
    if type_ == "discussion":
        items = content.get("items", [])
        return strip_markdown(" ".join(str(i.get("md", "")) for i in items if isinstance(i, dict)))
    # explainer_figure / attribution は索引しない
    return ""
