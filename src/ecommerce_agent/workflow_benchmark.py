"""Offline Agent workflow closure using fake model responses and real Olist SQLite."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Callable

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.repair_limits import RepairLimits
from src.ecommerce_agent.repair_workflow import SqlRepairWorkflow
from src.ecommerce_agent.sql_repair import SqlRepairer
from src.ecommerce_agent.analysis_adapter import (
    AnalysisInputError,
    AnalysisInputErrorCode,
    load_metric_provenance,
    monthly_observations_from_query_result,
)
from src.ecommerce_agent.period_comparison import (
    ComparisonRequest,
    ComparisonType,
    calculate_period_comparison,
)
from src.ecommerce_agent.analysis_models import CalculationLineage
from src.ecommerce_agent.analysis_presentation import present_comparison
from src.ecommerce_agent.workflow_state import WorkflowState
from src.ecommerce_agent.workflow import (
    WorkflowServices,
    AgentStateMachine,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
)
from src.ecommerce_agent.retrieval import KeywordRetriever, build_documents
from src.ecommerce_agent.sql_generation import SqlGenerator


QUESTION = "比较 2018 年 7 月与 6 月的已送达月度 GMV 环比。"
PLAN = {
    "status": "ready",
    "plan": {
        "metrics": ["delivered_monthly_gmv"],
        "dimensions": ["purchase_month"],
        "filters": [],
        "time_range": {
            "mode": "bounded",
            "start_date": "2018-06-01",
            "end_date": "2018-07-31",
        },
    },
    "evidence_document_ids": ["metric:delivered_monthly_gmv"],
}
PARAMETERS = {
    "start_date": "2018-06-01",
    "end_date_exclusive": "2018-08-01",
}
MONTHLY_SQL = """
SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       SUM(i.price) AS monthly_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month
""".strip()
REPAIRABLE_SQL = """
SELECT COUNT(DISTINCT i.order_id, o.customer_id) AS monthly_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
""".strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _response(payload: dict, model: str) -> ModelResponse:
    return ModelResponse(
        content=json.dumps(payload, ensure_ascii=False),
        model_name=model,
    )


def _catalog(root: Path) -> MetricCatalog:
    return MetricCatalog.from_csv(
        root / "data/metadata/metric_dictionary.csv",
        root / "data/metadata/dimension_dictionary.csv",
    )


def _comparison_analyzer(root: Path):
    def analyze(state: WorkflowState):
        observations = monthly_observations_from_query_result(
            state.execution_result,
            period_column="purchase_month",
            value_column="monthly_gmv",
            incomplete_period_reasons={},
        )
        return calculate_period_comparison(
            ComparisonRequest(
                analysis_type=ComparisonType.MOM,
                target_period=date(2018, 7, 1),
                metric=load_metric_provenance(
                    root,
                    "delivered_monthly_gmv",
                ),
                observations=observations,
                lineage=CalculationLineage(
                    parent_run_id=state.run_id,
                    source_sql_attempt=(
                        state.sql_attempt_trace.attempts[-1].sql_attempt
                    ),
                    input_reference=(
                        "query_trace.attempts[-1].result_summary.rows"
                    ),
                ),
            )
        )

    return analyze


def _controlled_failure_analyzer(state: WorkflowState):
    raise AnalysisInputError(
        AnalysisInputErrorCode.INVALID_VALUE,
        "助手机械案例：确定性计算输入被受控拒绝",
    )


def _machine(
    root: Path,
    *,
    planning_payload: dict = PLAN,
    sql: str = MONTHLY_SQL,
    repair_sqls: tuple[str, ...] = (),
    max_repairs: int = 2,
    analyzer: Callable = None,
) -> AgentStateMachine:
    documents = build_documents(root)
    planning_client = FakeModelClient(
        _response(planning_payload, "fake-workflow-planner")
    )
    sql_client = FakeModelClient(
        _response(
            {"sql": sql, "parameters": PARAMETERS},
            "fake-workflow-sql",
        )
    )
    repair_responses = [
        _response(
            {"sql": candidate, "parameters": PARAMETERS},
            "fake-workflow-repair",
        )
        for candidate in repair_sqls
    ]
    repair_client = FakeModelClient(
        response=(
            repair_responses[-1]
            if repair_responses
            else _response(
                {"sql": MONTHLY_SQL, "parameters": PARAMETERS},
                "fake-workflow-repair",
            )
        ),
        scripted_responses=repair_responses,
    )
    database = root / "data/processed/olist.sqlite3"
    return AgentStateMachine(
        WorkflowServices(
            root=root,
            database_path=database,
            documents=documents,
            retriever=KeywordRetriever(documents),
            planner=AnalysisPlanner(
                planning_client,
                ModelConfig(model_name="fake-workflow-planner"),
                _catalog(root),
                max_output_corrections=0,
                require_retrieval_grounding=True,
            ),
            sql_generator=SqlGenerator(
                sql_client,
                ModelConfig(model_name="fake-workflow-sql"),
            ),
            repair_workflow=SqlRepairWorkflow(
                database_path=database,
                repairer=SqlRepairer(
                    repair_client,
                    ModelConfig(model_name="fake-workflow-repair"),
                ),
                limits=RepairLimits(max_repair_attempts=max_repairs),
                response_source="fake_model_response",
            ),
            analyzer=analyzer or _comparison_analyzer(root),
            presenter=present_comparison,
        )
    )


def _july_only_sql() -> str:
    return MONTHLY_SQL.replace(
        "  AND o.order_purchase_timestamp < :end_date_exclusive",
        "  AND o.order_purchase_timestamp < :end_date_exclusive\n"
        "  AND o.order_purchase_timestamp >= '2018-07-01'",
    )


def run_benchmark(root: Path) -> dict:
    database = root / "data/processed/olist.sqlite3"
    before = _sha256(database)
    repeated_error = REPAIRABLE_SQL.replace(
        "o.customer_id",
        "o.order_status",
    )
    cases = (
        (
            "D11_NORMAL_SUCCESS",
            _machine(root),
            QUESTION,
            "succeeded",
        ),
        (
            "D11_NEEDS_CLARIFICATION",
            _machine(
                root,
                planning_payload={
                    "status": "needs_clarification",
                    "clarification_question": (
                        "销售额具体指 GMV、含运费成交额还是支付金额？"
                    ),
                },
            ),
            "分析销售额。",
            "needs_clarification",
        ),
        (
            "D11_SAFETY_REJECTION",
            _machine(root, sql="DELETE FROM fact_orders"),
            QUESTION,
            "safety_rejected",
        ),
        (
            "D11_ONE_REPAIR_SUCCESS",
            _machine(
                root,
                sql=REPAIRABLE_SQL,
                repair_sqls=(MONTHLY_SQL,),
            ),
            QUESTION,
            "succeeded",
        ),
        (
            "D11_REPAIR_LIMIT",
            _machine(
                root,
                sql=REPAIRABLE_SQL,
                repair_sqls=(repeated_error,),
                max_repairs=1,
            ),
            QUESTION,
            "repair_limit_reached",
        ),
        (
            "D11_MISSING_COMPARISON_STATE",
            _machine(root, sql=_july_only_sql()),
            QUESTION,
            "succeeded",
        ),
        (
            "D11_CONTROLLED_CALCULATION_FAILURE",
            _machine(root, analyzer=_controlled_failure_analyzer),
            QUESTION,
            "calculation_failed",
        ),
    )
    results = []
    for case_id, machine, question, expected_status in cases:
        state = machine.run(question, run_id=case_id.lower())
        payload = state.to_dict()
        calculation_status = (
            getattr(state.analysis_result, "calculation_status", None)
            if state.analysis_result is not None
            else None
        )
        if calculation_status is not None:
            calculation_status = calculation_status.value
        lineage_connected = (
            state.calculation_trace is not None
            and state.sql_attempt_trace is not None
            and state.calculation_trace.parent_run_id == state.run_id
            and state.sql_attempt_trace.run_id == state.run_id
        )
        expectation_met = state.status.value == expected_status
        if case_id == "D11_MISSING_COMPARISON_STATE":
            expectation_met = expectation_met and (
                calculation_status == "missing_comparison_period"
            )
        results.append(
            {
                "case_id": case_id,
                "case_source": (
                    "assistant_authored_mechanical_day11_case_"
                    "from_user_requirements"
                ),
                "model_response_source": "fake_model_response",
                "expected_status": expected_status,
                "observed_status": state.status.value,
                "stop_reason": state.stop_reason,
                "calculation_status": calculation_status,
                "node_sequence": [
                    item.node.value for item in state.node_trace
                ],
                "sql_attempt_count": (
                    len(state.sql_attempt_trace.attempts)
                    if state.sql_attempt_trace is not None
                    else 0
                ),
                "lineage_connected": lineage_connected,
                "expectation_met": expectation_met,
                "business_reference_status": (
                    "not_independently_evaluated"
                ),
                "final_state": payload,
            }
        )
    after = _sha256(database)
    return {
        "measurement_scope": (
            "assistant_authored_mechanical_cases_with_real_local_sqlite; "
            "fake_model_responses; not_independent_business_accuracy"
        ),
        "source_data": "public_olist_local_sqlite",
        "external_api_calls": 0,
        "model_generated_numeric_results": 0,
        "case_count": len(results),
        "expectation_met_count": sum(
            item["expectation_met"] for item in results
        ),
        "database_sha256_before": before,
        "database_sha256_after": after,
        "database_unchanged": before == after,
        "cases": results,
    }


def main() -> None:
    root = Path(__file__).parents[2]
    report = run_benchmark(root)
    for destination in (
        root / "data/processed/workflow/offline_benchmark.json",
        root / "docs/reports/workflow.json",
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
