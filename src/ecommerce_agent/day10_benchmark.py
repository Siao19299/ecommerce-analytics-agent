"""Offline Day 10 closure using assistant-authored SQL and real Olist SQLite."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from src.ecommerce_agent.day09_pipeline import Day09RepairWorkflow
from src.ecommerce_agent.day09_repair import SqlRepairer
from src.ecommerce_agent.day10_adapter import (
    load_metric_provenance,
    monthly_observations_from_query_result,
)
from src.ecommerce_agent.day10_anomaly import (
    AnomalyRequest,
    detect_monthly_anomaly,
)
from src.ecommerce_agent.day10_comparison import (
    ComparisonRequest,
    ComparisonType,
    calculate_period_comparison,
)
from src.ecommerce_agent.day10_contribution import (
    ContributionComponent,
    ContributionRequest,
    ContributionScope,
    calculate_contribution,
)
from src.ecommerce_agent.day10_models import CalculationLineage
from src.ecommerce_agent.day10_presentation import (
    present_anomaly,
    present_comparison,
    present_contribution,
)
from src.ecommerce_agent.day10_trace import build_calculation_trace
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
)
from src.ecommerce_agent.sql_generation import (
    GeneratedQuery,
    SqlGenerationContext,
)
from src.ecommerce_agent.sql_safety import SqlSafetyPolicy, load_global_schema


MONTHLY_GMV_SQL = """
SELECT
    date(o.order_purchase_timestamp, 'start of month') AS purchase_month,
    SUM(i.price) AS monthly_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON o.order_id = i.order_id
WHERE o.order_status = 'delivered'
GROUP BY date(o.order_purchase_timestamp, 'start of month')
ORDER BY purchase_month
""".strip()


JULY_2018_CATEGORY_GMV_SQL = """
SELECT
    COALESCE(p.product_category_name, 'unknown') AS product_category,
    SUM(i.price) AS category_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON o.order_id = i.order_id
LEFT JOIN dim_products AS p ON i.product_id = p.product_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2018-07-01'
  AND o.order_purchase_timestamp < '2018-08-01'
GROUP BY COALESCE(p.product_category_name, 'unknown')
ORDER BY product_category
""".strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _context(root: Path, metric_id: str) -> SqlGenerationContext:
    global_schema = load_global_schema(root)
    return SqlGenerationContext(
        question="Day 10 assistant-authored offline deterministic fixture",
        analysis_plan={
            "metrics": [metric_id],
            "dimensions": ["purchase_month"],
            "filters": [],
            "time_range": {"mode": "all_data"},
        },
        retrieved_document_ids=(f"metric:{metric_id}",),
        metric_definitions=(
            {"metric_id": metric_id, "source": "metric_dictionary.csv"},
        ),
        dimension_definitions=(),
        schema_fields=tuple(
            {"qualified_name": f"{table}.{column}"}
            for table, columns in sorted(global_schema.items())
            for column in sorted(columns)
        ),
        table_context=tuple(
            {"table": table, "source": "sql/schema.sql"}
            for table in sorted(global_schema)
        ),
        parameter_contract={},
        safety_policy=SqlSafetyPolicy(
            global_schema=global_schema,
            plan_schema=global_schema,
            max_rows=2000,
        ),
    )


def _run_guarded_sql(
    root: Path,
    sql: str,
    metric_id: str,
    run_id: str,
):
    fake_client = FakeModelClient(
        ModelResponse(
            content='{"sql":"SELECT 1","parameters":{}}',
            model_name="unused-fake-repair-client",
        )
    )
    workflow = Day09RepairWorkflow(
        database_path=root / "data/processed/olist.sqlite3",
        repairer=SqlRepairer(
            client=fake_client,
            config=ModelConfig(model_name="unused-fake-repair-client"),
        ),
        response_source="assistant_authored_sql_fixture_real_sqlite",
    )
    result = workflow.run(
        GeneratedQuery(sql=sql, parameters={}),
        _context(root, metric_id),
        run_id=run_id,
    )
    if not result.is_success:
        raise RuntimeError(
            f"Day 10 offline source query failed: {result.trace.stop_reason}"
        )
    if fake_client.requests:
        raise RuntimeError("成功的离线源查询不得调用修复模型")
    return result


def _case(
    case_id: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    trace,
    presentation,
) -> dict[str, Any]:
    expectation_met = all(
        observed.get(key) == value for key, value in expected.items()
    )
    return {
        "case_id": case_id,
        "case_source": (
            "assistant_authored_mechanical_day10_case_from_user_requirements"
        ),
        "expected": expected,
        "observed": observed,
        "expectation_met": expectation_met,
        "business_reference_status": "not_independently_evaluated",
        "calculation_trace": trace.model_dump(mode="json"),
        "presentation": presentation.model_dump(mode="json"),
    }


def run_benchmark(root: Path) -> dict[str, Any]:
    database = root / "data/processed/olist.sqlite3"
    database_hash_before = _sha256(database)
    monthly_source = _run_guarded_sql(
        root,
        MONTHLY_GMV_SQL,
        "delivered_monthly_gmv",
        "day10-real-monthly-gmv-source",
    )
    rows = monthly_source.execution.rows
    first_period = date.fromisoformat(str(rows[0]["purchase_month"]))
    last_period = date.fromisoformat(str(rows[-1]["purchase_month"]))
    observations = monthly_observations_from_query_result(
        monthly_source.execution,
        period_column="purchase_month",
        value_column="monthly_gmv",
        incomplete_period_reasons={
            first_period: "first_metric_observed_boundary_month",
            last_period: "last_metric_observed_boundary_month",
        },
    )
    metric = load_metric_provenance(root, "delivered_monthly_gmv")
    lineage = CalculationLineage(
        parent_run_id=monthly_source.trace.run_id,
        source_sql_attempt=1,
        input_reference="day09_trace.attempts[0].result_summary.rows",
    )

    mom = calculate_period_comparison(
        ComparisonRequest(
            analysis_type=ComparisonType.MOM,
            target_period=date(2018, 7, 1),
            metric=metric,
            observations=observations,
            lineage=lineage,
        )
    )
    mom_trace = build_calculation_trace(
        calculation_id="day10-real-mom-2018-07",
        step="deterministic_comparison",
        result=mom,
    )
    yoy = calculate_period_comparison(
        ComparisonRequest(
            analysis_type=ComparisonType.YOY,
            target_period=date(2018, 7, 1),
            metric=metric,
            observations=observations,
            lineage=lineage,
        )
    )
    yoy_trace = build_calculation_trace(
        calculation_id="day10-real-yoy-2018-07",
        step="deterministic_comparison",
        result=yoy,
    )
    anomaly = detect_monthly_anomaly(
        AnomalyRequest(
            metric=metric,
            target_period=date(2017, 11, 1),
            observations=observations,
            lineage=lineage,
            history_window=5,
            minimum_history=5,
            threshold=3.5,
        )
    )
    anomaly_trace = build_calculation_trace(
        calculation_id="day10-real-anomaly-2017-11",
        step="deterministic_anomaly_detection",
        result=anomaly,
    )

    contribution_source = _run_guarded_sql(
        root,
        JULY_2018_CATEGORY_GMV_SQL,
        "delivered_category_gmv_contribution",
        "day10-real-category-gmv-source",
    )
    contribution_metric = load_metric_provenance(
        root,
        "delivered_category_gmv_contribution",
    )
    components = tuple(
        ContributionComponent(
            group=str(row["product_category"]),
            value=row["category_gmv"],
            is_unknown_group=row["product_category"] == "unknown",
        )
        for row in contribution_source.execution.rows
    )
    contribution = calculate_contribution(
        ContributionRequest(
            metric=contribution_metric,
            dimension="product_category",
            scope=ContributionScope(
                metric_id="delivered_category_gmv_contribution",
                period=date(2018, 7, 1),
                status_scope="order_status=delivered",
                amount_basis="fact_order_items.price_excluding_freight",
                completeness="complete",
            ),
            components=components,
            lineage=CalculationLineage(
                parent_run_id=contribution_source.trace.run_id,
                source_sql_attempt=1,
                input_reference=(
                    "day09_trace.attempts[0].result_summary.rows"
                ),
            ),
        )
    )
    contribution_trace = build_calculation_trace(
        calculation_id="day10-real-category-contribution-2018-07",
        step="deterministic_contribution",
        result=contribution,
    )

    cases = [
        _case(
            "D10_REAL_MOM",
            {
                "status": "computed",
                "comparison_period": "2018-06-01",
                "comparable": "comparable",
            },
            {
                "status": mom.calculation_status.value,
                "comparison_period": mom.comparison_period.isoformat(),
                "comparable": mom.comparability.value,
                "absolute_change": mom.absolute_change,
                "relative_change": mom.relative_change,
            },
            mom_trace,
            present_comparison(mom),
        ),
        _case(
            "D10_REAL_YOY",
            {
                "status": "computed",
                "comparison_period": "2017-07-01",
                "comparable": "comparable",
            },
            {
                "status": yoy.calculation_status.value,
                "comparison_period": yoy.comparison_period.isoformat(),
                "comparable": yoy.comparability.value,
                "absolute_change": yoy.absolute_change,
                "relative_change": yoy.relative_change,
            },
            yoy_trace,
            present_comparison(yoy),
        ),
        _case(
            "D10_REAL_CONTRIBUTION",
            {"status": "computed", "sum_check_passed": True},
            {
                "status": contribution.calculation_status.value,
                "sum_check_passed": contribution.sum_check.passed,
                "group_count": len(contribution.rows),
                "denominator_value": contribution.denominator_value,
                "contribution_sum": (
                    contribution.sum_check.contribution_sum
                ),
            },
            contribution_trace,
            present_contribution(contribution),
        ),
        _case(
            "D10_REAL_ANOMALY",
            {"status": "detected", "is_anomaly": True},
            {
                "status": anomaly.calculation_status.value,
                "is_anomaly": anomaly.is_anomaly,
                "robust_z_score": anomaly.robust_z_score,
                "threshold": anomaly.method.threshold,
            },
            anomaly_trace,
            present_anomaly(anomaly),
        ),
    ]
    database_hash_after = _sha256(database)
    return {
        "measurement_scope": (
            "assistant_authored_mechanical_cases_with_real_local_sqlite; "
            "not_independent_business_accuracy"
        ),
        "source_data": "public_olist_local_sqlite",
        "external_api_calls": 0,
        "model_generated_numeric_results": 0,
        "database_sha256_before": database_hash_before,
        "database_sha256_after": database_hash_after,
        "database_unchanged": database_hash_before == database_hash_after,
        "period_completeness_policy": (
            "first_and_last_metric_observed_months_are_marked_incomplete; "
            "this_is_a_conservative_offline_fixture_policy_not_proof_of_all_"
            "interior_month_completeness"
        ),
        "case_count": len(cases),
        "expectation_met_count": sum(
            case["expectation_met"] for case in cases
        ),
        "cases": cases,
    }


def main() -> None:
    root = Path(__file__).parents[2]
    report = run_benchmark(root)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    processed = root / "data/processed/day10/offline_benchmark.json"
    compact = root / "docs/DAY10_RESULTS.json"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(payload, encoding="utf-8")
    compact.write_text(payload, encoding="utf-8")
    print(
        "Day 10 offline benchmark: "
        f"{report['expectation_met_count']}/{report['case_count']} expected"
    )


if __name__ == "__main__":
    main()
