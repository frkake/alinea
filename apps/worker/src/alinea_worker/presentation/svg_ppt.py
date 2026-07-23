"""Flatten sanitized SVG into the *inline* form ppt-master requires.

The security sanitizer (:func:`alinea_worker.figure_assets.sanitize_svg_document`)
deliberately *preserves* ``<style>`` blocks, ``class`` attributes and
``<g opacity>`` — they are safe for a browser/renderer and legitimate for
ingested figures. ppt-master, however, is not a browser:

* Its quality gate (``svg_quality_checker.py``) hard-*errors* on a ``<style>``
  element, a ``class=`` attribute, and ``<g ... opacity=>`` (see
  ``_check_forbidden_elements``). Any one of these fails the gate and aborts the
  whole PPTX export.
* Its native converter (``svg_to_pptx``) resolves every style property through
  ``_get_attr`` — the element's own presentation attribute, then the value
  inherited from an ancestor ``<g>``. It never parses a ``style="..."`` string
  for text/shapes (only gradient ``<stop>`` colors). So a CSS ``class`` never
  reaches DrawingML: class-styled text would render *unstyled* (black, 16 px)
  even if the gate were bypassed.

LLMs, prompted to "draw an SVG", naturally emit a ``<style>`` block with class
selectors and group opacity — exactly the three forbidden constructs. Rather
than hope a prompt suppresses them, this module deterministically rewrites the
SVG so the visual result is preserved but expressed the way the converter reads
it:

* class rules and inline ``style="..."`` declarations are resolved into
  individual presentation attributes (``fill=``, ``font-size=`` …),
* ``font-size`` values are stripped of ``px``/``pt`` units (the converter's
  ``_f`` treats a unit-suffixed size as invalid and falls back to 16 px),
* ``<style>`` elements and ``class`` attributes are removed,
* ``<g opacity="X">`` is folded onto its children (multiplying into any child
  opacity), which reproduces the converter's own group-opacity inheritance
  while satisfying the gate.

Input MUST already be sanitized: this function trusts that values are safe and
only re-expresses them. It never introduces new elements or references.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import cast

import structlog

_LOGGER = structlog.get_logger("alinea_worker.presentation.svg_ppt")

_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"

ET.register_namespace("", _SVG_NAMESPACE)
ET.register_namespace("xlink", _XLINK_NAMESPACE)

# Presentation properties the ppt-master converter honours as individual SVG
# attributes (and inherits from <g>). Only these are lifted out of CSS; anything
# else in a class/inline style is dropped (it would not survive conversion
# anyway). Kept in sync with svg_to_pptx INHERITABLE_ATTRS + the run-level text
# attributes convert_text reads.
_PPT_INLINE_PROPS = frozenset(
    {
        "fill",
        "fill-opacity",
        "stroke",
        "stroke-width",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "opacity",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "text-anchor",
        "text-decoration",
        "letter-spacing",
        "word-spacing",
    }
)

# font-size / letter-spacing may carry a length unit in CSS; the converter wants
# a bare number (px). Strip px/pt so the size is not silently reset to 16 px.
_LENGTH_UNIT_RE = re.compile(r"^\s*(-?\d*\.?\d+)\s*(?:px|pt)\s*$", re.IGNORECASE)
_SIMPLE_CLASS_SELECTOR_RE = re.compile(r"\.([A-Za-z_-][A-Za-z0-9_-]*)\Z")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _strip_length_unit(value: str) -> str:
    match = _LENGTH_UNIT_RE.match(value)
    return match.group(1) if match is not None else value.strip()


def _parse_declarations(text: str) -> dict[str, str]:
    """Parse ``prop:val;prop:val`` into a map limited to inline-able properties."""

    out: dict[str, str] = {}
    for declaration in text.split(";"):
        clean = declaration.strip()
        if not clean or ":" not in clean:
            continue
        raw_name, raw_value = clean.split(":", 1)
        name = raw_name.strip().lower()
        value = raw_value.strip()
        if name in _PPT_INLINE_PROPS and value:
            out[name] = value
    return out


def _parse_class_rules(css_text: str) -> tuple[list[tuple[frozenset[str], dict[str, str]]], int]:
    """Parse a sanitized stylesheet into ordered ``(class-names, decls)`` rules.

    Only *simple class* selectors (``.foo`` and comma groups of them) are
    resolved — that is exactly what the authoring model emits. Complex selectors
    (descendant, child, element, id) are counted and skipped: their ``class``
    attributes are still removed downstream so the gate passes, but their styling
    is not lifted. The returned list preserves stylesheet order so a later rule
    overrides an earlier one for the same property (CSS cascade among equal
    specificity).
    """

    rules: list[tuple[frozenset[str], dict[str, str]]] = []
    skipped = 0
    position = 0
    length = len(css_text)
    while position < length:
        open_brace = css_text.find("{", position)
        if open_brace < 0:
            break
        close_brace = css_text.find("}", open_brace + 1)
        if close_brace < 0:
            break
        selector_text = css_text[position:open_brace]
        declarations = _parse_declarations(css_text[open_brace + 1 : close_brace])
        position = close_brace + 1
        if not declarations:
            continue
        for selector in selector_text.split(","):
            match = _SIMPLE_CLASS_SELECTOR_RE.fullmatch(selector.strip())
            if match is None:
                skipped += 1
                continue
            rules.append((frozenset({match.group(1)}), declarations))
    return rules, skipped


def _collect_and_remove_style_elements(root: ET.Element[str]) -> str:
    """Remove every ``<style>`` element and return its concatenated text."""

    parents = {child: parent for parent in root.iter() for child in parent}
    css_parts: list[str] = []
    for element in list(root.iter()):
        if _local_name(str(element.tag)) != "style":
            continue
        css_parts.append("".join(element.itertext()))
        parent = parents.get(element)
        if parent is not None:
            parent.remove(element)
    return "".join(css_parts)


def _resolve_effective_style(
    element: ET.Element[str],
    class_rules: list[tuple[frozenset[str], dict[str, str]]],
) -> dict[str, str]:
    """Compute the effective inline-able properties for one element.

    Cascade (low → high precedence): existing presentation attributes < matching
    class rules (stylesheet order) < the element's own ``style=""`` declarations.
    """

    class_attr = element.get("class")
    classes = frozenset(class_attr.split()) if class_attr else frozenset()

    final: dict[str, str] = {}
    for prop in _PPT_INLINE_PROPS:
        value = element.get(prop)
        if value is not None:
            final[prop] = value
    if classes:
        for names, declarations in class_rules:
            if names & classes:
                final.update(declarations)
    style_attr = element.get("style")
    if style_attr:
        final.update(_parse_declarations(style_attr))
    return final


def _inline_styles(
    root: ET.Element[str],
    class_rules: list[tuple[frozenset[str], dict[str, str]]],
) -> None:
    for element in root.iter():
        name = _local_name(str(element.tag))
        # Gradient <stop> styling IS read from style= by the converter; leave it
        # untouched so gradients keep their colors.
        if name == "stop":
            continue
        has_class = element.get("class") is not None
        has_style = element.get("style") is not None
        if not has_class and not has_style:
            continue
        effective = _resolve_effective_style(element, class_rules)
        for prop, value in effective.items():
            if prop in {"font-size", "letter-spacing", "word-spacing"}:
                value = _strip_length_unit(value)
            element.set(prop, value)
        if has_class:
            del element.attrib["class"]
        if has_style:
            del element.attrib["style"]


def _parse_opacity(value: str | None) -> float:
    if value is None:
        return 1.0
    try:
        parsed = float(value.strip())
    except (TypeError, ValueError):
        return 1.0
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _format_opacity(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _fold_group_opacity(element: ET.Element[str]) -> None:
    """Push ``<g opacity>`` onto children (multiplying), top-down.

    Processing a node *before* recursing means opacity handed to a child ``<g>``
    is pushed further down on the next step, so no ``<g opacity>`` survives. This
    matches the converter's own multiplicative group-opacity inheritance while
    satisfying the gate (leaf ``opacity`` is allowed; only ``<g opacity>`` and
    ``<image opacity>`` are forbidden).
    """

    if _local_name(str(element.tag)) == "g" and "opacity" in element.attrib:
        group_opacity = _parse_opacity(element.attrib.get("opacity"))
        del element.attrib["opacity"]
        if group_opacity < 1.0:
            for child in element:
                combined = group_opacity * _parse_opacity(child.get("opacity"))
                child.set("opacity", _format_opacity(combined))
    for child in element:
        _fold_group_opacity(child)


def flatten_svg_for_ppt(data: bytes) -> bytes:
    """Rewrite a sanitized SVG into ppt-master's inline-attribute form.

    Returns re-serialized bytes with no ``<style>`` element, no ``class``
    attribute, and no ``<g opacity>`` — every visual property expressed as an
    individual presentation attribute the converter reads. Assumes ``data`` is
    already sanitized; it is byte-for-byte returned unchanged when it contains
    none of the three constructs and parses cleanly.
    """

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # A well-formed sanitized document is the contract; if parsing fails we
        # cannot safely rewrite, so hand the bytes back untouched and let the
        # downstream gate surface the problem.
        _LOGGER.warning("svg_ppt.parse_failed")
        return data

    css_text = _collect_and_remove_style_elements(root)
    class_rules, skipped = _parse_class_rules(css_text)
    if skipped:
        _LOGGER.info("svg_ppt.skipped_complex_selectors", count=skipped)
    _inline_styles(root, class_rules)
    _fold_group_opacity(root)
    return cast(bytes, ET.tostring(root, encoding="utf-8", short_empty_elements=True))


__all__ = ["flatten_svg_for_ppt"]
