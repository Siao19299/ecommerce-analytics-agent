"""Semantic result comparison for Frozen benchmark evaluation cases."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.ecommerce_agent.evaluation_schema import ComparisonRules, NumericTolerance, RowComparison


@dataclass(frozen=True)
class ComparisonIssue:
    code: str
    message: str
    expected_row: int | None = None
    actual_row: int | None = None
    column: str | None = None


@dataclass(frozen=True)
class ResultComparison:
    columns_correct: bool
    row_count_correct: bool
    values_correct: bool
    order_applicable: bool
    order_correct: bool
    result_correct: bool
    expected_row_count: int
    actual_row_count: int
    issues: tuple[ComparisonIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _finite_number(value: object) -> float | None:
    if not _is_number(value):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _normalize_temporal(value: object) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return None


def _temporal_matches(expected: object, actual: object) -> bool | None:
    expected_temporal = _normalize_temporal(expected)
    actual_temporal = _normalize_temporal(actual)
    if expected_temporal is None and actual_temporal is None:
        return None
    if expected_temporal is None and isinstance(expected, str):
        expected_temporal = expected
    if actual_temporal is None and isinstance(actual, str):
        actual_temporal = actual
    if expected_temporal is None or actual_temporal is None:
        return False
    return expected_temporal == actual_temporal


def _value_matches(
    expected: object,
    actual: object,
    tolerance: NumericTolerance,
) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if _is_number(expected) or _is_number(actual):
        expected_number = _finite_number(expected)
        actual_number = _finite_number(actual)
        if expected_number is None or actual_number is None:
            return False
        return math.isclose(
            expected_number,
            actual_number,
            rel_tol=tolerance.relative,
            abs_tol=tolerance.absolute,
        )
    temporal = _temporal_matches(expected, actual)
    if temporal is not None:
        return temporal
    return type(expected) is type(actual) and expected == actual


def _row_matches(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    rules: ComparisonRules,
) -> bool:
    for column in rules.expected_columns:
        if column not in expected or column not in actual:
            return False
        tolerance = rules.numeric_tolerance_by_column.get(
            column,
            rules.numeric_tolerance,
        )
        if not _value_matches(expected[column], actual[column], tolerance):
            return False
    return True


def _maximum_row_matching(
    expected_rows: Sequence[Mapping[str, object]],
    actual_rows: Sequence[Mapping[str, object]],
    rules: ComparisonRules,
) -> tuple[bool, tuple[int | None, ...]]:
    """Return multiset equality using bipartite matching, preserving duplicates."""
    edges = [
        [
            actual_index
            for actual_index, actual in enumerate(actual_rows)
            if _row_matches(expected, actual, rules)
        ]
        for expected in expected_rows
    ]
    actual_to_expected: dict[int, int] = {}

    def augment(expected_index: int, visited: set[int]) -> bool:
        for actual_index in edges[expected_index]:
            if actual_index in visited:
                continue
            visited.add(actual_index)
            previous = actual_to_expected.get(actual_index)
            if previous is None or augment(previous, visited):
                actual_to_expected[actual_index] = expected_index
                return True
        return False

    matched = 0
    for expected_index in range(len(expected_rows)):
        matched += int(augment(expected_index, set()))
    expected_to_actual: list[int | None] = [None] * len(expected_rows)
    for actual_index, expected_index in actual_to_expected.items():
        expected_to_actual[expected_index] = actual_index
    complete = matched == len(expected_rows) == len(actual_rows)
    return complete, tuple(expected_to_actual)


def compare_results(
    *,
    expected_columns: Sequence[str],
    expected_rows: Sequence[Mapping[str, object]],
    actual_columns: Sequence[str],
    actual_rows: Sequence[Mapping[str, object]],
    rules: ComparisonRules,
) -> ResultComparison:
    """Compare execution results without depending on candidate SQL text."""
    expected_column_set = set(expected_columns)
    actual_column_set = set(actual_columns)
    columns_correct = (
        expected_column_set <= actual_column_set
        if rules.allow_extra_columns
        else expected_column_set == actual_column_set
    ) and expected_column_set == set(rules.expected_columns)
    row_count_correct = len(expected_rows) == len(actual_rows)
    issues: list[ComparisonIssue] = []
    if not columns_correct:
        issues.append(
            ComparisonIssue(
                code="column_mismatch",
                message=(
                    f"expected columns {sorted(expected_column_set)}, "
                    f"actual columns {sorted(actual_column_set)}"
                ),
            )
        )
    if not row_count_correct:
        issues.append(
            ComparisonIssue(
                code="row_count_mismatch",
                message=f"expected {len(expected_rows)} rows, actual {len(actual_rows)}",
            )
        )

    multiset_correct, matching = _maximum_row_matching(
        expected_rows,
        actual_rows,
        rules,
    )
    values_correct = row_count_correct and multiset_correct
    if not values_correct:
        for expected_index, actual_index in enumerate(matching):
            if actual_index is None:
                issues.append(
                    ComparisonIssue(
                        code="unmatched_expected_row",
                        message="no actual row matched this expected row within tolerance",
                        expected_row=expected_index,
                    )
                )
        if len(issues) < 3:
            issues.append(
                ComparisonIssue(
                    code="row_value_mismatch",
                    message="row multiset differs after NULL, date, and tolerance comparison",
                )
            )

    order_applicable = rules.row_comparison is RowComparison.ORDERED
    if order_applicable:
        order_correct = row_count_correct and all(
            _row_matches(expected, actual, rules)
            for expected, actual in zip(expected_rows, actual_rows)
        )
        if values_correct and not order_correct:
            issues.append(
                ComparisonIssue(
                    code="row_order_mismatch",
                    message="row values match as a multiset but violate required order",
                )
            )
    else:
        order_correct = True

    result_correct = (
        columns_correct
        and row_count_correct
        and values_correct
        and order_correct
    )
    return ResultComparison(
        columns_correct=columns_correct,
        row_count_correct=row_count_correct,
        values_correct=values_correct,
        order_applicable=order_applicable,
        order_correct=order_correct,
        result_correct=result_correct,
        expected_row_count=len(expected_rows),
        actual_row_count=len(actual_rows),
        issues=tuple(issues),
    )
