from pathlib import Path

import pytest
from pydantic import ValidationError

from src.ecommerce_agent.analysis_plan import (
    AnalysisPlan,
    FilterCondition,
    PlanValidationErrorType,
    TimeRange,
    try_validate_analysis_plan,
    validate_analysis_plan,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog


PROJECT_ROOT = Path(__file__).parents[1]
METRIC_DICTIONARY_PATH = (
    PROJECT_ROOT / "data" / "metadata" / "metric_dictionary.csv"
)
DIMENSION_DICTIONARY_PATH = (
    PROJECT_ROOT / "data" / "metadata" / "dimension_dictionary.csv"
)


@pytest.fixture
def metric_catalog():
    return MetricCatalog.from_csv(
        METRIC_DICTIONARY_PATH,
        DIMENSION_DICTIONARY_PATH,
    )


def test_filter_condition_accepts_matching_operator_and_value_shapes():
    equal_filter = FilterCondition.model_validate(
        {
            "field": "customer_state",
            "operator": "eq",
            "value": "SP",
        }
    )
    inclusion_filter = FilterCondition.model_validate(
        {
            "field": "payment_type",
            "operator": "in",
            "value": ["credit_card", "voucher"],
        }
    )

    assert equal_filter.value == "SP"
    assert inclusion_filter.value == ["credit_card", "voucher"]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "field": "customer_state",
            "operator": "eq",
            "value": ["SP", "RJ"],
        },
        {
            "field": "payment_type",
            "operator": "in",
            "value": "credit_card",
        },
        {
            "field": "payment_type",
            "operator": "in",
            "value": [],
        },
    ],
)
def test_filter_condition_rejects_mismatched_operator_and_value_shapes(
    payload,
):
    with pytest.raises(ValidationError):
        FilterCondition.model_validate(payload)


def test_time_range_accepts_bounded_dates_and_explicit_all_data():
    bounded = TimeRange.model_validate(
        {
            "mode": "bounded",
            "start_date": "2018-01-01",
            "end_date": "2018-06-30",
        }
    )
    all_data = TimeRange.model_validate({"mode": "all_data"})

    assert bounded.start_date.isoformat() == "2018-01-01"
    assert bounded.end_date.isoformat() == "2018-06-30"
    assert all_data.start_date is None
    assert all_data.end_date is None


def test_analysis_plan_accepts_valid_business_question_mapping():
    plan = AnalysisPlan.model_validate(
        {
            "metrics": ["delivered_gmv"],
            "dimensions": ["purchase_month"],
            "filters": [
                {
                    "field": "customer_state",
                    "operator": "eq",
                    "value": "SP",
                }
            ],
            "time_range": {
                "mode": "bounded",
                "start_date": "2018-01-01",
                "end_date": "2018-06-30",
            },
        }
    )

    assert plan.metrics == ["delivered_gmv"]
    assert plan.dimensions == ["purchase_month"]
    assert plan.filters[0].value == "SP"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "mode": "bounded",
            "start_date": "2018-01-01",
        },
        {
            "mode": "bounded",
            "start_date": "2018-06-01",
            "end_date": "2018-01-01",
        },
        {
            "mode": "all_data",
            "start_date": "2018-01-01",
            "end_date": "2018-06-30",
        },
    ],
)
def test_time_range_rejects_inconsistent_mode_and_dates(payload):
    with pytest.raises(ValidationError):
        TimeRange.model_validate(payload)


def test_metric_catalog_loads_metrics_and_stable_dimensions(
    metric_catalog,
):
    assert len(metric_catalog.metrics) == 27
    assert metric_catalog.dimensions == {
        "purchase_month",
        "product_category",
        "seller",
        "customer_state",
        "customer_city",
        "payment_type",
    }


@pytest.mark.parametrize(
    ("metrics", "dimensions"),
    [
        (["total_sales"], []),
        (["delivered_gmv"], ["unknown_dimension"]),
        (["delivered_payment_amount"], ["product_category"]),
        (
            ["delivered_gmv", "delivered_payment_amount"],
            ["product_category"],
        ),
    ],
)
def test_analysis_plan_rejects_unknown_or_unsupported_semantics(
    metric_catalog,
    metrics,
    dimensions,
):
    payload = {
        "metrics": metrics,
        "dimensions": dimensions,
        "filters": [],
        "time_range": {"mode": "all_data"},
    }

    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(
            payload,
            context={"metric_catalog": metric_catalog},
        )


def test_unified_validator_accepts_json_and_always_uses_catalog(
    metric_catalog,
):
    json_payload = """
    {
      "metrics": ["delivered_gmv"],
      "dimensions": ["purchase_month"],
      "filters": [],
      "time_range": {
        "mode": "bounded",
        "start_date": "2018-01-01",
        "end_date": "2018-06-30"
      }
    }
    """

    plan = validate_analysis_plan(json_payload, metric_catalog)

    assert plan.metrics == ["delivered_gmv"]


@pytest.mark.parametrize(
    "payload",
    [
        "这不是 JSON",
        {
            "metrics": ["total_sales"],
            "dimensions": [],
            "filters": [],
            "time_range": {"mode": "all_data"},
        },
    ],
)
def test_unified_validator_rejects_non_json_and_unknown_metric(
    metric_catalog,
    payload,
):
    with pytest.raises(ValidationError):
        validate_analysis_plan(payload, metric_catalog)


def test_safe_validator_returns_success_for_valid_plan(metric_catalog):
    result = try_validate_analysis_plan(
        {
            "metrics": ["delivered_gmv"],
            "dimensions": ["purchase_month"],
            "filters": [],
            "time_range": {"mode": "all_data"},
        },
        metric_catalog,
    )

    assert result.is_success is True
    assert result.plan is not None
    assert result.error_type is None


@pytest.mark.parametrize(
    ("payload", "expected_error_type"),
    [
        ("这不是 JSON", PlanValidationErrorType.NON_JSON),
        (
            {
                "metrics": ["delivered_gmv"],
                "dimensions": [],
                "filters": [],
            },
            PlanValidationErrorType.INVALID_STRUCTURE,
        ),
        (
            {
                "metrics": "delivered_gmv",
                "dimensions": [],
                "filters": [],
                "time_range": {"mode": "all_data"},
            },
            PlanValidationErrorType.INVALID_STRUCTURE,
        ),
        (
            {
                "metrics": ["delivered_gmv"],
                "dimensions": [],
                "filters": [],
                "time_range": {
                    "mode": "bounded",
                    "start_date": "2018-06-01",
                    "end_date": "2018-01-01",
                },
            },
            PlanValidationErrorType.INVALID_STRUCTURE,
        ),
        (
            {
                "metrics": ["total_sales"],
                "dimensions": [],
                "filters": [],
                "time_range": {"mode": "all_data"},
            },
            PlanValidationErrorType.INVALID_SEMANTICS,
        ),
        (
            {
                "metrics": ["delivered_payment_amount"],
                "dimensions": ["product_category"],
                "filters": [],
                "time_range": {"mode": "all_data"},
            },
            PlanValidationErrorType.INVALID_SEMANTICS,
        ),
    ],
)
def test_safe_validator_converts_bad_model_output_to_controlled_failure(
    metric_catalog,
    payload,
    expected_error_type,
):
    result = try_validate_analysis_plan(payload, metric_catalog)

    assert result.is_success is False
    assert result.plan is None
    assert result.error_type == expected_error_type
    assert result.error_message


def test_handwritten_valid_json_fails_only_at_metric_semantics(
    metric_catalog,
):
    payload = (
        PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "planning"
        / "invalid_unsupported_dimension.json"
    ).read_text(encoding="utf-8")

    structure_only_plan = AnalysisPlan.model_validate_json(payload)
    result = try_validate_analysis_plan(payload, metric_catalog)

    assert structure_only_plan.metrics == ["delivered_payment_amount"]
    assert structure_only_plan.dimensions == ["product_category"]
    assert result.is_success is False
    assert (
        result.error_type
        == PlanValidationErrorType.INVALID_SEMANTICS
    )


def test_analysis_plan_rejects_unexpected_model_fields(metric_catalog):
    payload = {
        "metrics": ["delivered_gmv"],
        "dimensions": [],
        "filters": [],
        "time_range": {"mode": "all_data"},
        "sql": "SELECT * FROM fact_orders",
    }

    result = try_validate_analysis_plan(payload, metric_catalog)

    assert result.is_success is False
    assert (
        result.error_type
        == PlanValidationErrorType.INVALID_STRUCTURE
    )


def test_analysis_plan_rejects_undeclared_extra_fields(metric_catalog):
    result = try_validate_analysis_plan(
        {
            "metrics": ["delivered_gmv"],
            "dimensions": [],
            "filters": [],
            "time_range": {"mode": "all_data"},
            "sql": "SELECT * FROM fact_orders",
        },
        metric_catalog,
    )

    assert result.is_success is False
    assert (
        result.error_type
        == PlanValidationErrorType.INVALID_STRUCTURE
    )
    assert "Extra inputs are not permitted" in result.error_message
