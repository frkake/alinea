"""PY-FIG-01 / PY-FIG-02 / PY-FIG-02b(plans/07 §5.4.4・plans/12 §7)。

- PY-FIG-01: DSL→SVG で cards の label/heading/body 全テキストが過不足なく出現する。
- PY-FIG-02: 同一 DSL から 2 回レンダリングしてバイト同一。ゴールデン SVG と sha256 一致。
- PY-FIG-02b: サブプロセス独立(``PYTHONHASHSEED`` 依存の検出)。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from _data import GOLDEN_DIR, load_fixture
from alinea_figures.dsl import OverviewFigureDsl
from alinea_figures.overview_svg import (
    ARROW_ZONE_W,
    BODY_FS,
    BODY_MAX_LINES,
    CANVAS_W,
    CARD_FLEX,
    CARD_PAD_X,
    HEADING_FS,
    HEADING_MAX_LINES,
    LABEL_FS,
    LABEL_MAX_LINES,
    PAD_X,
    render_overview_svg,
)
from alinea_figures.wrap import wrap_text

SVG_NS = "{http://www.w3.org/2000/svg}"
_HERE = Path(__file__).parent


def _dsl_and_chips() -> tuple[OverviewFigureDsl, list[str]]:
    data: dict[str, Any] = load_fixture("overview_rectified_flow.json")
    return OverviewFigureDsl.model_validate(data["dsl"]), list(data["evidence_chips"])


def _text_widths() -> tuple[float, float, float]:
    card_area_w = CANVAS_W - 2 * PAD_X - 2 * ARROW_ZONE_W
    unit = card_area_w / sum(CARD_FLEX)
    w0 = round(unit * CARD_FLEX[0], 2)
    w2 = round(unit * CARD_FLEX[2], 2)
    w1 = round(card_area_w - w0 - w2, 2)
    return tuple(cw - 2 * CARD_PAD_X for cw in (w0, w1, w2))  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# PY-FIG-01
# --------------------------------------------------------------------------- #
def test_py_fig_01_all_card_text_present_without_corruption() -> None:
    dsl, chips = _dsl_and_chips()
    svg = render_overview_svg(dsl, evidence_chips=chips)
    root = ET.fromstring(svg)  # well-formed であること自体も検証する
    texts = [el.text or "" for el in root.iter(f"{SVG_NS}text")]

    text_widths = _text_widths()
    expected: list[str] = []
    for card, tw in zip(dsl.cards, text_widths, strict=True):
        expected.extend(wrap_text(card.label, tw, LABEL_FS, LABEL_MAX_LINES))
        expected.extend(wrap_text(card.heading, tw, HEADING_FS, HEADING_MAX_LINES))
        expected.extend(wrap_text(card.body, tw, BODY_FS, BODY_MAX_LINES))
    expected.extend(["→", "→"])
    assert texts[: len(expected)] == expected

    # 折返しで文字が欠落・破損していないこと(結合すれば元テキストと完全一致)。
    for card in dsl.cards:
        assert "".join(wrap_text(card.label, 10_000.0, LABEL_FS, LABEL_MAX_LINES)) == card.label
        assert (
            "".join(wrap_text(card.heading, 10_000.0, HEADING_FS, HEADING_MAX_LINES))
            == card.heading
        )
        assert "".join(wrap_text(card.body, 10_000.0, BODY_FS, BODY_MAX_LINES)) == card.body


# --------------------------------------------------------------------------- #
# PY-FIG-02
# --------------------------------------------------------------------------- #
def test_py_fig_02_two_renders_are_byte_identical() -> None:
    dsl, chips = _dsl_and_chips()
    out1 = render_overview_svg(dsl, evidence_chips=chips)
    out2 = render_overview_svg(dsl, evidence_chips=chips)
    assert out1 == out2


def test_py_fig_02_golden_sha256_matches() -> None:
    dsl, chips = _dsl_and_chips()
    out = render_overview_svg(dsl, evidence_chips=chips)
    golden = (GOLDEN_DIR / "overview_rectified_flow.svg").read_bytes()
    assert hashlib.sha256(out).hexdigest() == hashlib.sha256(golden).hexdigest()
    assert out == golden


def test_py_fig_02_output_is_well_formed_xml() -> None:
    dsl, chips = _dsl_and_chips()
    out = render_overview_svg(dsl, evidence_chips=chips)
    root = ET.fromstring(out)  # 例外にならなければ well-formed
    assert root.tag == f"{SVG_NS}svg"


def test_py_fig_02_no_xml_decl_or_comments() -> None:
    dsl, chips = _dsl_and_chips()
    out = render_overview_svg(dsl, evidence_chips=chips).decode("utf-8")
    assert not out.startswith("<?xml")
    assert "<!--" not in out


# --------------------------------------------------------------------------- #
# PY-FIG-02b: プロセス独立性(PYTHONHASHSEED 依存の検出)
# --------------------------------------------------------------------------- #
def test_py_fig_02b_process_independent_rendering() -> None:
    src_dir = _HERE.parent / "src"
    fixture_path = _HERE / "fixtures" / "overview_rectified_flow.json"
    script = (
        f"import json, sys; "
        f"sys.path.insert(0, {str(src_dir)!r}); "
        f"from alinea_figures.dsl import OverviewFigureDsl; "
        f"from alinea_figures.overview_svg import render_overview_svg; "
        f"data = json.load(open({str(fixture_path)!r}, encoding='utf-8')); "
        f"dsl = OverviewFigureDsl.model_validate(data['dsl']); "
        f"sys.stdout.buffer.write(render_overview_svg(dsl, evidence_chips=data['evidence_chips']))"
    )
    outputs = []
    for hashseed in ("0", "1", "random"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hashseed
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            env=env,
        )
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    golden = (GOLDEN_DIR / "overview_rectified_flow.svg").read_bytes()
    assert outputs[0] == golden
