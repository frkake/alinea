"""表セル/キャプションの制御文字サニタイズ回帰テスト。

実測(arXiv 2307.09288 Llama 2)で、モデルが表セル訳文に改行を混ぜると
``TableTranslationContent`` 構築時に ``_has_control`` が ``\\n``(Unicode カテゴリ Cc)を
拒否し、翻訳ジョブが 4 回リトライして terminal 失敗 → 取り込み全体が停止した。

修正: :func:`_table_inline_projection` と :func:`_sanitize_caption_inlines` が構築前に
空白制御(``\\t\\n\\r``)を空白へ正規化し、その他の C0/C1 制御を除去する。危険制御
(NUL/ESC 等)の除去というセキュリティ意図は維持される。
"""

from __future__ import annotations

from alinea_core.translation.pipeline import (
    _sanitize_caption_inlines,
    _table_inline_projection,
)
from alinea_core.translation.table_cells import (
    TableTranslationContent,
    _has_control,
)


def test_table_inline_projection_strips_control_chars() -> None:
    # 実測の失敗入力形状: セル訳文に改行が混ざる。
    inlines = [{"t": "text", "v": "228 プロンプト:\n質問はありますか？"}]  # noqa: RUF001 - intentional fullwidth question mark
    out = _table_inline_projection(inlines)
    assert "\n" not in out
    assert not _has_control(out)
    # 訳語は保たれ、改行は空白へ正規化される(値の破棄ではない)。
    assert "プロンプト" in out and "質問はありますか" in out


def test_table_inline_projection_normalizes_tabs_and_drops_dangerous_controls() -> None:
    inlines = [{"t": "text", "v": "col A\tcol B"}, {"t": "text", "v": "x\x00\x1by"}]
    out = _table_inline_projection(inlines)
    assert not _has_control(out)
    assert "\t" not in out and "\x00" not in out and "\x1b" not in out
    # タブは空白へ、NUL/ESC は除去。
    assert "col A col B" in out
    assert "xy" in out


def test_table_content_constructs_after_projection_without_raising() -> None:
    # 修正前はこの構築が ValidationError を送出して terminal 失敗していた。
    cell = _table_inline_projection([{"t": "text", "v": "行1\n行2"}])
    content = TableTranslationContent(kind="table", version=1, caption=None, cells=[[cell]])
    assert content.cells is not None
    assert not _has_control(content.cells[0][0] or "")


def test_sanitize_caption_inlines_strips_control_chars_recursively() -> None:
    caption = [
        {"t": "text", "v": "Figure\ncaption"},
        {"t": "emphasis", "children": [{"t": "text", "v": "nested\ttext"}]},
    ]
    cleaned = _sanitize_caption_inlines(caption)
    # サニタイズ後は _validate_caption(=構築)が例外を投げない。
    content = TableTranslationContent(kind="table", version=1, caption=cleaned, cells=None)
    assert content.caption is not None
    flat = cleaned[0]["v"] + cleaned[1]["children"][0]["v"]
    assert not _has_control(flat)
    assert "caption" in cleaned[0]["v"] and "nested" in cleaned[1]["children"][0]["v"]
