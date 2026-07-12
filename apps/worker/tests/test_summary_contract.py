"""Contract tests for the structured paper-summary assertions."""

from __future__ import annotations

from typing import Any

import pytest
from _summary_contract import assert_summary_lines_contract


@pytest.mark.parametrize(
    "lines",
    [
        ["課題: p", "提案: q", "検証: v", "結果: r"],
        ["課題: p", "提案: q", "検証: v", "結果: r", "限界: l"],
        ["課題: p", "提案: q", "仕組み: m", "検証: v", "結果: r", "限界: l"],
    ],
    ids=["required-four", "optional-limit", "all-six"],
)
def test_summary_lines_contract_accepts_valid_optional_labels(lines: list[str]) -> None:
    assert_summary_lines_contract(lines)


@pytest.mark.parametrize(
    "lines",
    [
        ["課題: p", "提案: q", "結果: r"],
        ["課題: p", "提案: q", "仕組み: m", "検証: v", "結果: r", "限界: l", "補足: x"],
        ["課題: p", "提案: q", "検証: v", "結果:   "],
        ["課題： p", "提案: q", "検証: v", "結果: r"],  # noqa: RUF001
        ["課題: p", "提案: q", "手法: m", "検証: v", "結果: r"],
        ["課題: p", "提案: q", "課題: duplicate", "検証: v", "結果: r"],
        ["課題: p", "提案: q", "仕組み: m", "結果: r", "限界: l"],
        ["課題: p", "提案: q", "検証: v", "仕組み: m", "結果: r"],
    ],
    ids=[
        "too-few",
        "too-many",
        "whitespace-body",
        "wrong-separator",
        "noncanonical-label",
        "duplicate-label",
        "missing-required-label",
        "noncanonical-order",
    ],
)
def test_summary_lines_contract_rejects_invalid_shapes(lines: list[Any]) -> None:
    with pytest.raises(AssertionError):
        assert_summary_lines_contract(lines)
