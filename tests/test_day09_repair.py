"""Assistant-authored mechanical tests for controlled Day 9 repair."""

import json

from src.ecommerce_agent.day09_repair import (
    REPAIR_CONTEXT_SOURCE,
    SqlRepairContext,
    SqlRepairErrorType,
    SqlRepairer,
)
from src.ecommerce_agent.day09_sql_identity import identify_sql
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
)
from src.ecommerce_agent.sql_generation import SqlGenerationContext
from src.ecommerce_agent.sql_safety import (
    SqlSafetyErrorCode,
    SqlSafetyPolicy,
)


def _generation_context() -> SqlGenerationContext:
    global_schema = {
        "fact_orders": {"order_id", "order_status"},
        "dim_customers": {"customer_id", "customer_unique_id"},
    }
    return SqlGenerationContext(
        question="不应再次发送给修复模型的问题原文",
        analysis_plan={
            "metrics": ["delivered_order_count"],
            "dimensions": [],
            "filters": [],
            "time_range": {"mode": "all_data"},
        },
        retrieved_document_ids=("metric:delivered_order_count",),
        metric_definitions=(
            {
                "metric_id": "delivered_order_count",
                "formula": "COUNT(DISTINCT fact_orders.order_id)",
            },
        ),
        dimension_definitions=(),
        schema_fields=(
            {"qualified_name": "fact_orders.order_id"},
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


def _repair_context() -> SqlRepairContext:
    sql = (
        "SELECT order_id FROM fact_orders "
        "UNION SELECT order_id, order_status FROM fact_orders"
    )
    identity = identify_sql(sql)
    return SqlRepairContext(
        source_sql_attempt=1,
        original_sql=sql,
        original_identity=identity,
        database_error_rule="sqlite_compound_select_column_count",
        sanitized_database_error=(
            "SELECTs to the left and right of UNION do not have the same "
            "number of result columns"
        ),
        attempted_sql_hashes=(identity.sha256,),
        generation_context=_generation_context(),
    )


def _repairer(payload: dict):
    client = FakeModelClient(
        ModelResponse(
            content=json.dumps(payload),
            model_name="fake-day09-repair",
        )
    )
    return (
        SqlRepairer(
            client=client,
            config=ModelConfig(model_name="fake-day09-repair"),
        ),
        client,
    )


def test_controlled_prompt_exposes_plan_scope_not_question_or_global_schema():
    repairer, client = _repairer(
        {
            "sql": "SELECT order_id FROM fact_orders",
            "parameters": {},
        }
    )

    result = repairer.repair(_repair_context())
    system = client.requests[0][0][0].content

    assert result.is_success is True
    assert REPAIR_CONTEXT_SOURCE in system
    assert "sqlite_compound_select_column_count" in system
    assert "不应再次发送给修复模型的问题原文" not in system
    assert "dim_customers" not in system
    assert "不得增加或替换指标" in system


def test_globally_allowed_but_plan_denied_table_is_rejected_again():
    repairer, _ = _repairer(
        {
            "sql": (
                "SELECT c.customer_unique_id "
                "FROM dim_customers AS c"
            ),
            "parameters": {},
        }
    )

    result = repairer.repair(_repair_context())

    assert result.error_type == SqlRepairErrorType.UNSAFE_CANDIDATE
    assert result.safety_trace.error_code == (
        SqlSafetyErrorCode.PLAN_TABLE_DENIED
    )


def test_repair_candidate_cannot_change_parameter_contract():
    context = _repair_context()
    generation = context.generation_context
    parameterized_generation = SqlGenerationContext(
        question=generation.question,
        analysis_plan=generation.analysis_plan,
        retrieved_document_ids=generation.retrieved_document_ids,
        metric_definitions=generation.metric_definitions,
        dimension_definitions=generation.dimension_definitions,
        schema_fields=generation.schema_fields,
        table_context=generation.table_context,
        parameter_contract={"status": "delivered"},
        safety_policy=generation.safety_policy,
    )
    context = SqlRepairContext(
        source_sql_attempt=context.source_sql_attempt,
        original_sql=context.original_sql,
        original_identity=context.original_identity,
        database_error_rule=context.database_error_rule,
        sanitized_database_error=context.sanitized_database_error,
        attempted_sql_hashes=context.attempted_sql_hashes,
        generation_context=parameterized_generation,
    )
    repairer, _ = _repairer(
        {
            "sql": "SELECT order_id FROM fact_orders",
            "parameters": {},
        }
    )

    result = repairer.repair(context)

    assert result.error_type == SqlRepairErrorType.PARAMETER_MISMATCH


def test_write_candidate_is_rejected_by_same_day8_policy():
    repairer, _ = _repairer(
        {"sql": "DELETE FROM fact_orders", "parameters": {}}
    )

    result = repairer.repair(_repair_context())

    assert result.error_type == SqlRepairErrorType.UNSAFE_CANDIDATE
    assert result.safety_trace.error_code == SqlSafetyErrorCode.NON_QUERY


def test_repair_error_path_is_sanitized_before_prompt():
    context = _repair_context()
    with_path = SqlRepairContext(
        source_sql_attempt=context.source_sql_attempt,
        original_sql=context.original_sql,
        original_identity=context.original_identity,
        database_error_rule=context.database_error_rule,
        sanitized_database_error=(
            "unable to open C:\\Users\\person\\private\\olist.sqlite3"
        ),
        attempted_sql_hashes=context.attempted_sql_hashes,
        generation_context=context.generation_context,
    )
    repairer, client = _repairer(
        {"sql": "SELECT order_id FROM fact_orders", "parameters": {}}
    )

    repairer.repair(with_path)
    system = client.requests[0][0][0].content

    assert "C:\\Users" not in system
    assert "<database-path>" in system
