"""Assistant-authored mechanical tests derived from the SQL repair requirements."""

import sqlite3
from pathlib import Path

import pytest

from src.ecommerce_agent.execution_errors import (
    RepairEligibilityCategory,
    classify_repair_eligibility,
)
from src.ecommerce_agent.sql_generation import execute_read_only_query
from src.ecommerce_agent.sql_safety import SqlSafetyPolicy


@pytest.fixture
def guarded_database(tmp_path: Path):
    path = tmp_path / "error-classification.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE fact_orders (order_id TEXT, customer_id TEXT)"
    )
    connection.execute("INSERT INTO fact_orders VALUES ('o1', 'c1')")
    connection.commit()
    connection.close()
    policy = SqlSafetyPolicy(
        global_schema={"fact_orders": {"order_id", "customer_id"}},
        plan_schema={"fact_orders": {"order_id", "customer_id"}},
        timeout_seconds=0.5,
        progress_handler_steps=1,
    )
    return path, policy


@pytest.mark.parametrize(
    ("sql", "expected_rule"),
    [
        (
            "SELECT missing_function(order_id) FROM fact_orders LIMIT 1",
            "sqlite_missing_scalar_function",
        ),
        (
            "SELECT count(DISTINCT order_id, customer_id) "
            "FROM fact_orders",
            "sqlite_function_argument_count",
        ),
        (
            "SELECT order_id FROM fact_orders "
            "UNION SELECT order_id, customer_id FROM fact_orders",
            "sqlite_compound_select_column_count",
        ),
        (
            "WITH x(a,b) AS (SELECT order_id FROM fact_orders) "
            "SELECT a FROM x",
            "sqlite_cte_column_count",
        ),
        (
            "SELECT order_id LIKE 'x' ESCAPE 'ab' FROM fact_orders",
            "sqlite_escape_width",
        ),
    ],
)
def test_real_sqlite_errors_after_safety_are_narrowly_repairable(
    guarded_database,
    sql,
    expected_rule,
):
    path, policy = guarded_database

    execution = execute_read_only_query(
        path,
        sql,
        safety_policy=policy,
    )
    decision = classify_repair_eligibility(execution)

    assert execution.execution_started is True
    assert decision.eligible is True
    assert decision.category == (
        RepairEligibilityCategory.REPAIRABLE_SQL_ERROR
    )
    assert decision.rule_id == expected_rule


def test_safety_rejection_never_enters_repair(guarded_database):
    path, policy = guarded_database

    execution = execute_read_only_query(
        path,
        "SELECT missing_column FROM fact_orders",
        safety_policy=policy,
    )
    decision = classify_repair_eligibility(execution)

    assert execution.execution_started is False
    assert decision.category == RepairEligibilityCategory.SAFETY_FAILURE
    assert decision.eligible is False


def test_timeout_never_enters_repair(guarded_database):
    path, policy = guarded_database
    timeout_policy = SqlSafetyPolicy(
        global_schema=policy.global_schema,
        plan_schema=policy.plan_schema,
        timeout_seconds=0.000001,
        progress_handler_steps=1,
    )
    sql = """
        WITH RECURSIVE counter(x) AS (
            SELECT 1
            UNION ALL
            SELECT x + 1 FROM counter WHERE x < 100000000
        )
        SELECT x FROM counter ORDER BY x DESC LIMIT 1
    """

    execution = execute_read_only_query(
        path,
        sql,
        safety_policy=timeout_policy,
    )
    decision = classify_repair_eligibility(execution)

    assert execution.execution_started is True
    assert decision.category == RepairEligibilityCategory.RESOURCE_FAILURE
    assert decision.eligible is False


def test_missing_database_is_environment_error(guarded_database, tmp_path):
    _, policy = guarded_database

    execution = execute_read_only_query(
        tmp_path / "missing.sqlite3",
        "SELECT order_id FROM fact_orders",
        safety_policy=policy,
    )
    decision = classify_repair_eligibility(execution)

    assert execution.execution_started is False
    assert decision.category == RepairEligibilityCategory.ENVIRONMENT_ERROR
    assert decision.eligible is False


def test_data_dependent_database_error_defaults_to_no_repair(
    guarded_database,
):
    path, policy = guarded_database

    execution = execute_read_only_query(
        path,
        "SELECT json_extract(order_id, 'badpath') FROM fact_orders",
        safety_policy=policy,
    )
    decision = classify_repair_eligibility(execution)

    assert execution.execution_started is True
    assert execution.error_message == "malformed JSON"
    assert decision.category == (
        RepairEligibilityCategory.UNCLASSIFIED_DATABASE_ERROR
    )
    assert decision.eligible is False
