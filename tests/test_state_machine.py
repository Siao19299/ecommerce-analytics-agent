"""Assistant-authored tests for the plain-Python Agent workflow state machine."""

import json
import sqlite3
from datetime import date
from pathlib import Path

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.database import create_schema
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
    CalculationStatus,
    ComparisonRequest,
    ComparisonType,
    calculate_period_comparison,
)
from src.ecommerce_agent.analysis_models import CalculationLineage
from src.ecommerce_agent.analysis_presentation import present_comparison
from src.ecommerce_agent.workflow_state import WorkflowNode, WorkflowStatus
from src.ecommerce_agent.langgraph_runner import LangGraphRunner
from src.ecommerce_agent.workflow import (
    NODE_CONTRACTS,
    WorkflowServices,
    AgentStateMachine,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
)
from src.ecommerce_agent.retrieval import (
    RetrievalFilter,
    RetrievalHit,
    build_documents,
)
from src.ecommerce_agent.sql_generation import SqlGenerator
from src.ecommerce_agent.sql_generation import (
    QueryExecutionErrorType,
    QueryExecutionResult,
)


ROOT = Path(__file__).parents[1]
QUESTION = "比较 2018 年 7 月与 6 月的已送达月度 GMV。"
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
SUCCESS_SQL = """
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


class FixedRetriever:
    version = "fixed-workflow-test-retriever"

    def __init__(self, documents):
        selected = {
            "metric:delivered_monthly_gmv",
            "schema:fact_orders",
            "schema:fact_order_items",
        }
        self.documents = tuple(
            item for item in documents if item.document_id in selected
        )

    def search(self, query, *, top_k=5, filters=None):
        filters = filters or RetrievalFilter()
        matches = [
            item
            for item in self.documents
            if item.document_type in filters.document_types
        ][:top_k]
        return tuple(
            RetrievalHit(
                document=item,
                score=1.0,
                rank=index,
                evidence={"source": "assistant_authored_test_fixture"},
            )
            for index, item in enumerate(matches, 1)
        )


def _catalog():
    return MetricCatalog.from_csv(
        ROOT / "data/metadata/metric_dictionary.csv",
        ROOT / "data/metadata/dimension_dictionary.csv",
    )


def _database(path: Path):
    connection = sqlite3.connect(path)
    create_schema(connection, str(ROOT / "sql/schema.sql"))
    connection.executescript(
        """
        INSERT INTO dim_customers VALUES
            ('c1', 'u1', '01001', 'city', 'SP');
        INSERT INTO dim_products VALUES
            ('p1', 'category', 10, 100, 1, 200, 10, 5, 8);
        INSERT INTO dim_sellers VALUES
            ('s1', '01001', 'city', 'SP');
        INSERT INTO fact_orders VALUES
            ('o1', 'c1', 'delivered', '2018-06-10', NULL, NULL, NULL,
             '2018-06-20'),
            ('o2', 'c1', 'delivered', '2018-07-10', NULL, NULL, NULL,
             '2018-07-20');
        INSERT INTO fact_order_items VALUES
            ('o1', 1, 'p1', 's1', '2018-06-11', 100, 10),
            ('o2', 1, 'p1', 's1', '2018-07-11', 120, 12);
        """
    )
    connection.commit()
    connection.close()
    return path


def _response(payload, model="fake-workflow"):
    return ModelResponse(
        content=json.dumps(payload, ensure_ascii=False),
        model_name=model,
    )


def _default_analyzer(state):
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
                ROOT,
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


def _machine(
    database,
    *,
    planning_payload=PLAN,
    sql=SUCCESS_SQL,
    repair_sqls=(),
    max_repairs=2,
    analyzer=_default_analyzer,
    max_node_steps=16,
):
    documents = build_documents(ROOT)
    planner_client = FakeModelClient(_response(planning_payload, "planner"))
    sql_client = FakeModelClient(
        _response({"sql": sql, "parameters": PARAMETERS}, "sql")
    )
    repair_responses = [
        _response({"sql": item, "parameters": PARAMETERS}, "repair")
        for item in repair_sqls
    ]
    fallback = repair_responses[-1] if repair_responses else _response(
        {"sql": SUCCESS_SQL, "parameters": PARAMETERS},
        "repair",
    )
    repair_client = FakeModelClient(
        fallback,
        scripted_responses=list(repair_responses),
    )
    services = WorkflowServices(
        root=ROOT,
        database_path=database,
        documents=documents,
        retriever=FixedRetriever(documents),
        planner=AnalysisPlanner(
            planner_client,
            ModelConfig(model_name="planner"),
            _catalog(),
            max_output_corrections=0,
            require_retrieval_grounding=True,
        ),
        sql_generator=SqlGenerator(
            sql_client,
            ModelConfig(model_name="sql"),
        ),
        repair_workflow=SqlRepairWorkflow(
            database_path=database,
            repairer=SqlRepairer(
                repair_client,
                ModelConfig(model_name="repair"),
            ),
            limits=RepairLimits(max_repair_attempts=max_repairs),
            response_source="fake_model_response",
        ),
        analyzer=analyzer,
        presenter=present_comparison,
    )
    return (
        AgentStateMachine(services, max_node_steps=max_node_steps),
        planner_client,
        sql_client,
        repair_client,
    )


def test_normal_path_has_ordered_nodes_and_shared_lineage(tmp_path):
    machine, _, _, repair_client = _machine(
        _database(tmp_path / "normal.sqlite3")
    )

    state = machine.run(QUESTION, run_id="workflow-normal")

    assert state.status is WorkflowStatus.SUCCEEDED
    assert state.stop_reason == "completed"
    assert [item.node for item in state.node_trace] == [
        WorkflowNode.RETRIEVAL,
        WorkflowNode.PLANNING,
        WorkflowNode.SQL_GENERATION,
        WorkflowNode.SQL_SAFETY,
        WorkflowNode.EXECUTION,
        WorkflowNode.DETERMINISTIC_ANALYSIS,
        WorkflowNode.PRESENTATION,
        WorkflowNode.FINALIZATION,
    ]
    assert state.sql_attempt_trace.run_id == "workflow-normal"
    assert state.calculation_trace.parent_run_id == "workflow-normal"
    assert state.calculation_trace.source_sql_attempt == 1
    assert state.analysis_result.relative_change == 0.2
    assert len(repair_client.requests) == 0


def test_clarification_stops_before_sql_generation(tmp_path):
    machine, _, sql_client, repair_client = _machine(
        _database(tmp_path / "clarification.sqlite3"),
        planning_payload={
            "status": "needs_clarification",
            "clarification_question": "销售额具体指哪一种口径？",
        },
    )

    state = machine.run("分析销售额。")

    assert state.status is WorkflowStatus.NEEDS_CLARIFICATION
    assert state.clarification_question == "销售额具体指哪一种口径？"
    assert state.generated_query is None
    assert len(sql_client.requests) == 0
    assert len(repair_client.requests) == 0


def test_safety_rejection_never_executes_or_repairs(tmp_path):
    machine, _, _, repair_client = _machine(
        _database(tmp_path / "unsafe.sqlite3"),
        sql="DELETE FROM fact_orders",
    )

    state = machine.run(QUESTION)

    assert state.status is WorkflowStatus.SAFETY_REJECTED
    assert state.sql_safety_trace.accepted is False
    assert state.execution_result is None
    assert WorkflowNode.EXECUTION not in [x.node for x in state.node_trace]
    assert len(repair_client.requests) == 0


def test_one_repair_continues_to_deterministic_analysis(tmp_path):
    machine, _, _, repair_client = _machine(
        _database(tmp_path / "repair-success.sqlite3"),
        sql=REPAIRABLE_SQL,
        repair_sqls=(SUCCESS_SQL,),
    )

    state = machine.run(QUESTION, run_id="repair-success")

    assert state.status is WorkflowStatus.SUCCEEDED
    assert state.repair_result.repair_succeeded is True
    assert state.sql_attempt_trace.stop_reason == "repair_succeeded"
    assert len(state.sql_attempt_trace.attempts) == 2
    assert WorkflowNode.BOUNDED_REPAIR in [x.node for x in state.node_trace]
    assert state.calculation_trace.source_sql_attempt == 2
    assert len(repair_client.requests) == 1


def test_repair_limit_is_a_distinct_terminal_state(tmp_path):
    second_error = REPAIRABLE_SQL.replace(
        "o.customer_id",
        "o.order_status",
    )
    machine, _, _, repair_client = _machine(
        _database(tmp_path / "repair-limit.sqlite3"),
        sql=REPAIRABLE_SQL,
        repair_sqls=(second_error,),
        max_repairs=1,
    )

    state = machine.run(QUESTION)

    assert state.status is WorkflowStatus.REPAIR_LIMIT_REACHED
    assert state.stop_reason == "repair_limit_reached"
    assert state.analysis_result is None
    assert len(state.sql_attempt_trace.attempts) == 2
    assert len(repair_client.requests) == 1


def test_missing_comparison_period_is_preserved_not_treated_as_failure(
    tmp_path,
):
    july_only = SUCCESS_SQL.replace(
        "o.order_purchase_timestamp >= :start_date",
        "o.order_purchase_timestamp >= '2018-07-01'",
    ).replace("  AND o.order_purchase_timestamp < :end_date_exclusive\n", "")
    # Keep the exact parameter contract in harmless predicates.
    july_only += "\nLIMIT (CASE WHEN :start_date < :end_date_exclusive THEN 100 ELSE 0 END)"
    machine, _, _, _ = _machine(
        _database(tmp_path / "missing-period.sqlite3"),
        sql=july_only,
    )

    state = machine.run(QUESTION)

    assert state.status is WorkflowStatus.SUCCEEDED
    assert state.analysis_result.calculation_status is (
        CalculationStatus.MISSING_COMPARISON_PERIOD
    )
    assert state.presentation.conclusion


def test_calculation_exception_is_controlled_and_skips_presentation(tmp_path):
    def failing_analyzer(state):
        raise AnalysisInputError(
            AnalysisInputErrorCode.INVALID_VALUE,
            "助手机械案例中的受控计算失败",
        )

    machine, _, _, _ = _machine(
        _database(tmp_path / "calculation-failure.sqlite3"),
        analyzer=failing_analyzer,
    )

    state = machine.run(QUESTION)

    assert state.status is WorkflowStatus.CALCULATION_FAILED
    assert state.stop_reason == "deterministic_analysis_exception"
    assert state.presentation is None
    assert state.node_trace[-2].outcome.value == "failed"


def test_environment_error_never_calls_repair_model(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    machine, _, _, repair_client = _machine(missing)

    state = machine.run(QUESTION)

    assert state.status is WorkflowStatus.ENVIRONMENT_FAILED
    assert state.stop_reason == "environment_error"
    assert len(repair_client.requests) == 0


def test_timeout_and_unknown_database_error_never_enter_repair(
    tmp_path,
    monkeypatch,
):
    scenarios = (
        (
            QueryExecutionErrorType.TIMEOUT,
            "查询超过截止时间",
            WorkflowStatus.RESOURCE_FAILED,
            "resource_failure",
        ),
        (
            QueryExecutionErrorType.DATABASE,
            "malformed JSON",
            WorkflowStatus.EXECUTION_FAILED,
            "unclassified_database_error",
        ),
    )
    for index, (error_type, message, expected_status, stop_reason) in enumerate(
        scenarios
    ):
        machine, _, _, repair_client = _machine(
            _database(tmp_path / f"nonrepair-{index}.sqlite3")
        )
        monkeypatch.setattr(
            "src.ecommerce_agent.repair_workflow.execute_read_only_query",
            lambda *args, error_type=error_type, message=message, **kwargs: (
                QueryExecutionResult(
                    error_type=error_type,
                    error_message=message,
                    execution_started=True,
                )
            ),
        )

        state = machine.run(QUESTION)

        assert state.status is expected_status
        assert state.stop_reason == stop_reason
        assert len(repair_client.requests) == 0


def test_node_contracts_expose_preconditions_and_write_scope():
    assert set(NODE_CONTRACTS) == set(WorkflowNode)
    assert NODE_CONTRACTS[WorkflowNode.EXECUTION].required_fields == (
        "generation_context",
        "generated_query",
        "sql_safety_trace",
    )
    assert "analysis_result" not in NODE_CONTRACTS[
        WorkflowNode.SQL_SAFETY
    ].allowed_updates
    assert "presentation" not in NODE_CONTRACTS[
        WorkflowNode.DETERMINISTIC_ANALYSIS
    ].allowed_updates


def test_top_level_loop_guard_is_program_enforced(tmp_path):
    machine, _, _, _ = _machine(
        _database(tmp_path / "loop-guard.sqlite3"),
        max_node_steps=1,
    )

    state = machine.run(QUESTION)

    assert state.status is WorkflowStatus.INTERNAL_FAILED
    assert state.stop_reason == "max_node_steps_reached"
    assert len(state.node_trace) == 1


def test_final_state_is_json_serializable(tmp_path):
    machine, _, _, _ = _machine(
        _database(tmp_path / "serialize.sqlite3")
    )

    payload = state_payload = machine.run(QUESTION).to_dict()

    encoded = json.dumps(state_payload, ensure_ascii=False)
    assert payload["status"] == "succeeded"
    assert "calculation_trace" in encoded


def test_langgraph_maps_every_contract_and_reuses_python_nodes(tmp_path):
    machine, _, _, _ = _machine(
        _database(tmp_path / "langgraph.sqlite3")
    )
    runner = LangGraphRunner(machine)

    state = runner.run(QUESTION, run_id="langgraph-success")
    graph_nodes = set(runner.graph.get_graph().nodes)

    assert {node.value for node in WorkflowNode} <= graph_nodes
    assert state.status is WorkflowStatus.SUCCEEDED
    assert state.sql_attempt_trace.run_id == "langgraph-success"
    assert state.calculation_trace.parent_run_id == "langgraph-success"
    assert [item.node for item in state.node_trace] == [
        WorkflowNode.RETRIEVAL,
        WorkflowNode.PLANNING,
        WorkflowNode.SQL_GENERATION,
        WorkflowNode.SQL_SAFETY,
        WorkflowNode.EXECUTION,
        WorkflowNode.DETERMINISTIC_ANALYSIS,
        WorkflowNode.PRESENTATION,
        WorkflowNode.FINALIZATION,
    ]


def test_langgraph_clarification_uses_same_terminal_branch(tmp_path):
    machine, _, sql_client, _ = _machine(
        _database(tmp_path / "langgraph-clarification.sqlite3"),
        planning_payload={
            "status": "needs_clarification",
            "clarification_question": "请确认销售额口径。",
        },
    )

    state = LangGraphRunner(machine).run("分析销售额。")

    assert state.status is WorkflowStatus.NEEDS_CLARIFICATION
    assert [item.node for item in state.node_trace] == [
        WorkflowNode.RETRIEVAL,
        WorkflowNode.PLANNING,
        WorkflowNode.FINALIZATION,
    ]
    assert len(sql_client.requests) == 0
