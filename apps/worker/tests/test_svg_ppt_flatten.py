"""Unit tests for the ppt-master SVG style flattener.

The LLM emits SVGs with a ``<style>`` block, ``class`` attributes, and
``<g opacity>`` — the three constructs ppt-master's quality gate hard-errors on,
and (for CSS classes) the converter silently ignores. ``flatten_svg_for_ppt``
must rewrite them to inline presentation attributes without losing the styling.
These tests pin the exact contract against real generated-SVG shapes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from alinea_worker.presentation.svg_ppt import flatten_svg_for_ppt

_SVG_NS = "http://www.w3.org/2000/svg"


def _parse(data: bytes) -> ET.Element:
    return ET.fromstring(data)


def _find_all(root: ET.Element, local: str) -> list[ET.Element]:
    return [e for e in root.iter() if str(e.tag).rsplit("}", 1)[-1] == local]


def _opacity(el: ET.Element) -> float:
    """Return the element's ``opacity`` as a float, asserting it is present.

    ``Element.get`` is typed ``str | None``; the tests below require the folded
    opacity to exist, so assert-then-cast keeps both mypy and the intent honest.
    """
    value = el.get("opacity")
    assert value is not None, "expected opacity attribute to be present"
    return float(value)


def _forbidden_present(text: str) -> list[str]:
    """Mirror the checker's three hard-error style constructs."""
    import re

    hits = []
    lowered = text.lower()
    if "<style" in lowered:
        hits.append("<style>")
    if re.search(r"\bclass\s*=", text):
        hits.append("class=")
    if re.search(r"<g[^>]*\sopacity\s*=", lowered):
        hits.append("<g opacity>")
    return hits


# --- <style> + class resolution --------------------------------------------- #


def test_style_block_and_class_are_inlined() -> None:
    svg = (
        f'<svg xmlns="{_SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">'
        "<defs><style>"
        ".title{fill:#f7fbff;font-weight:800;font-size:46px}"
        ".sub{fill:#b8c7e6;font-size:24px}"
        "</style></defs>"
        '<text class="title" x="78" y="138">タイトル</text>'
        '<text class="sub" x="80" y="226">サブ</text>'
        "</svg>"
    ).encode()

    out = flatten_svg_for_ppt(svg)
    text = out.decode("utf-8")

    # No forbidden constructs remain.
    assert _forbidden_present(text) == []

    root = _parse(out)
    # <style> element removed entirely.
    assert _find_all(root, "style") == []

    title, sub = _find_all(root, "text")
    assert title.get("class") is None and sub.get("class") is None
    # Class rule properties became individual attributes.
    assert title.get("fill") == "#f7fbff"
    assert title.get("font-weight") == "800"
    # px unit stripped so the converter reads the size (else it resets to 16).
    assert title.get("font-size") == "46"
    assert sub.get("fill") == "#b8c7e6"
    assert sub.get("font-size") == "24"


def test_inline_style_attribute_overrides_class_and_beats_presentation_attr() -> None:
    svg = (
        f'<svg xmlns="{_SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">'
        "<style>.c{fill:#111111;font-size:20px}</style>"
        # element: presentation attr fill=#222 < class fill=#111 < style fill=#333
        '<text class="c" fill="#222222" style="fill:#333333">x</text>'
        "</svg>"
    ).encode()

    root = _parse(flatten_svg_for_ppt(svg))
    text = _find_all(root, "text")[0]
    assert text.get("fill") == "#333333"  # inline style wins
    assert text.get("font-size") == "20"  # from class, unit stripped
    assert text.get("class") is None
    assert text.get("style") is None


def test_multiple_classes_on_one_element_merge_in_stylesheet_order() -> None:
    svg = (
        f'<svg xmlns="{_SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">'
        "<style>"
        ".base{fill:#101010;font-weight:400}"
        ".hi{fill:#ffffff}"  # later rule overrides fill for equal specificity
        "</style>"
        '<text class="base hi">y</text>'
        "</svg>"
    ).encode()

    root = _parse(flatten_svg_for_ppt(svg))
    text = _find_all(root, "text")[0]
    assert text.get("fill") == "#ffffff"
    assert text.get("font-weight") == "400"


# --- <g opacity> folding ----------------------------------------------------- #


def test_group_opacity_is_folded_onto_children() -> None:
    svg = (
        f'<svg xmlns="{_SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">'
        '<g opacity="0.18">'
        '<path d="M0 0 L10 10" fill="#2e66aa"/>'
        '<rect x="0" y="0" width="4" height="4" fill="#fff" opacity="0.5"/>'
        "</g>"
        "</svg>"
    ).encode()

    out = flatten_svg_for_ppt(svg)
    assert _forbidden_present(out.decode("utf-8")) == []

    root = _parse(out)
    group = _find_all(root, "g")[0]
    assert "opacity" not in group.attrib  # lifted off the group

    path = _find_all(root, "path")[0]
    rect = _find_all(root, "rect")[0]
    assert path.get("opacity") == "0.18"
    # child with its own opacity multiplies: 0.18 * 0.5 = 0.09
    assert abs(_opacity(rect) - 0.09) < 1e-6


def test_nested_group_opacity_is_pushed_all_the_way_down() -> None:
    svg = (
        f'<svg xmlns="{_SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">'
        '<g opacity="0.5">'
        '<g opacity="0.4">'
        '<rect x="0" y="0" width="4" height="4" fill="#fff"/>'
        "</g>"
        "</g>"
        "</svg>"
    ).encode()

    out = flatten_svg_for_ppt(svg)
    assert _forbidden_present(out.decode("utf-8")) == []
    root = _parse(out)
    for g in _find_all(root, "g"):
        assert "opacity" not in g.attrib
    rect = _find_all(root, "rect")[0]
    # 0.5 * 0.4 = 0.20
    assert abs(_opacity(rect) - 0.20) < 1e-6


# --- preservation ------------------------------------------------------------ #


def test_gradient_stop_style_is_preserved() -> None:
    # The converter DOES read style= on <stop>; the flattener must not touch it.
    svg = (
        f'<svg xmlns="{_SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">'
        '<defs><linearGradient id="bg">'
        '<stop offset="0" style="stop-color:#07152f;stop-opacity:1"/>'
        '<stop offset="1" stop-color="#102b5c"/>'
        "</linearGradient></defs>"
        '<rect x="0" y="0" width="10" height="10" fill="url(#bg)"/>'
        "</svg>"
    ).encode()

    root = _parse(flatten_svg_for_ppt(svg))
    stops = _find_all(root, "stop")
    assert stops[0].get("style") == "stop-color:#07152f;stop-opacity:1"


def test_clean_svg_is_returned_unchanged() -> None:
    svg = (
        f'<svg xmlns="{_SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">'
        '<rect x="0" y="0" width="10" height="10" fill="#123456" />'
        '<text x="5" y="5" fill="#ffffff" font-size="24" '
        "font-family=\"'Noto Sans JP',Arial\">z</text>"
        "</svg>"
    ).encode()

    out = flatten_svg_for_ppt(svg)
    assert _forbidden_present(out.decode("utf-8")) == []
    root = _parse(out)
    assert _find_all(root, "text")[0].get("font-size") == "24"


def test_gt_style_attribute_is_not_the_forbidden_style_element() -> None:
    # Regression: an element carrying style= must not be mistaken for <style>.
    svg = (
        f'<svg xmlns="{_SVG_NS}" width="1280" height="720" viewBox="0 0 1280 720">'
        '<rect style="fill:#abcdef" x="0" y="0" width="4" height="4"/>'
        "</svg>"
    ).encode()
    root = _parse(flatten_svg_for_ppt(svg))
    rect = _find_all(root, "rect")[0]
    assert rect.get("fill") == "#abcdef"
    assert rect.get("style") is None
