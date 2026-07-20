"""Structured-output schemas for the grounded slide plan (Task 29, Step 3).

The planning LLM returns a :class:`SlidePlanDocument` (a list of
:class:`SlidePlan` items). These pydantic models are also compiled to a JSON
Schema (:data:`SLIDE_PLAN_SCHEMA_SPEC`) so the router can request native
structured output. The schema is a *fixed contract*: untrusted paper body and
the optional user instruction can influence phrasing only, never the shape of
this output.

Validation of a returned plan against the source packet (nonexistent anchors,
duplicate figures, ungrounded numbers) lives in
:mod:`alinea_worker.presentation.runner`; this module only defines the shape.
"""

from __future__ import annotations

from typing import Literal

from alinea_llm.types import JsonSchemaSpec
from pydantic import BaseModel, Field

LayoutIntent = Literal["title", "content", "comparison", "figure", "summary"]

#: Per-preset slide-count band (min, max). Matches the design doc (§ユーザー体験).
PRESET_SLIDE_RANGE: dict[str, tuple[int, int]] = {
    "reading_group": (10, 14),
    "research_talk": (12, 18),
    "implementation": (10, 16),
}

SLIDE_PLAN_SCHEMA_NAME = "presentation_slide_plan_v1"


class SlidePlan(BaseModel):
    """One planned slide: its claims and the paper evidence that grounds them."""

    index: int
    title: str
    claims: list[str] = Field(default_factory=list)
    evidence_anchors: list[str] = Field(default_factory=list)
    figure_ids: list[str] = Field(default_factory=list)
    speaker_notes: str = ""
    layout: LayoutIntent


class SlidePlanDocument(BaseModel):
    """The full deck plan returned by the first LLM call."""

    slides: list[SlidePlan] = Field(default_factory=list)


# The JSON Schema handed to the provider. Kept explicit (rather than generated
# from the model) so it is a stable, reviewable contract and forbids extra keys.
SLIDE_PLAN_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["slides"],
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "index",
                    "title",
                    "claims",
                    "evidence_anchors",
                    "figure_ids",
                    "speaker_notes",
                    "layout",
                ],
                "properties": {
                    "index": {"type": "integer"},
                    "title": {"type": "string"},
                    "claims": {"type": "array", "items": {"type": "string"}},
                    "evidence_anchors": {"type": "array", "items": {"type": "string"}},
                    "figure_ids": {"type": "array", "items": {"type": "string"}},
                    "speaker_notes": {"type": "string"},
                    "layout": {
                        "type": "string",
                        "enum": ["title", "content", "comparison", "figure", "summary"],
                    },
                },
            },
        }
    },
}

SLIDE_PLAN_SCHEMA_SPEC = JsonSchemaSpec(
    name=SLIDE_PLAN_SCHEMA_NAME, json_schema=SLIDE_PLAN_JSON_SCHEMA
)


class SlideSvg(BaseModel):
    """A single generated SVG page prior to sanitization/export."""

    index: int
    filename: str
    svg: str


SLIDE_SVG_SCHEMA_NAME = "presentation_slide_svg_v1"

SLIDE_SVG_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["svg"],
    "properties": {"svg": {"type": "string"}},
}

SLIDE_SVG_SCHEMA_SPEC = JsonSchemaSpec(
    name=SLIDE_SVG_SCHEMA_NAME, json_schema=SLIDE_SVG_JSON_SCHEMA
)


__all__ = [
    "PRESET_SLIDE_RANGE",
    "SLIDE_PLAN_JSON_SCHEMA",
    "SLIDE_PLAN_SCHEMA_NAME",
    "SLIDE_PLAN_SCHEMA_SPEC",
    "SLIDE_SVG_JSON_SCHEMA",
    "SLIDE_SVG_SCHEMA_NAME",
    "SLIDE_SVG_SCHEMA_SPEC",
    "LayoutIntent",
    "SlidePlan",
    "SlidePlanDocument",
    "SlideSvg",
]
