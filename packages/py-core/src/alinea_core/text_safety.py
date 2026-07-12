"""外部由来テキストを DB・JSON・画面へ安全に渡すための共通処理。"""

from __future__ import annotations

import unicodedata
from typing import Any

_PRESERVED_CONTROLS = frozenset({"\t", "\n", "\r"})
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cs"})


def sanitize_untrusted_text(value: str) -> str:
    """NUL、表示制御文字、不正なサロゲートを除去する。

    PostgreSQL の UTF-8 text/JSONB は NUL とサロゲートを保存できない。Markdown の構造に
    必要なタブ・改行・復帰だけは保持し、それ以外の C0/C1 制御文字も画面崩れを防ぐため
    除去する。
    """
    return "".join(
        char
        for char in value
        if char in _PRESERVED_CONTROLS or unicodedata.category(char) not in _UNSAFE_CATEGORIES
    )


def sanitize_json_text(value: Any) -> Any:
    """JSON 相当の入れ子構造に含まれる全文字列を再帰的に無害化する。"""
    if isinstance(value, str):
        return sanitize_untrusted_text(value)
    if isinstance(value, list):
        return [sanitize_json_text(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_json_text(item) for item in value)
    if isinstance(value, dict):
        return {
            sanitize_untrusted_text(key) if isinstance(key, str) else key: sanitize_json_text(item)
            for key, item in value.items()
        }
    return value


__all__ = ["sanitize_json_text", "sanitize_untrusted_text"]
