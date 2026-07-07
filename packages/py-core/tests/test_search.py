"""M1-11: pgroonga_query の共有ヘルパ(plans/11 §3・§5)。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.search.pgroonga_query import (
    MARK_CLOSE,
    MARK_OPEN,
    chat_qa_snippet,
    finalize_snippet_html,
    is_valid_query,
    matched_in,
    normalize_query,
    snippet_lang_for,
    truncate_plain,
)


# ---- 入力正規化・妥当性(§3.1) ----
def test_normalize_query_trims_and_collapses_whitespace() -> None:
    assert normalize_query("  EMA   teacher  ") == "EMA teacher"
    assert normalize_query("\t整流フロー\n") == "整流フロー"


def test_is_valid_query_length_bounds() -> None:
    assert is_valid_query("a") is True
    assert is_valid_query("a" * 200) is True
    assert is_valid_query("") is False
    assert is_valid_query("a" * 201) is False


# ---- スニペット整形(§5.1) ----
def test_finalize_snippet_html_replaces_mark_and_wraps_ellipsis() -> None:
    raw = 'straight <span class="keyword">transport</span> paths'
    out = finalize_snippet_html(raw)
    assert out == f"…straight {MARK_OPEN}transport{MARK_CLOSE} paths…"


def test_finalize_snippet_html_clips_long_fragment_and_closes_open_mark() -> None:
    raw = '<span class="keyword">x</span>' + "y" * 600
    out = finalize_snippet_html(raw)
    assert len(out) <= 500
    assert out.endswith("…")
    # 開いた <mark> は閉じられている(壊れた HTML を返さない)。
    assert out.count(MARK_OPEN) == out.count(MARK_CLOSE)


def test_finalize_snippet_html_is_html_escaped_upstream() -> None:
    # pgroonga_snippet_html は元テキストを HTML エスケープして返す前提。ここでは
    # <script> のような文字列がそのまま来ても素通しする(エスケープ責務は SQL 側)ことのみ確認。
    raw = "&lt;script&gt;"
    out = finalize_snippet_html(raw)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ---- チャット Q/A スニペット(§5.1) ----
def test_chat_qa_snippet_user_hit_with_assistant_other_side() -> None:
    hit_html = f"…reflow は何回必要ですか{MARK_OPEN}?{MARK_CLOSE}…"
    out = chat_qa_snippet(
        hit_role="user",
        hit_snippet_html=hit_html,
        other_text_plain="1 回の reflow で経路が直線になります",
    )
    assert out.startswith(f"Q: {hit_html}")
    assert " — A: " in out
    assert "1 回の reflow" in out


def test_chat_qa_snippet_assistant_hit_without_user_pair() -> None:
    hit_html = f"1 回の {MARK_OPEN}reflow{MARK_CLOSE} で経路が直線になります"
    out = chat_qa_snippet(hit_role="assistant", hit_snippet_html=hit_html, other_text_plain=None)
    assert out == f"A: {hit_html}"


def test_truncate_plain_limits_to_60_chars_and_escapes() -> None:
    long_text = "a" * 70
    out = truncate_plain(long_text)
    assert out.endswith("…")
    assert len(out) == 61  # 60 文字 + 「…」
    assert truncate_plain("<b>ok</b>") == "&lt;b&gt;ok&lt;/b&gt;"


# ---- matched_in / snippet_lang(§3.4) ----
def test_matched_in_and_snippet_lang() -> None:
    assert matched_in(matched_source=True, matched_translation=False) == ["source"]
    assert matched_in(matched_source=False, matched_translation=True) == ["translation"]
    assert matched_in(matched_source=True, matched_translation=True) == ["source", "translation"]
    assert snippet_lang_for(["source"]) == "en"
    assert snippet_lang_for(["source", "translation"]) == "en"
    assert snippet_lang_for(["translation"]) == "ja"


# ---- PGroonga 統合(実 PostgreSQL): snippet 関数の生出力を最終形にできること ----
@pytest.mark.asyncio
async def test_pgroonga_snippet_html_roundtrip_with_finalize(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT pgroonga_snippet_html("
                "  'Rectified flow learns straight transport paths.',"
                "  pgroonga_query_extract_keywords(pgroonga_query_escape('transport paths')),"
                "  300"
                ") AS snippets"
            )
        )
    ).one()
    fragments = row.snippets
    assert fragments, "PGroonga から少なくとも1断片返る"
    finalized = finalize_snippet_html(fragments[0])
    assert MARK_OPEN in finalized
    assert finalized.startswith("…")
    assert finalized.endswith("…")
    await db_session.rollback()
