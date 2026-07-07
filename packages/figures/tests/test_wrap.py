"""決定的テキスト折返し(plans/07 §5.4.3)のユニットテスト。"""

from __future__ import annotations

from yakudoku_figures.wrap import char_width, text_width, wrap_text


def test_char_width_table() -> None:
    assert char_width("あ", 10.0) == 10.0  # 全角
    assert char_width("A", 10.0) == 5.5  # 半角英数
    assert char_width(" ", 10.0) == 3.0  # 半角スペース


def test_no_wrap_when_fits() -> None:
    assert wrap_text("課題", 1000.0, 10.0, 1) == ["課題"]


def test_wraps_cjk_at_arbitrary_boundary() -> None:
    text = "課題提案結果まとめ"
    width = text_width("課題提案", 10.0)  # 4 文字ぎりぎり
    lines = wrap_text(text, width, 10.0, 10)
    assert "".join(lines) == text
    assert all(text_width(line, 10.0) <= width for line in lines)


def test_latin_run_kept_as_word_unit() -> None:
    text = "RECTIFIED FLOW model"
    # "RECTIFIED" がちょうど収まる幅(超えたら次行へ全体で送られる=強制分割されない)
    width = text_width("RECTIFIED", 10.0) + 1
    lines = wrap_text(text, width, 10.0, 10)
    assert lines[0] == "RECTIFIED"


def test_forced_split_of_overlong_latin_word() -> None:
    text = "SUPERCALIFRAGILISTIC"
    width = text_width("SUPER", 10.0)
    lines = wrap_text(text, width, 10.0, 10)
    assert "".join(lines) == text
    assert all(text_width(line, 10.0) <= width + 1e-6 for line in lines)


def test_kinsoku_no_forbidden_leading_char() -> None:
    text = "これは長い文です。次の行に句点が来ないようにする。"
    width = text_width("これは長い文です", 10.0)
    lines = wrap_text(text, width, 10.0, 10)
    for line in lines[1:]:
        assert line[0] not in "、。)」』"
    assert "".join(lines) == text


def test_max_lines_truncates_with_ellipsis() -> None:
    text = "あ" * 20
    width = text_width("あああ", 10.0)
    lines = wrap_text(text, width, 10.0, 2)
    assert len(lines) == 2
    assert lines[-1].endswith("…")


def test_empty_text_returns_empty_list() -> None:
    assert wrap_text("", 100.0, 10.0, 3) == []


def test_deterministic_repeated_calls() -> None:
    text = "課題 → 提案 — RECTIFIED FLOW → 結果"
    width = text_width("課題→提案", 10.0)
    first = wrap_text(text, width, 10.0, 5)
    second = wrap_text(text, width, 10.0, 5)
    assert first == second
