"""Assistant-authored real-SQLite tests for the bounded Day 9 workflow."""

import json
import sqlite3

from src.ecommerce_agent.day09_attempt_budget import RepairLimits
from src.ecommerce_agent.day09_pipeline import (
    BusinessValidationStatus,
    Day09RepairWorkflow,
)
from src.ecommerce_agent.day09_repair import SqlRepairer
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
    RetryPolicy,
    RetryingModelClient,
    TransientModelError,
)
from src.ecommerce_agent.sql_generation import (
    GeneratedQuery,
    SqlGenerationContext,
)
from src.ecommerce_agent.sql_safety import SqlSafetyPolicy


def _database(tmp_path):
    path = tmp_path / "day09-workflow.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE fact_orders ("
        "order_id TEXT, customer_id TEXT, order_status TEXT)"
    )
    connection.executemany(
        "INSERT INTO fact_orders VALUES (?, ?, ?)",
        [("o1", "c1", "delivered"), ("o2", "c2", "delivered")],
    )
    connection.commit()
    connection.close()
    return path


def _context():
    global_schema = {
        "fact_orders": {"order_id", "customer_id", "order_status"},
        "dim_customers": {"customer_id", "customer_unique_id"},
    }
    return SqlGenerationContext(
        question="全部订单数",
        analysis_plan={
            "metrics": ["order_count"],
            "dimensions": [],
            "filters": [],
            "time_range": {"mode": "all_data"},
        },
        retrieved_document_ids=("metric:order_count",),
        metric_definitions=(
            {
                "metric_id": "order_count",
                "formula": "COUNT(DISTINCT fact_orders.order_id)",
            },
        ),
        dimension_definitions=(),
        schema_fields=(
            {"qualified_name": "fact_orders.order_id"},
            {"qualified_name": "fact_orders.customer_id"},
            {"qualified_name": "fact_orders.order_status"},
        ),
        table_context=(
            {"table": "fact_orders", "grain": "one order"},
        ),
        parameter_contract={},
        safety_policy=SqlSafetyPolicy(
            global_schema=global_schema,
            plan_schema={"fact_orders": global_schema["fact_orders"]},
        ),
    )


def _response(sql):
    return ModelResponse(
        content=json.dumps({"sql": sql, "parameters": {}}),
        model_name="fake-day09-repair",
    )


def _workflow(database, responses, *, max_repairs=2):
    client = FakeModelClient(
        response=responses[-1],
        scripted_responses=list(responses),
    )
    workflow = Day09RepairWorkflow(
        database_path=database,
        repairer=SqlRepairer(
            client=client,
            config=ModelConfig(model_name="fake-day09-repair"),
        ),
        limits=RepairLimits(max_repair_attempts=max_repairs),
        response_source="fake_model_response",
    )
    return workflow, client


def _initial_query():
    return GeneratedQuery(
        sql=(
            "SELECT count(DISTINCT order_id, customer_id) "
            "FROM fact_orders"
        ),
        parameters={},
    )


def test_one_repair_succeeds_in_real_sqlite(tmp_path):
    workflow, client = _workflow(
        _database(tmp_path),
        [_response("SELECT COUNT(DISTINCT order_id) AS n FROM fact_orders")],
    )

    result = workflow.run(_initial_query(), _context(), run_id="success-run")

    assert result.is_success is True
    assert result.repair_succeeded is True
    assert result.execution.rows == ({"n": 2},)
    assert result.trace.run_id == "success-run"
    assert result.trace.stop_reason == "repair_succeeded"
    assert len(result.trace.attempts) == 2
    assert result.trace.attempts[0].execution_started is True
    assert result.trace.attempts[1].execution_started is True
    assert result.business_validation_status == (
        BusinessValidationStatus.NOT_EVALUATED
    )
    assert len(client.requests) == 1


def test_repair_failures_reach_strict_limit(tmp_path):
    workflow, client = _workflow(
        _database(tmp_path),
        [
            _response(
                "SELECT order_id FROM fact_orders "
                "UNION SELECT order_id, customer_id FROM fact_orders"
            ),
            _response(
                "WITH x(a,b) AS (SELECT order_id FROM fact_orders) "
                "SELECT a FROM x"
            ),
        ],
        max_repairs=2,
    )

    result = workflow.run(_initial_query(), _context())

    assert result.is_success is False
    assert result.trace.stop_reason == "repair_limit_reached"
    assert len(result.trace.attempts) == 3
    assert all(attempt.execution_started for attempt in result.trace.attempts)
    assert len(client.requests) == 2


def test_format_only_repeat_stops_before_second_sqlite_entry(tmp_path):
    workflow, _ = _workflow(
        _database(tmp_path),
        [
            _response(
                " select COUNT(distinct ORDER_ID, CUSTOMER_ID) "
                "from FACT_ORDERS;"
            )
        ],
    )

    result = workflow.run(_initial_query(), _context())

    assert result.trace.stop_reason == "duplicate_candidate"
    assert len(result.trace.attempts) == 2
    assert result.trace.attempts[0].execution_started is True
    assert result.trace.attempts[1].execution_started is False
    assert result.trace.attempts[1].result_summary == {
        "first_seen_sql_attempt": 1
    }


def test_unsafe_repair_candidate_never_enters_sqlite(tmp_path):
    workflow, _ = _workflow(
        _database(tmp_path),
        [_response("DELETE FROM fact_orders")],
    )

    result = workflow.run(_initial_query(), _context())

    assert result.trace.stop_reason == "repair_candidate_safety_rejected"
    assert result.trace.attempts[-1].execution_started is False
    assert result.trace.attempts[-1].safety_trace["accepted"] is False


def test_missing_database_is_not_sent_to_repair_model(tmp_path):
    workflow, client = _workflow(
        tmp_path / "missing.sqlite3",
        [_response("SELECT COUNT(DISTINCT order_id) FROM fact_orders")],
    )
    query = GeneratedQuery(
        sql="SELECT COUNT(DISTINCT order_id) FROM fact_orders",
        parameters={},
    )

    result = workflow.run(query, _context())

    assert result.trace.stop_reason == "environment_error"
    assert result.trace.attempts[0].execution_started is False
    assert len(client.requests) == 0


def test_exhausted_transport_retries_are_traced_without_sql_candidate(
    tmp_path,
):
    fake = FakeModelClient(
        response=_response("SELECT order_id FROM fact_orders"),
        errors_before_response=[
            TransientModelError("synthetic") for _ in range(3)
        ],
    )
    workflow = Day09RepairWorkflow(
        database_path=_database(tmp_path),
        repairer=SqlRepairer(
            client=RetryingModelClient(
                fake,
                RetryPolicy(max_attempts=3, initial_backoff_seconds=0),
            ),
            config=ModelConfig(model_name="fake-day09-repair"),
        ),
        response_source="fake_model_response",
    )

    result = workflow.run(_initial_query(), _context())

    assert result.trace.stop_reason == "transient_model_client_error"
    assert len(result.trace.attempts) == 1
    assert len(result.trace.repair_model_events) == 1
    event = result.trace.repair_model_events[0]
    assert event.status == "failed"
    assert event.http_attempts == 3
