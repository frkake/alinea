"""決定的テキスト折返し(plans/07 §5.4.3)。

フォントメトリクスに依存しない文字幅推定表で折返しを行うため、実行環境(フォント有無・OS)に
依存せず常に同一の結果を返す。
"""

from __future__ import annotations

import re
import unicodedata

#: ラテン語列(語単位で折返す)。
_LATIN_RUN = re.compile(r"[A-Za-z0-9.,()\-]+")
#: トークナイザ: ラテン語列 / 空白列 / それ以外の 1 文字(CJK は文字間で任意に改行可)。
_TOKENIZER = re.compile(r"[A-Za-z0-9.,()\-]+|\s+|.", re.DOTALL)

#: 行頭に置かない禁則文字(前行末に追い出す)。
FORBIDDEN_LEADING = "、。)」』"

ELLIPSIS = "…"


def char_width(ch: str, font_size: float) -> float:
    """1 文字の推定幅(plans/07 §5.4.3 の表)。"""
    if ch == " ":
        return 0.30 * font_size
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("F", "W", "A"):
        return 1.0 * font_size
    return 0.55 * font_size


def text_width(s: str, font_size: float) -> float:
    """文字列の推定幅(各文字幅の総和)。"""
    return sum(char_width(ch, font_size) for ch in s)


def _tokenize(text: str) -> list[str]:
    return _TOKENIZER.findall(text)


def _split_forced(token: str, max_width: float, font_size: float) -> list[str]:
    """行幅を超えるラテン語列を文字単位で強制分割する。"""
    parts: list[str] = []
    current = ""
    current_w = 0.0
    for ch in token:
        w = char_width(ch, font_size)
        if current and current_w + w > max_width:
            parts.append(current)
            current, current_w = "", 0.0
        current += ch
        current_w += w
    if current:
        parts.append(current)
    return parts


def _greedy_lines(text: str, max_width: float, font_size: float) -> list[str]:
    lines: list[str] = []
    current = ""
    current_w = 0.0
    for tok in _tokenize(text):
        tok_w = text_width(tok, font_size)
        if tok_w > max_width and _LATIN_RUN.fullmatch(tok):
            for piece in _split_forced(tok, max_width, font_size):
                piece_w = text_width(piece, font_size)
                if current and current_w + piece_w > max_width:
                    lines.append(current)
                    current, current_w = "", 0.0
                current += piece
                current_w += piece_w
            continue
        if current and current_w + tok_w > max_width:
            lines.append(current)
            current, current_w = "", 0.0
            if tok.isspace():
                continue  # 行頭の空白は捨てる
        current += tok
        current_w += tok_w
    if current:
        lines.append(current)
    return lines


def _apply_kinsoku(lines: list[str]) -> list[str]:
    """行頭禁則(、。)」』 を前行末に追い出す)。"""
    out = list(lines)
    for i in range(1, len(out)):
        while out[i] and out[i][0] in FORBIDDEN_LEADING:
            ch = out[i][0]
            out[i] = out[i][1:]
            out[i - 1] = out[i - 1] + ch
    return [line for line in out if line]


def _truncate_with_ellipsis(text: str, max_width: float, font_size: float) -> str:
    if text_width(text + ELLIPSIS, font_size) <= max_width:
        return text + ELLIPSIS
    truncated = text
    while truncated and text_width(truncated + ELLIPSIS, font_size) > max_width:
        truncated = truncated[:-1]
    return truncated + ELLIPSIS


def wrap_text(text: str, max_width: float, font_size: float, max_lines: int) -> list[str]:
    """テキストを ``max_width`` に収まる行に決定的に折返す。

    ``max_lines`` を超える場合は最終行を切詰めて ``…`` を付ける(plans/07 §5.4.3)。
    """
    if not text:
        return []
    lines = _apply_kinsoku(_greedy_lines(text, max_width, font_size))
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    kept[-1] = _truncate_with_ellipsis(kept[-1], max_width, font_size)
    return kept


__all__ = [
    "ELLIPSIS",
    "FORBIDDEN_LEADING",
    "char_width",
    "text_width",
    "wrap_text",
]
