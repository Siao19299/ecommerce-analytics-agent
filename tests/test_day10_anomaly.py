"""Assistant-authored mechanical tests for Day 10 anomaly detection."""

from datetime import date

import pytest
from pydantic import ValidationError

from src.ecommerce_agent.day10_anomaly import (
    AnomalyRequest,
    AnomalyStatus,
    detect_monthly_anomaly,
)
from src.ecommerce_agent.day10_models import (
    CalculationLineage,
    MetricProvenance,
    MonthlyObservation,
)


def _month(number, value, completeness="complete", reason=None):
    return MonthlyObservation(
        period=date(2024, number, 1),
        value=value,
        completeness=completeness,
        completeness_reason=reason,
    )


def _request(rows, target=6, **changes):
    payload = {
        "metric": MetricProvenance(
            metric_id="delivered_gmv",
            metric_definition="已送达订单商品成交金额，不含运费",
            time_field="order_purchase_timestamp",
        ),
        "target_period": date(2024, target, 1),
        "observations": rows,
        "lineage": CalculationLineage(
            parent_run_id="day09-anomaly",
            source_sql_attempt=1,
            input_reference="query_result.rows",
        ),
        "history_window": 5,
        "minimum_history": 5,
        "threshold": 3.5,
    }
    payload.update(changes)
    return AnomalyRequest(**payload)


def test_clear_high_outlier_is_detected_from_prior_history_only():
    request = _request(
        tuple(
            _month(month, value)
            for month, value in enumerate(
                [100, 102, 98, 101, 99, 160],
                start=1,
            )
        )
    )

    result = detect_monthly_anomaly(request)

    assert result.calculation_status is AnomalyStatus.DETECTED
    assert result.is_anomaly is True
    assert result.baseline.median == 100
    assert result.baseline.mad == 1
    assert result.robust_z_score == pytest.approx(40.47)
    assert date(2024, 6, 1) not in result.baseline.periods


def test_normal_value_is_explicitly_not_detected():
    rows = tuple(
        _month(month, value)
        for month, value in enumerate([100, 102, 98, 101, 99, 103], start=1)
    )
    result = detect_monthly_anomaly(_request(rows))

    assert result.calculation_status is AnomalyStatus.NOT_DETECTED
    assert result.is_anomaly is False


def test_insufficient_history_is_not_reported_as_no_anomaly():
    result = detect_monthly_anomaly(
        _request(
            (_month(3, 100), _month(4, 101), _month(5, 99), _month(6, 160))
        )
    )

    assert result.calculation_status is AnomalyStatus.NON_CONTIGUOUS_HISTORY
    assert result.is_anomaly is None


def test_missing_or_incomplete_history_is_controlled():
    missing = detect_monthly_anomaly(
        _request(
            (_month(1, 100), _month(2, 101), _month(4, 99), _month(5, 100), _month(6, 160))
        )
    )
    incomplete = detect_monthly_anomaly(
        _request(
            (
                _month(1, 100),
                _month(2, 101),
                _month(3, 99, "incomplete", "partial_load"),
                _month(4, 100),
                _month(5, 102),
                _month(6, 160),
            )
        )
    )

    assert missing.calculation_status is AnomalyStatus.NON_CONTIGUOUS_HISTORY
    assert incomplete.calculation_status is AnomalyStatus.INCOMPLETE_HISTORY
    assert missing.is_anomaly is None
    assert incomplete.is_anomaly is None


def test_incomplete_current_period_is_not_evaluated():
    rows = tuple(
        [_month(month, 100 + month) for month in range(1, 6)]
        + [_month(6, 20, "incomplete", "month_to_date")]
    )
    result = detect_monthly_anomaly(_request(rows))

    assert result.calculation_status is (
        AnomalyStatus.INCOMPLETE_CURRENT_PERIOD
    )
    assert result.is_anomaly is None


def test_zero_mad_requires_a_separate_explicit_rule():
    rows = tuple(_month(month, 100) for month in range(1, 6)) + (
        _month(6, 120),
    )
    result = detect_monthly_anomaly(_request(rows))

    assert result.calculation_status is AnomalyStatus.ZERO_DISPERSION
    assert result.robust_z_score is None
    assert result.is_anomaly is None


@pytest.mark.parametrize("threshold", [0, -1, float("inf"), True, "3.5"])
def test_invalid_threshold_is_rejected(threshold):
    with pytest.raises(ValidationError):
        _request(tuple(_month(month, month) for month in range(1, 7)), threshold=threshold)


def test_output_records_method_input_lineage_and_no_causal_claim():
    rows = tuple(
        _month(month, value)
        for month, value in enumerate([100, 102, 98, 101, 99, 160], start=1)
    )
    payload = detect_monthly_anomaly(_request(rows)).model_dump(mode="json")

    assert payload["method"]["name"] == "trailing_median_mad"
    assert payload["lineage"]["parent_run_id"] == "day09-anomaly"
    assert len(payload["raw_input"]) == 6
    assert len(payload["chart_data"]) == 6
    assert "anomaly_does_not_establish_cause" in payload["boundary_notes"]
