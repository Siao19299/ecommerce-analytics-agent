"""Assistant-authored tests for Day 10 adapters and calculation trace."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.ecommerce_agent.day10_adapter import (
    Day10InputError,
    Day10InputErrorCode,
    load_metric_provenance,
    monthly_observations_from_query_result,
)
from src.ecommerce_agent.day10_comparison import (
    ComparisonRequest,
    ComparisonType,
    calculate_period_comparison,
)
from src.ecommerce_agent.day10_models import CalculationLineage
from src.ecommerce_agent.day10_trace import (
    build_calculation_trace,
    write_calculation_trace,
)
from src.ecommerce_agent.sql_generation import (
    QueryExecutionErrorType,
    QueryExecutionResult,
)


ROOT = Path(__file__).parents[1]


def _success(rows, *, truncated=False):
    return QueryExecutionResult(
        columns=("purchase_month", "monthly_gmv"),
        rows=tuple(rows),
        execution_started=True,
        rows_truncated=truncated,
    )


def test_adapter_preserves_explicit_boundary_metadata_without_filling_gaps():
    observations = monthly_observations_from_query_result(
        _success(
            [
                {"purchase_month": "2024-01-01", "monthly_gmv": 10},
                {"purchase_month": "2024-03-01", "monthly_gmv": 30},
            ]
        ),
        period_column="purchase_month",
        value_column="monthly_gmv",
        incomplete_period_reasons={
            date(2024, 3, 1): "metric_observed_boundary_month"
        },
    )

    assert [item.period for item in observations] == [
        date(2024, 1, 1),
        date(2024, 3, 1),
    ]
    assert observations[1].completeness.value == "incomplete"
    assert all(item.period != date(2024, 2, 1) for item in observations)


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (
            QueryExecutionResult(
                error_type=QueryExecutionErrorType.DATABASE,
                error_message="synthetic",
            ),
            Day10InputErrorCode.EXECUTION_FAILED,
        ),
        (
            _success([], truncated=True),
            Day10InputErrorCode.TRUNCATED_RESULT,
        ),
        (
            QueryExecutionResult(columns=("purchase_month",), rows=()),
            Day10InputErrorCode.MISSING_COLUMN,
        ),
    ],
)
def test_failed_truncated_or_missing_column_result_is_rejected(result, code):
    with pytest.raises(Day10InputError) as captured:
        monthly_observations_from_query_result(
            result,
            period_column="purchase_month",
            value_column="monthly_gmv",
            incomplete_period_reasons={},
        )
    assert captured.value.code is code


def test_metric_provenance_comes_from_unique_dictionary_source():
    metric = load_metric_provenance(ROOT, "delivered_gmv")

    assert metric.metric_id == "delivered_gmv"
    assert "不包含运费" in metric.metric_definition
    assert metric.time_field == "order_purchase_timestamp"


def test_trace_has_stable_input_hash_and_day9_parent(tmp_path):
    metric = load_metric_provenance(ROOT, "delivered_gmv")
    observations = monthly_observations_from_query_result(
        _success(
            [
                {"purchase_month": "2024-01-01", "monthly_gmv": 10},
                {"purchase_month": "2024-02-01", "monthly_gmv": 15},
            ]
        ),
        period_column="purchase_month",
        value_column="monthly_gmv",
        incomplete_period_reasons={},
    )
    result = calculate_period_comparison(
        ComparisonRequest(
            analysis_type=ComparisonType.MOM,
            target_period=date(2024, 2, 1),
            metric=metric,
            observations=observations,
            lineage=CalculationLineage(
                parent_run_id="day09-parent",
                source_sql_attempt=2,
                input_reference="trace.attempts[1].result_summary.rows",
            ),
        )
    )
    timestamp = datetime(2026, 9, 16, tzinfo=timezone.utc)
    first = build_calculation_trace(
        calculation_id="calc-1",
        step="deterministic_comparison",
        result=result,
        created_at=timestamp,
    )
    second = build_calculation_trace(
        calculation_id="calc-2",
        step="deterministic_comparison",
        result=result,
        created_at=timestamp,
    )
    path = tmp_path / "calculation.json"
    write_calculation_trace(path, first)

    assert first.parent_run_id == "day09-parent"
    assert first.source_sql_attempt == 2
    assert first.input_sha256 == second.input_sha256
    assert "day09-parent" in path.read_text(encoding="utf-8")
