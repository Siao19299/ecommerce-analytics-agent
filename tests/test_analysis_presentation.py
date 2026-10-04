"""Assistant-authored tests for deterministic Deterministic analysis presentation."""

from datetime import date

from src.ecommerce_agent.anomaly_detection import (
    AnomalyRequest,
    detect_monthly_anomaly,
)
from src.ecommerce_agent.period_comparison import (
    ComparisonRequest,
    ComparisonType,
    calculate_period_comparison,
)
from src.ecommerce_agent.contribution_analysis import (
    ContributionComponent,
    ContributionRequest,
    ContributionScope,
    calculate_contribution,
)
from src.ecommerce_agent.analysis_models import (
    CalculationLineage,
    MetricProvenance,
    MonthlyObservation,
)
from src.ecommerce_agent.analysis_presentation import (
    ChartType,
    present_anomaly,
    present_comparison,
    present_contribution,
)


def _metric(metric_id="delivered_gmv"):
    return MetricProvenance(
        metric_id=metric_id,
        metric_definition="已送达订单商品成交金额，不含运费",
        time_field="order_purchase_timestamp",
    )


def _lineage():
    return CalculationLineage(
        parent_run_id="query-presentation",
        source_sql_attempt=1,
        input_reference="query_result.rows",
    )


def test_comparison_presentation_uses_precomputed_values_and_boundaries():
    result = calculate_period_comparison(
        ComparisonRequest(
            analysis_type=ComparisonType.MOM,
            target_period=date(2024, 2, 1),
            metric=_metric(),
            observations=(
                MonthlyObservation(
                    period=date(2024, 1, 1),
                    value=100,
                    completeness="complete",
                ),
                MonthlyObservation(
                    period=date(2024, 2, 1),
                    value=120,
                    completeness="complete",
                ),
            ),
            lineage=_lineage(),
        )
    )
    presentation = present_comparison(result)

    assert presentation.chart.chart_type is ChartType.BAR
    assert presentation.table.rows[0]["value"] == 100
    assert presentation.table.rows[1]["value"] == 120
    assert "20.0000%" in presentation.conclusion
    assert presentation.generated_by == "deterministic_python_template"


def test_incomplete_comparison_template_does_not_claim_standard_growth():
    result = calculate_period_comparison(
        ComparisonRequest(
            analysis_type=ComparisonType.YOY,
            target_period=date(2024, 2, 1),
            metric=_metric(),
            observations=(
                MonthlyObservation(
                    period=date(2023, 2, 1),
                    value=100,
                    completeness="complete",
                ),
                MonthlyObservation(
                    period=date(2024, 2, 1),
                    value=50,
                    completeness="incomplete",
                    completeness_reason="month_to_date",
                ),
            ),
            lineage=_lineage(),
        )
    )
    presentation = present_comparison(result)

    assert "不应解释为标准同比或环比" in presentation.conclusion


def test_contribution_uses_sorted_bar_data_and_preserves_unknown():
    result = calculate_contribution(
        ContributionRequest(
            metric=_metric("delivered_category_gmv_contribution"),
            dimension="product_category",
            scope=ContributionScope(
                metric_id="delivered_category_gmv_contribution",
                period=date(2024, 1, 1),
                status_scope="order_status=delivered",
                amount_basis="price_excluding_freight",
                completeness="complete",
            ),
            components=(
                ContributionComponent(
                    group="unknown", value=10, is_unknown_group=True
                ),
                ContributionComponent(group="A", value=60),
                ContributionComponent(group="B", value=30),
            ),
            lineage=_lineage(),
        )
    )
    presentation = present_contribution(result)

    assert presentation.chart.chart_type is ChartType.BAR
    assert [row["group"] for row in presentation.chart.data] == [
        "A",
        "B",
        "unknown",
    ]
    assert "unknown 分组保留" in presentation.conclusion


def test_anomaly_line_chart_and_text_never_claim_a_cause():
    values = [100, 102, 98, 101, 99, 160]
    result = detect_monthly_anomaly(
        AnomalyRequest(
            metric=_metric(),
            target_period=date(2024, 6, 1),
            observations=tuple(
                MonthlyObservation(
                    period=date(2024, month, 1),
                    value=value,
                    completeness="complete",
                )
                for month, value in enumerate(values, start=1)
            ),
            lineage=_lineage(),
            history_window=5,
            minimum_history=5,
            threshold=3.5,
        )
    )
    presentation = present_anomaly(result)

    assert presentation.chart.chart_type is ChartType.LINE
    assert presentation.chart.y_fields == ("value", "baseline_median")
    assert "不证明异常原因" in presentation.conclusion
    assert "促销" not in presentation.conclusion


def test_insufficient_anomaly_evidence_is_not_called_no_anomaly():
    result = detect_monthly_anomaly(
        AnomalyRequest(
            metric=_metric(),
            target_period=date(2024, 3, 1),
            observations=(
                MonthlyObservation(
                    period=date(2024, 2, 1),
                    value=100,
                    completeness="complete",
                ),
                MonthlyObservation(
                    period=date(2024, 3, 1),
                    value=110,
                    completeness="complete",
                ),
            ),
            lineage=_lineage(),
            history_window=5,
            minimum_history=5,
        )
    )
    presentation = present_anomaly(result)

    assert "证据不足" in presentation.conclusion
    assert "不能表述为未发现异常" in presentation.conclusion
