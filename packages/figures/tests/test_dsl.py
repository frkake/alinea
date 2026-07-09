"""OverviewFigureDsl / OverviewFigureDslGenerated の検証(plans/07 §5.1)。"""

from __future__ import annotations

from typing import Any

import pytest
from _data import load_fixture
from alinea_figures.dsl import (
    OVERVIEW_FIGURE_DSL_JSON_SCHEMA,
    OverviewFigureDsl,
    OverviewFigureDslGenerated,
)
from pydantic import ValidationError


def _generated_payload() -> dict[str, Any]:
    fixture = load_fixture("overview_rectified_flow.json")
    dsl = fixture["dsl"]
    return {
        "layout": dsl["layout"],
        "cards": dsl["cards"],
        "connectors": dsl["connectors"],
        "evidence": ["blk-0001", "sec-2-2"],
    }


def test_valid_dsl_round_trips() -> None:
    fixture = load_fixture("overview_rectified_flow.json")
    dsl = OverviewFigureDsl.model_validate(fixture["dsl"])
    assert dsl.layout == "flow-3"
    assert [c.role for c in dsl.cards] == ["problem", "proposal", "result"]
    assert [c.tone for c in dsl.cards] == ["neutral", "accent", "green"]
    assert [(c.from_, c.to) for c in dsl.connectors] == [(0, 1), (1, 2)]


def test_generated_dsl_to_render_dsl_adds_footer_drops_evidence() -> None:
    generated = OverviewFigureDslGenerated.model_validate(_generated_payload())
    rendered = generated.to_render_dsl(generated_by="✦ AI 生成 · Alinea", date="2026-07-06")
    assert rendered.footer.generated_by == "✦ AI 生成 · Alinea"
    assert rendered.footer.date == "2026-07-06"
    assert not hasattr(rendered, "evidence")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["cards"].__setitem__(0, {**d["cards"][0], "role": "result"}),
        lambda d: d["cards"].__setitem__(1, {**d["cards"][1], "tone": "green"}),
        lambda d: d["connectors"].__setitem__(0, {"from": 0, "to": 2}),
    ],
)
def test_fixed_shape_violations_rejected(mutate: Any) -> None:
    fixture = load_fixture("overview_rectified_flow.json")
    data = dict(fixture["dsl"])
    data["cards"] = [dict(c) for c in data["cards"]]
    data["connectors"] = [dict(c) for c in data["connectors"]]
    mutate(data)
    with pytest.raises(ValidationError):
        OverviewFigureDsl.model_validate(data)


def test_footer_not_allowed_in_generated_schema() -> None:
    payload = _generated_payload()
    payload["footer"] = {"generated_by": "x", "date": "y"}
    with pytest.raises(ValidationError):
        OverviewFigureDslGenerated.model_validate(payload)


def test_evidence_pattern_enforced() -> None:
    payload = _generated_payload()
    payload["evidence"] = ["not-a-valid-id"]
    with pytest.raises(ValidationError):
        OverviewFigureDslGenerated.model_validate(payload)


def test_label_heading_body_max_length_enforced() -> None:
    fixture = load_fixture("overview_rectified_flow.json")
    data = dict(fixture["dsl"])
    data["cards"] = [dict(c) for c in data["cards"]]
    data["cards"][0]["label"] = "あ" * 25
    with pytest.raises(ValidationError):
        OverviewFigureDsl.model_validate(data)


def test_json_schema_shape_matches_spec() -> None:
    assert OVERVIEW_FIGURE_DSL_JSON_SCHEMA["properties"]["layout"] == {"const": "flow-3"}
    assert OVERVIEW_FIGURE_DSL_JSON_SCHEMA["properties"]["cards"]["minItems"] == 3
    assert OVERVIEW_FIGURE_DSL_JSON_SCHEMA["properties"]["cards"]["maxItems"] == 3
    assert OVERVIEW_FIGURE_DSL_JSON_SCHEMA["properties"]["evidence"]["maxItems"] == 4
