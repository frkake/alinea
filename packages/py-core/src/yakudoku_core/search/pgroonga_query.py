"""PGroonga クエリ・スニペット生成の共有ヘルパ(plans/11 §3・§5)。

api / worker から共有する純関数群。実際の全文検索・スコアリングは PostgreSQL 側
(`&@~` 演算子・`pgroonga_query_escape` 等)が担い、ここでは:

- クエリ文字列の正規化・妥当性検証(§3.1)
- `pgroonga_snippet_html` の 1 断片を最終 HTML(`<mark class="yk-search-hit">`)へ整形(§5.1)
- チャット Q/A ペアのスニペット組み立て(§5.1)
- `matched_in` / `snippet_lang` の導出(§3.4)

を行う。DB 呼び出しは含まない(呼び出し側が SQL 関数の結果を渡す)。
"""

from __future__ import annotations

import html
import re
from typing import Literal

MARK_OPEN = '<mark class="yk-search-hit">'
MARK_CLOSE = "</mark>"
_PGROONGA_KEYWORD_OPEN = '<span class="keyword">'
_PGROONGA_KEYWORD_CLOSE = "</span>"

# plans/11 §3.1: q は 1〜200 字。
MAX_QUERY_LEN = 200
# plans/11 §5.1: 1 ヒット 1 断片、HTML 込み最大 500 文字。
SNIPPET_MAX_CHARS = 500
# plans/11 §5.1: チャット相手側は先頭 60 文字(ハイライトなし)。
CHAT_OTHER_SIDE_MAX_CHARS = 60

_WS = re.compile(r"\s+")

MatchedInValue = Literal["source", "translation"]


def normalize_query(raw: str) -> str:
    """前後空白 trim・連続空白を半角 1 個に圧縮する(plans/11 §3.1 手順 1)。"""
    return _WS.sub(" ", raw.strip())


def is_valid_query(q: str) -> bool:
    """1〜200 字(空文字・201 字以上は不可)。呼び出し側が 422 を返す判定に使う。"""
    return 1 <= len(q) <= MAX_QUERY_LEN


def _clip_html(text_html: str, limit: int) -> str:
    """HTML 込みで `limit` 文字を超えたら切り詰め、開いたままの `<mark>` を閉じて「…」を付す。"""
    if len(text_html) <= limit:
        return text_html
    truncated = text_html[: max(0, limit - 1)]
    opens = truncated.count(MARK_OPEN)
    closes = truncated.count(MARK_CLOSE)
    if opens > closes:
        truncated += MARK_CLOSE
    return truncated + "…"


def finalize_snippet_html(raw_fragment: str) -> str:
    """`pgroonga_snippet_html` の 1 断片を最終形へ整形する(plans/11 §5.1 確定手順)。

    - `<span class="keyword">` → `<mark class="yk-search-hit">`、`</span>` → `</mark>`
    - 前後に省略記号「…」を常に付す(断片が本文の先頭/末尾に接しているかは判定しない)
    - 500 文字上限(超過は切り詰め+「…」)

    `pgroonga_snippet_html` は元テキストを HTML エスケープ済みで返すため、この関数の
    入力・出力ともに安全な HTML である(XSS 不可)。
    """
    marked = raw_fragment.replace(_PGROONGA_KEYWORD_OPEN, MARK_OPEN).replace(
        _PGROONGA_KEYWORD_CLOSE, MARK_CLOSE
    )
    wrapped = f"…{marked}…"
    return _clip_html(wrapped, SNIPPET_MAX_CHARS)


def truncate_plain(text: str, limit: int = CHAT_OTHER_SIDE_MAX_CHARS) -> str:
    """ハイライトなし平文の切り詰め+HTML エスケープ(plans/11 §5.1 チャット相手側)。"""
    if len(text) <= limit:
        return html.escape(text)
    return html.escape(text[:limit]) + "…"


def chat_qa_snippet(*, hit_role: str, hit_snippet_html: str, other_text_plain: str | None) -> str:
    """チャットの Q/A スニペットを組み立てる(plans/11 §5.1 確定)。

    ヒットした側は `finalize_snippet_html` 済みの HTML、相手側は平文の先頭 60 文字
    (ハイライトなし)。片側が無い(相手が存在しない)場合はある側のみを返す
    (`Q:` / `A:` プレフィックスは維持)。
    """
    other_html = truncate_plain(other_text_plain) if other_text_plain else None
    q_part = hit_snippet_html if hit_role == "user" else other_html
    a_part = hit_snippet_html if hit_role == "assistant" else other_html
    parts: list[str] = []
    if q_part is not None:
        parts.append(f"Q: {q_part}")
    if a_part is not None:
        parts.append(f"A: {a_part}")
    return _clip_html(" — ".join(parts), SNIPPET_MAX_CHARS)


def matched_in(*, matched_source: bool, matched_translation: bool) -> list[MatchedInValue]:
    """本文ヒットの `matched_in` を導出する(plans/11 §3.4)。"""
    result: list[MatchedInValue] = []
    if matched_source:
        result.append("source")
    if matched_translation:
        result.append("translation")
    return result


def snippet_lang_for(matched: list[MatchedInValue]) -> Literal["en", "ja"]:
    """原文ヒットを含むなら `en`、訳文のみなら `ja`(plans/11 §3.4 スニペット採用面)。"""
    return "en" if "source" in matched else "ja"


__all__ = [
    "CHAT_OTHER_SIDE_MAX_CHARS",
    "MARK_CLOSE",
    "MARK_OPEN",
    "MAX_QUERY_LEN",
    "SNIPPET_MAX_CHARS",
    "MatchedInValue",
    "chat_qa_snippet",
    "finalize_snippet_html",
    "is_valid_query",
    "matched_in",
    "normalize_query",
    "snippet_lang_for",
    "truncate_plain",
]
