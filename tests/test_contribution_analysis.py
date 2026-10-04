"""Assistant-authored mechanical tests for Deterministic analysis contribution analysis."""

from datetime import date

import pytest
from pydantic import ValidationError

from src.ecommerce_agent.contribution_analysis import (
    ContributionComponent,
    ContributionRequest,
    ContributionScope,
    ContributionStatus,
    ExternalDenominator,
    calculate_contribution,
)
from src.ecommerce_agent.analysis_models import (
    CalculationLineage,
    MetricProvenance,
)


def _metric(metric_id="delivered_gmv"):
    return MetricProvenance(
        metric_id=metric_id,
        metric_definition="已送达订单商品成交金额，不含运费",
        time_field="order_purchase_timestamp",
    )


def _scope(metric_id="delivered_gmv", **changes):
    payload = {
        "metric_id": metric_id,
        "period": date(2024, 1, 1),
        "filters": {"customer_state": "SP"},
        "status_scope": "order_status=delivered",
        "amount_basis": "fact_order_items.price_excluding_freight",
        "completeness": "complete",
    }
    payload.update(changes)
    return ContributionScope(**payload)


def _lineage():
    return CalculationLineage(
        parent_run_id="query-contribution",
        source_sql_attempt=1,
        input_reference="query_result.rows",
    )


def _request(components, **changes):
    payload = {
        "metric": _metric(),
        "dimension": "product_category",
        "scope": _scope(),
        "components": components,
        "lineage": _lineage(),
    }
    payload.update(changes)
    return ContributionRequest(**payload)


def test_normal_contribution_preserves_unknown_and_sums_to_one():
    result = calculate_contribution(
        _request(
            (
                ContributionComponent(group="A", value=60),
                ContributionComponent(group="B", value=30),
                ContributionComponent(
                    group="unknown",
                    value=10,
                    is_unknown_group=True,
                ),
            )
        )
    )

    assert result.calculation_status is ContributionStatus.COMPUTED
    assert [row.contribution for row in result.rows] == [0.6, 0.3, 0.1]
    assert result.rows[-1].is_unknown_group is True
    assert result.sum_check.passed is True
    assert result.sum_check.contribution_sum == pytest.approx(1.0)


def test_external_denominator_with_different_scope_is_rejected():
    wrong_scope = _scope(filters={"customer_state": "RJ"})

    with pytest.raises(ValidationError, match="分析范围不一致"):
        _request(
            (ContributionComponent(group="A", value=60),),
            denominator=ExternalDenominator(
                value=100,
                scope=wrong_scope,
                input_reference="separate_total_query",
            ),
        )


def test_same_scope_external_total_records_unallocated_difference():
    result = calculate_contribution(
        _request(
            (ContributionComponent(group="A", value=60),),
            denominator=ExternalDenominator(
                value=100,
                scope=_scope(),
                input_reference="verified_same_scope_total",
            ),
        )
    )

    assert result.rows[0].contribution == 0.6
    assert result.sum_check.passed is False
    assert result.sum_check.value_difference == -40
    assert result.sum_check.contribution_sum == 0.6


def test_zero_denominator_does_not_return_zero_percentages():
    result = calculate_contribution(
        _request(
            (
                ContributionComponent(group="A", value=0),
                ContributionComponent(group="B", value=0),
            )
        )
    )

    assert result.calculation_status is ContributionStatus.ZERO_DENOMINATOR
    assert all(row.contribution is None for row in result.rows)
    assert result.sum_check.contribution_sum is None


def test_negative_components_are_computed_but_explicitly_flagged():
    result = calculate_contribution(
        _request(
            (
                ContributionComponent(group="positive", value=120),
                ContributionComponent(group="negative", value=-20),
            )
        )
    )

    assert result.denominator_value == 100
    assert [row.contribution for row in result.rows] == [1.2, -0.2]
    assert "negative_component_requires_non_share_interpretation" in (
        result.boundary_notes
    )


def test_duplicate_groups_and_metric_scope_mismatch_are_controlled():
    with pytest.raises(ValidationError, match="重复分组"):
        _request(
            (
                ContributionComponent(group="A", value=1),
                ContributionComponent(group="A", value=2),
            )
        )
    with pytest.raises(ValidationError, match="metric_id"):
        _request(
            (ContributionComponent(group="A", value=1),),
            scope=_scope(metric_id="delivered_payment_amount"),
        )


def test_incomplete_scope_and_traceable_output_are_preserved():
    result = calculate_contribution(
        _request(
            (ContributionComponent(group="A", value=10),),
            scope=_scope(completeness="incomplete"),
        )
    )
    payload = result.model_dump(mode="json")

    assert "incomplete_period_not_standard_comparable" in (
        payload["boundary_notes"]
    )
    assert payload["metric"]["definition_source"] == (
        "data/metadata/metric_dictionary.csv"
    )
    assert payload["lineage"]["parent_run_id"] == "query-contribution"
    assert payload["chart_data"] == payload["rows"]
