"""Shared assertions for the structured paper-summary test contract."""

from __future__ import annotations

from typing import Any

CANONICAL_SUMMARY_LABELS = ("課題", "提案", "仕組み", "検証", "結果", "限界")
REQUIRED_SUMMARY_LABELS = frozenset({"課題", "提案", "検証", "結果"})


def assert_summary_lines_contract(lines: list[Any] | None) -> None:
    """Assert the persisted 4-6 item, ordered, labeled summary contract."""
    assert lines is not None
    assert 4 <= len(lines) <= 6

    labels: list[str] = []
    for line in lines:
        assert isinstance(line, str)
        label, separator, body = line.partition(": ")
        assert separator == ": "
        assert body and body == body.strip()
        assert "\n" not in body and "\r" not in body
        labels.append(label)

    label_set = set(labels)
    assert len(labels) == len(label_set)
    assert REQUIRED_SUMMARY_LABELS <= label_set
    assert label_set <= set(CANONICAL_SUMMARY_LABELS)
    assert labels == [label for label in CANONICAL_SUMMARY_LABELS if label in label_set]


__all__ = ["assert_summary_lines_contract"]
