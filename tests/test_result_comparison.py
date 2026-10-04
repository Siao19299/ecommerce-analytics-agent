from datetime import date, datetime, timezone
from decimal import Decimal

from src.ecommerce_agent.result_comparison import compare_results
from src.ecommerce_agent.evaluation_schema import (
    ComparisonRules,
    NumericTolerance,
    OrderKey,
    RowComparison,
    SortDirection,
)


def _rules(row_comparison=RowComparison.ORDERED):
    return ComparisonRules(
        expected_columns=("period", "value", "note"),
        row_comparison=row_comparison,
        order_keys=(
            (OrderKey(column="period", direction=SortDirection.ASCENDING),)
            if row_comparison is RowComparison.ORDERED
            else ()
        ),
        numeric_tolerance=NumericTolerance(absolute=0, relative=0),
        numeric_tolerance_by_column={
            "value": NumericTolerance(absolute=1e-6, relative=1e-9)
        },
    )


def test_numeric_date_and_null_comparison_is_type_aware():
    result = compare_results(
        expected_columns=("period", "value", "note"),
        expected_rows=[{"period": "2018-07-01", "value": 1.0, "note": None}],
        actual_columns=("note", "value", "period"),
        actual_rows=[
            {"period": date(2018, 7, 1), "value": Decimal("1.0000004"), "note": None}
        ],
        rules=_rules(),
    )
    assert result.result_correct is True
    assert result.columns_correct is True


def test_null_is_not_zero_and_boolean_is_not_one():
    null_result = compare_results(
        expected_columns=("period", "value", "note"),
        expected_rows=[{"period": "2018-07-01", "value": 1, "note": None}],
        actual_columns=("period", "value", "note"),
        actual_rows=[{"period": "2018-07-01", "value": 1, "note": 0}],
        rules=_rules(),
    )
    bool_result = compare_results(
        expected_columns=("period", "value", "note"),
        expected_rows=[{"period": "2018-07-01", "value": 1, "note": "x"}],
        actual_columns=("period", "value", "note"),
        actual_rows=[{"period": "2018-07-01", "value": True, "note": "x"}],
        rules=_rules(),
    )
    assert null_result.result_correct is False
    assert bool_result.result_correct is False


def test_missing_row_key_is_not_treated_as_json_null():
    result = compare_results(
        expected_columns=("period", "value", "note"),
        expected_rows=[{"period": "2018-07-01", "value": 1, "note": None}],
        actual_columns=("period", "value", "note"),
        actual_rows=[{"period": "2018-07-01", "value": 1}],
        rules=_rules(),
    )
    assert result.values_correct is False
    assert result.result_correct is False


def test_ordered_comparison_separates_values_from_order():
    expected = [
        {"period": "2018-06-01", "value": 1, "note": None},
        {"period": "2018-07-01", "value": 2, "note": None},
    ]
    result = compare_results(
        expected_columns=("period", "value", "note"),
        expected_rows=expected,
        actual_columns=("period", "value", "note"),
        actual_rows=list(reversed(expected)),
        rules=_rules(),
    )
    assert result.values_correct is True
    assert result.order_correct is False
    assert result.result_correct is False
    assert any(issue.code == "row_order_mismatch" for issue in result.issues)


def test_unordered_multiset_preserves_duplicate_counts():
    rules = _rules(RowComparison.UNORDERED_MULTISET)
    expected = [
        {"period": "2018-07-01", "value": 1, "note": None},
        {"period": "2018-07-01", "value": 1, "note": None},
    ]
    actual = [
        {"period": "2018-07-01", "value": 1, "note": None},
        {"period": "2018-07-01", "value": 2, "note": None},
    ]
    result = compare_results(
        expected_columns=("period", "value", "note"),
        expected_rows=expected,
        actual_columns=("period", "value", "note"),
        actual_rows=actual,
        rules=rules,
    )
    assert result.values_correct is False
    assert result.result_correct is False


def test_aware_datetime_is_normalized_to_utc_iso_8601():
    result = compare_results(
        expected_columns=("period", "value", "note"),
        expected_rows=[{"period": "2018-07-01T00:00:00Z", "value": 1, "note": None}],
        actual_columns=("period", "value", "note"),
        actual_rows=[
            {
                "period": datetime(2018, 7, 1, tzinfo=timezone.utc),
                "value": 1,
                "note": None,
            }
        ],
        rules=_rules(),
    )
    assert result.result_correct is True
