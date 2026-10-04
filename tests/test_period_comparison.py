"""Assistant-authored mechanical tests for Deterministic analysis comparisons."""

from datetime import date

import pytest
from pydantic import ValidationError

from src.ecommerce_agent.period_comparison import (
    CalculationStatus,
    ComparabilityStatus,
    ComparisonRequest,
    ComparisonType,
    SignTransition,
    calculate_period_comparison,
)
from src.ecommerce_agent.analysis_models import (
    CalculationLineage,
    MetricProvenance,
    MonthlyObservation,
)


def _metric():
    return MetricProvenance(
        metric_id="delivered_gmv",
        metric_definition="已送达订单商品成交金额，不含运费",
        time_field="order_purchase_timestamp",
    )


def _lineage():
    return CalculationLineage(
        parent_run_id="query-example",
        source_sql_attempt=1,
        input_reference="query_result.rows",
    )


def _request(kind, target, rows):
    return ComparisonRequest(
        analysis_type=kind,
        target_period=target,
        metric=_metric(),
        observations=rows,
        lineage=_lineage(),
    )


def _row(period, value, completeness="complete", reason=None):
    return MonthlyObservation(
        period=period,
        value=value,
        completeness=completeness,
        completeness_reason=reason,
    )


def test_normal_yoy_uses_same_calendar_month():
    result = calculate_period_comparison(
        _request(
            ComparisonType.YOY,
            date(2024, 1, 1),
            (
                _row(date(2023, 1, 1), 100),
                _row(date(2024, 1, 1), 90),
            ),
        )
    )

    assert result.comparison_period == date(2023, 1, 1)
    assert result.absolute_change == -10
    assert result.relative_change == pytest.approx(-0.1)
    assert result.calculation_status is CalculationStatus.COMPUTED
    assert result.comparability is ComparabilityStatus.COMPARABLE


def test_normal_mom_requires_previous_calendar_month_not_previous_row():
    result = calculate_period_comparison(
        _request(
            ComparisonType.MOM,
            date(2024, 3, 1),
            (
                _row(date(2024, 1, 1), 100),
                _row(date(2024, 3, 1), 120),
            ),
        )
    )

    assert result.comparison_period == date(2024, 2, 1)
    assert result.calculation_status is (
        CalculationStatus.MISSING_COMPARISON_PERIOD
    )
    assert result.absolute_change is None
    assert result.relative_change is None
    assert "missing_period_is_not_imputed_as_zero" in result.boundary_notes


def test_incomplete_current_keeps_arithmetic_but_blocks_standard_comparison():
    result = calculate_period_comparison(
        _request(
            ComparisonType.YOY,
            date(2024, 2, 1),
            (
                _row(date(2023, 2, 1), 120),
                _row(
                    date(2024, 2, 1),
                    60,
                    "incomplete",
                    "data_cutoff_before_month_end",
                ),
            ),
        )
    )

    assert result.absolute_change == -60
    assert result.relative_change == pytest.approx(-0.5)
    assert result.calculation_status is CalculationStatus.COMPUTED
    assert result.comparability is ComparabilityStatus.NOT_COMPARABLE
    assert result.comparability_reasons == ("current_period_incomplete",)


def test_zero_baseline_returns_absolute_change_without_relative_change():
    result = calculate_period_comparison(
        _request(
            ComparisonType.MOM,
            date(2024, 4, 1),
            (
                _row(date(2024, 3, 1), 0),
                _row(date(2024, 4, 1), 40),
            ),
        )
    )

    assert result.absolute_change == 40
    assert result.relative_change is None
    assert result.calculation_status is CalculationStatus.ZERO_BASELINE


@pytest.mark.parametrize(
    ("baseline", "current", "transition"),
    [
        (-100, -50, SignTransition.NONE),
        (-100, 20, SignTransition.NEGATIVE_TO_ZERO_OR_POSITIVE),
        (100, -20, SignTransition.ZERO_OR_POSITIVE_TO_NEGATIVE),
    ],
)
def test_negative_values_do_not_use_positive_scale_growth_language(
    baseline,
    current,
    transition,
):
    result = calculate_period_comparison(
        _request(
            ComparisonType.MOM,
            date(2024, 2, 1),
            (
                _row(date(2024, 1, 1), baseline),
                _row(date(2024, 2, 1), current),
            ),
        )
    )

    assert result.absolute_change == current - baseline
    if baseline < 0:
        assert result.calculation_status is CalculationStatus.NEGATIVE_BASELINE
        assert result.relative_change is None
    assert result.sign_transition is transition


def test_duplicate_unsorted_and_invalid_month_inputs_are_controlled():
    with pytest.raises(ValidationError, match="重复期间"):
        _request(
            ComparisonType.MOM,
            date(2024, 2, 1),
            (
                _row(date(2024, 1, 1), 10),
                _row(date(2024, 1, 1), 20),
            ),
        )
    with pytest.raises(ValidationError, match="升序"):
        _request(
            ComparisonType.MOM,
            date(2024, 2, 1),
            (
                _row(date(2024, 2, 1), 20),
                _row(date(2024, 1, 1), 10),
            ),
        )
    with pytest.raises(ValidationError):
        _row(date(2024, 1, 15), 10)


def test_incomplete_period_requires_a_reason_and_boolean_is_not_numeric():
    with pytest.raises(ValidationError, match="completeness_reason"):
        _row(date(2024, 1, 1), 10, "incomplete")
    with pytest.raises(ValidationError):
        _row(date(2024, 1, 1), True)


def test_result_contains_metric_lineage_raw_input_method_and_chart_data():
    result = calculate_period_comparison(
        _request(
            ComparisonType.MOM,
            date(2024, 2, 1),
            (
                _row(date(2024, 1, 1), 10),
                _row(date(2024, 2, 1), 15),
            ),
        )
    )
    payload = result.model_dump(mode="json")

    assert payload["metric"]["definition_source"] == (
        "data/metadata/metric_dictionary.csv"
    )
    assert payload["lineage"]["parent_run_id"] == "query-example"
    assert len(payload["raw_input"]) == 2
    assert payload["method"]["period_match"] == "previous_calendar_month"
    assert [point["role"] for point in payload["chart_data"]] == [
        "comparison",
        "current",
    ]
