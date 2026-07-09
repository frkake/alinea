"""全体概要図 SVG 決定的レンダラ(plans/07 §5.4)。

同一 ``OverviewFigureDsl``(+ 同一 ``evidence_chips``)から常に**バイト同一**の SVG を生成する。
乱数・時刻・UUID を含めない。属性順・数値書式・改行/インデントはすべて固定規則で出力する
(汎用 XML ライブラリの属性順序に依存しない — plans/07 §5.4.4)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alinea_figures.dsl import CardTone, OverviewFigureDsl
from alinea_figures.wrap import text_width, wrap_text

# --------------------------------------------------------------------------- #
# レイアウト定数(plans/07 §5.4.1 逐語)
# --------------------------------------------------------------------------- #
CANVAS_W = 718.0
PAD_X, PAD_Y = 20.0, 18.0
ARROW_ZONE_W = 32.0
CARD_FLEX = (1.0, 1.2, 1.0)
CARD_PAD_X, CARD_PAD_Y = 14.0, 12.0
CARD_RADIUS = 8.0
CARD_TOP_BAR_H = 3.0
CARD_GAP = 7.0
LABEL_FS, LABEL_LH = 9.5, 13.0
HEADING_FS, HEADING_LH = 12.0, 19.2
BODY_FS, BODY_LH = 10.5, 17.33
ARROW_FS = 16.0
FOOTER_H = 30.0
FOOTER_FS = 9.5
CHIP_H, CHIP_FS, CHIP_PAD_X, CHIP_GAP = 15.0, 9.0, 6.0, 6.0
FONT_UI = "'IBM Plex Sans JP', sans-serif"

LABEL_MAX_LINES = 1
HEADING_MAX_LINES = 3
BODY_MAX_LINES = 4

#: テキストのベースライン位置(行ボックス上端からの比率。固定・決定的な近似値)。
_BASELINE_RATIO = 0.72

# --------------------------------------------------------------------------- #
# 色トークン(plans/07 §5.4.2 逐語。CSS 変数 + 既定値フォールバック)
# --------------------------------------------------------------------------- #
_ACCENT_FG = "var(--pr-a, #3E5C76)"
_ACCENT_BORDER = "var(--pr-am, rgba(62,92,118,0.32))"

CANVAS_BG = "#FFFFFF"
FOOTER_BG = "#FBFAF7"
FOOTER_TOP_BORDER = "#F0EDE4"
HEADING_COLOR = "#1E2227"
BODY_COLOR = "#5B6067"
ARROW_COLOR = "#B0B4BA"
FOOTER_TEXT_COLOR = "#9A9EA4"
CHIP_BORDER = _ACCENT_BORDER
CHIP_TEXT = _ACCENT_FG


class _ToneColors:
    __slots__ = ("bg", "border", "label", "top_bar")

    def __init__(self, border: str, top_bar: str, bg: str, label: str) -> None:
        self.border = border
        self.top_bar = top_bar
        self.bg = bg
        self.label = label


TONE_COLORS: dict[CardTone, _ToneColors] = {
    "neutral": _ToneColors(border="#E2DFD5", top_bar="#B0ACA2", bg="#FBFAF7", label="#8A8E94"),
    "accent": _ToneColors(
        border=_ACCENT_BORDER, top_bar=_ACCENT_FG, bg="#FFFFFF", label=_ACCENT_FG
    ),
    "green": _ToneColors(border="#E2DFD5", top_bar="#659471", bg="#FBFAF7", label="#4C7458"),
}


# --------------------------------------------------------------------------- #
# 決定的な数値・文字列フォーマット(plans/07 §5.4.4 の規則(2)(3))
# --------------------------------------------------------------------------- #
def _fmt(x: float) -> str:
    return format(round(x, 2), "g")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class _Writer:
    def __init__(self) -> None:
        self._lines: list[str] = []

    def add(self, line: str) -> None:
        self._lines.append("  " + line)

    def render(self) -> bytes:
        return ("\n".join(self._lines) + "\n").encode("utf-8")


def _rect(
    w: _Writer,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    fill: str,
    stroke: str | None = None,
    rx: float | None = None,
) -> None:
    attrs = f'x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(width)}" height="{_fmt(height)}"'
    if rx is not None:
        attrs += f' rx="{_fmt(rx)}"'
    attrs += f' fill="{fill}"'
    if stroke is not None:
        attrs += f' stroke="{stroke}"'
    w.add(f"<rect {attrs}/>")


def _top_bar_path(
    w: _Writer, *, x: float, y: float, width: float, height: float, radius: float, fill: str
) -> None:
    """上辺のみ角丸の帯(カード上バー。plans/07 §5.4.4)。"""
    r = min(radius, height, width / 2)
    d = (
        f"M{_fmt(x)},{_fmt(y + height)} "
        f"L{_fmt(x)},{_fmt(y + r)} "
        f"Q{_fmt(x)},{_fmt(y)} {_fmt(x + r)},{_fmt(y)} "
        f"L{_fmt(x + width - r)},{_fmt(y)} "
        f"Q{_fmt(x + width)},{_fmt(y)} {_fmt(x + width)},{_fmt(y + r)} "
        f"L{_fmt(x + width)},{_fmt(y + height)} Z"
    )
    w.add(f'<path d="{d}" fill="{fill}"/>')


def _text(
    w: _Writer,
    *,
    x: float,
    y: float,
    content: str,
    font_size: float,
    fill: str,
    weight: int | None = None,
    anchor: str | None = None,
    letter_spacing: float | None = None,
) -> None:
    attrs = f'x="{_fmt(x)}" y="{_fmt(y)}" font-size="{_fmt(font_size)}"'
    if weight is not None:
        attrs += f' font-weight="{weight}"'
    if anchor is not None:
        attrs += f' text-anchor="{anchor}"'
    if letter_spacing is not None:
        attrs += f' letter-spacing="{_fmt(letter_spacing)}"'
    attrs += f' fill="{fill}"'
    w.add(f"<text {attrs}>{_esc(content)}</text>")


def _card_widths() -> tuple[float, float, float]:
    card_area_w = CANVAS_W - 2 * PAD_X - 2 * ARROW_ZONE_W
    unit = card_area_w / sum(CARD_FLEX)
    w0 = round(unit * CARD_FLEX[0], 2)
    w2 = round(unit * CARD_FLEX[2], 2)
    w1 = round(
        card_area_w - w0 - w2, 2
    )  # 端数は中央カードで吸収し合計=card_area_w(plans/07 §5.4.4)
    return (w0, w1, w2)


def render_overview_svg(dsl: OverviewFigureDsl, *, evidence_chips: Sequence[str] = ()) -> bytes:
    """DSL から決定的に SVG バイト列を生成する(plans/07 §5.4)。"""
    card_widths = _card_widths()
    text_widths = tuple(cw - 2 * CARD_PAD_X for cw in card_widths)

    wrapped_labels = [
        wrap_text(c.label, tw, LABEL_FS, LABEL_MAX_LINES)
        for c, tw in zip(dsl.cards, text_widths, strict=True)
    ]
    wrapped_headings = [
        wrap_text(c.heading, tw, HEADING_FS, HEADING_MAX_LINES)
        for c, tw in zip(dsl.cards, text_widths, strict=True)
    ]
    wrapped_bodies = [
        wrap_text(c.body, tw, BODY_FS, BODY_MAX_LINES)
        for c, tw in zip(dsl.cards, text_widths, strict=True)
    ]

    content_heights = [
        LABEL_LH
        + CARD_GAP
        + len(wrapped_headings[i]) * HEADING_LH
        + CARD_GAP
        + len(wrapped_bodies[i]) * BODY_LH
        for i in range(3)
    ]
    card_h = max(content_heights) + 2 * CARD_PAD_Y + CARD_TOP_BAR_H
    svg_h = PAD_Y + card_h + PAD_Y + FOOTER_H

    card_x = [0.0, 0.0, 0.0]
    card_x[0] = PAD_X
    card_x[1] = card_x[0] + card_widths[0] + ARROW_ZONE_W
    card_x[2] = card_x[1] + card_widths[1] + ARROW_ZONE_W
    card_y = PAD_Y

    w = _Writer()
    w.add(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(CANVAS_W)}" '
        f'height="{_fmt(svg_h)}" viewBox="0 0 {_fmt(CANVAS_W)} {_fmt(svg_h)}" '
        f'font-family="{FONT_UI}">'
    )
    _rect(w, x=0.0, y=0.0, width=CANVAS_W, height=svg_h, fill=CANVAS_BG)

    for i, card in enumerate(dsl.cards):
        colors = TONE_COLORS[card.tone]
        x, cw = card_x[i], card_widths[i]
        _rect(
            w,
            x=x,
            y=card_y,
            width=cw,
            height=card_h,
            fill=colors.bg,
            stroke=colors.border,
            rx=CARD_RADIUS,
        )
        _top_bar_path(
            w,
            x=x,
            y=card_y,
            width=cw,
            height=CARD_TOP_BAR_H,
            radius=CARD_RADIUS,
            fill=colors.top_bar,
        )
        text_x = x + CARD_PAD_X
        cursor_y = card_y + CARD_TOP_BAR_H + CARD_PAD_Y

        for line in wrapped_labels[i]:
            _text(
                w,
                x=text_x,
                y=cursor_y + LABEL_LH * _BASELINE_RATIO,
                content=line,
                font_size=LABEL_FS,
                fill=colors.label,
                weight=700,
                letter_spacing=0.6,
            )
            cursor_y += LABEL_LH
        cursor_y += CARD_GAP

        for line in wrapped_headings[i]:
            _text(
                w,
                x=text_x,
                y=cursor_y + HEADING_LH * _BASELINE_RATIO,
                content=line,
                font_size=HEADING_FS,
                fill=HEADING_COLOR,
                weight=600,
            )
            cursor_y += HEADING_LH
        cursor_y += CARD_GAP

        for line in wrapped_bodies[i]:
            _text(
                w,
                x=text_x,
                y=cursor_y + BODY_LH * _BASELINE_RATIO,
                content=line,
                font_size=BODY_FS,
                fill=BODY_COLOR,
            )
            cursor_y += BODY_LH

    arrow_y = card_y + card_h / 2 + ARROW_FS * 0.36
    for i in range(2):
        arrow_x = card_x[i] + card_widths[i] + ARROW_ZONE_W / 2
        _text(
            w,
            x=arrow_x,
            y=arrow_y,
            content="→",
            font_size=ARROW_FS,
            fill=ARROW_COLOR,
            anchor="middle",
        )

    _footer(w, card_y=card_y, card_h=card_h, dsl=dsl, chips=list(evidence_chips))

    w.add("</svg>")
    return w.render()


def _footer(
    w: _Writer, *, card_y: float, card_h: float, dsl: OverviewFigureDsl, chips: list[str]
) -> None:
    footer_top = card_y + card_h + PAD_Y
    footer_mid_y = footer_top + FOOTER_H / 2 + FOOTER_FS * 0.36

    w.add(
        f'<line x1="{_fmt(0.0)}" y1="{_fmt(footer_top)}" x2="{_fmt(CANVAS_W)}" '
        f'y2="{_fmt(footer_top)}" stroke="{FOOTER_TOP_BORDER}"/>'
    )
    _rect(w, x=0.0, y=footer_top, width=CANVAS_W, height=FOOTER_H, fill=FOOTER_BG)

    left_text = f"{dsl.footer.generated_by} · {dsl.footer.date}"
    _text(
        w, x=PAD_X, y=footer_mid_y, content=left_text, font_size=FOOTER_FS, fill=FOOTER_TEXT_COLOR
    )

    if not chips:
        return

    right_edge = CANVAS_W - PAD_X
    label = "根拠:"  # 根拠:
    label_w = text_width(label, FOOTER_FS)
    min_left = PAD_X + label_w + CHIP_GAP

    boxes: list[tuple[float, float, str]] = []
    cursor_x = right_edge
    for chip_text in reversed(chips):
        chip_w = 2 * CHIP_PAD_X + text_width(chip_text, CHIP_FS)
        candidate_x = cursor_x - chip_w
        if candidate_x < min_left:
            break
        boxes.append((candidate_x, chip_w, chip_text))
        cursor_x = candidate_x - CHIP_GAP
    boxes.reverse()
    if not boxes:
        return

    _text(
        w,
        x=boxes[0][0] - CHIP_GAP,
        y=footer_mid_y,
        content=label,
        font_size=FOOTER_FS,
        fill=FOOTER_TEXT_COLOR,
        anchor="end",
    )
    chip_y = footer_top + (FOOTER_H - CHIP_H) / 2
    for chip_x, chip_w, chip_text in boxes:
        _rect(
            w,
            x=chip_x,
            y=chip_y,
            width=chip_w,
            height=CHIP_H,
            fill=CANVAS_BG,
            stroke=CHIP_BORDER,
            rx=CHIP_H / 2,
        )
        _text(
            w,
            x=chip_x + chip_w / 2,
            y=chip_y + CHIP_H / 2 + CHIP_FS * 0.36,
            content=chip_text,
            font_size=CHIP_FS,
            fill=CHIP_TEXT,
            anchor="middle",
        )


__all__ = [
    "ARROW_FS",
    "BODY_FS",
    "BODY_LH",
    "CANVAS_W",
    "CARD_FLEX",
    "CARD_GAP",
    "CARD_PAD_X",
    "CARD_PAD_Y",
    "CARD_RADIUS",
    "CARD_TOP_BAR_H",
    "CHIP_FS",
    "CHIP_GAP",
    "CHIP_H",
    "CHIP_PAD_X",
    "FONT_UI",
    "FOOTER_FS",
    "FOOTER_H",
    "HEADING_FS",
    "HEADING_LH",
    "LABEL_FS",
    "LABEL_LH",
    "PAD_X",
    "PAD_Y",
    "TONE_COLORS",
    "render_overview_svg",
]
