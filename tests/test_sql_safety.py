import sqlite3
from pathlib import Path

import pytest

from src.ecommerce_agent.sql_generation import (
    QueryExecutionErrorType,
    execute_read_only_query,
)
from src.ecommerce_agent.sql_security_benchmark import load_cases
from src.ecommerce_agent.sql_safety import (
    SqlSafetyErrorCode,
    SqlSafetyPolicy,
    build_global_sql_policy,
    validate_sql_safety,
)


ROOT = Path(__file__).parents[1]


@pytest.fixture
def policy():
    global_schema = {
        "fact_orders": {
            "order_id",
            "customer_id",
            "order_status",
        },
        "dim_customers": {
            "customer_id",
            "customer_unique_id",
        },
        "fact_payments": {
            "order_id",
            "payment_value",
        },
    }
    return SqlSafetyPolicy(
        global_schema=global_schema,
        plan_schema={
            "fact_orders": global_schema["fact_orders"],
            "dim_customers": global_schema["dim_customers"],
        },
        max_rows=2,
        timeout_seconds=0.5,
        progress_handler_steps=100,
    )


def test_real_global_policy_has_six_tables_and_38_fields():
    actual = build_global_sql_policy(ROOT)

    assert len(actual.global_schema) == 6
    assert sum(map(len, actual.global_schema.values())) == 38
    assert actual.global_schema == actual.plan_schema


def test_fixture_has_at_least_ten_transparently_sourced_categories():
    source, cases = load_cases(
        ROOT / "tests/fixtures/security/security_cases.json"
    )

    assert source == (
        "assistant_authored_mechanical_security_cases_from_day8_requirements"
    )
    assert len(cases) == 16
    assert {case["category"] for case in cases} >= {
        "empty_sql",
        "parse_failure",
        "multiple_statements",
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "unknown_field",
        "row_limit_and_wildcard",
        "timeout_despite_limit",
    }


@pytest.mark.parametrize(
    ("sql", "error_code"),
    [
        ("", SqlSafetyErrorCode.EMPTY_SQL),
        ("/* comment only */", SqlSafetyErrorCode.EMPTY_SQL),
        ("SELECT 'unterminated", SqlSafetyErrorCode.PARSE_ERROR),
        ("SELECT 1; SELECT 2", SqlSafetyErrorCode.MULTIPLE_STATEMENTS),
        (
            "INSERT INTO fact_orders(order_id) VALUES ('o1')",
            SqlSafetyErrorCode.NON_QUERY,
        ),
        (
            "UPDATE fact_orders SET order_status = 'x'",
            SqlSafetyErrorCode.NON_QUERY,
        ),
        ("DELETE FROM fact_orders", SqlSafetyErrorCode.NON_QUERY),
        ("DROP TABLE fact_orders", SqlSafetyErrorCode.NON_QUERY),
        (
            "ALTER TABLE fact_orders ADD COLUMN secret TEXT",
            SqlSafetyErrorCode.NON_QUERY,
        ),
        ("PRAGMA query_only = OFF", SqlSafetyErrorCode.NON_QUERY),
        (
            "ATTACH DATABASE 'other.db' AS other",
            SqlSafetyErrorCode.NON_QUERY,
        ),
        (
            "SELECT order_id FROM fact_orders WHERE customer_id = ?",
            SqlSafetyErrorCode.PARAMETER_STYLE_DENIED,
        ),
    ],
)
def test_empty_parse_multi_dml_ddl_and_sqlite_commands_default_deny(
    policy,
    sql,
    error_code,
):
    result = validate_sql_safety(sql, policy)

    assert result.is_safe is False
    assert result.trace.error_code == error_code
    assert result.trace.error_message


def test_nested_mutation_is_rejected_even_when_outer_root_is_select(policy):
    sql = (
        "WITH changed AS ("
        "DELETE FROM fact_orders RETURNING order_id"
        ") SELECT * FROM changed"
    )

    result = validate_sql_safety(sql, policy)

    assert result.trace.error_code == SqlSafetyErrorCode.FORBIDDEN_OPERATION
    assert result.trace.root_expression == "Select"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT load_extension('unsafe')",
        "SELECT readfile('data/raw/archive.zip')",
        "SELECT * FROM pragma_table_info('fact_orders')",
    ],
)
def test_extension_file_and_table_valued_functions_are_rejected(policy, sql):
    result = validate_sql_safety(sql, policy)

    assert result.is_safe is False
    assert result.trace.error_code in {
        SqlSafetyErrorCode.FORBIDDEN_FUNCTION,
        SqlSafetyErrorCode.UNSUPPORTED_SOURCE,
    }


def test_global_and_plan_table_scopes_are_distinct(policy):
    outside_global = validate_sql_safety(
        "SELECT name FROM sqlite_master",
        policy,
    )
    inside_global_but_outside_plan = validate_sql_safety(
        "SELECT payment_value FROM fact_payments",
        policy,
    )

    assert (
        outside_global.trace.error_code
        == SqlSafetyErrorCode.GLOBAL_TABLE_DENIED
    )
    assert (
        inside_global_but_outside_plan.trace.error_code
        == SqlSafetyErrorCode.PLAN_TABLE_DENIED
    )


def test_alias_cte_and_columns_are_resolved_to_physical_sources(policy):
    sql = (
        "WITH delivered AS ("
        "SELECT o.customer_id AS cid FROM fact_orders AS o "
        "WHERE o.order_status = 'delivered'"
        ") "
        "SELECT d.cid, c.customer_unique_id "
        "FROM delivered AS d "
        "JOIN dim_customers AS c ON c.customer_id = d.cid"
    )

    result = validate_sql_safety(sql, policy)

    assert result.is_safe is True
    assert set(result.trace.referenced_tables) == {
        "fact_orders",
        "dim_customers",
    }
    assert set(result.trace.referenced_columns) >= {
        "fact_orders.customer_id",
        "fact_orders.order_status",
        "dim_customers.customer_id",
        "dim_customers.customer_unique_id",
    }


def test_unknown_and_ambiguous_columns_are_rejected_before_sqlite(policy):
    unknown = validate_sql_safety(
        "SELECT missing FROM fact_orders",
        policy,
    )
    ambiguous = validate_sql_safety(
        "SELECT customer_id FROM fact_orders "
        "JOIN dim_customers ON fact_orders.customer_id = "
        "dim_customers.customer_id",
        policy,
    )

    assert (
        unknown.trace.error_code
        == SqlSafetyErrorCode.COLUMN_RESOLUTION_FAILED
    )
    assert (
        ambiguous.trace.error_code
        == SqlSafetyErrorCode.COLUMN_RESOLUTION_FAILED
    )


def test_plan_field_scope_and_wildcard_expansion(policy):
    field_policy = SqlSafetyPolicy(
        global_schema={"fact_orders": {"order_id", "order_status"}},
        plan_schema={"fact_orders": {"order_id"}},
    )
    allowed = validate_sql_safety(
        "SELECT o.order_id FROM fact_orders AS o",
        field_policy,
    )
    denied_field = validate_sql_safety(
        "SELECT o.order_status FROM fact_orders AS o",
        field_policy,
    )
    denied_star = validate_sql_safety(
        "SELECT o.* FROM fact_orders AS o",
        field_policy,
    )

    assert allowed.is_safe is True
    assert (
        denied_field.trace.error_code
        == SqlSafetyErrorCode.PLAN_COLUMN_DENIED
    )
    assert (
        denied_star.trace.error_code
        == SqlSafetyErrorCode.PLAN_COLUMN_DENIED
    )
    assert "fact_orders.order_status" in denied_star.trace.referenced_columns


def _sample_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE fact_orders ("
        "order_id TEXT, customer_id TEXT, order_status TEXT)"
    )
    connection.executemany(
        "INSERT INTO fact_orders VALUES (?, ?, ?)",
        [
            ("o1", "c1", "delivered"),
            ("o2", "c2", "delivered"),
            ("o3", "c3", "canceled"),
        ],
    )
    connection.commit()
    connection.close()


def test_executor_caps_returned_rows_and_records_resource_policy(
    tmp_path,
    policy,
):
    database = tmp_path / "rows.sqlite3"
    _sample_database(database)

    result = execute_read_only_query(
        database,
        "SELECT order_id FROM fact_orders ORDER BY order_id",
        safety_policy=policy,
    )

    assert result.is_success is True
    assert len(result.rows) == 2
    assert result.rows_truncated is True
    assert result.row_limit == 2
    assert result.execution_started is True


def test_dangerous_query_is_actually_rejected_before_execute_and_data_stays(
    tmp_path,
    policy,
):
    database = tmp_path / "readonly.sqlite3"
    _sample_database(database)

    result = execute_read_only_query(
        database,
        "DELETE FROM fact_orders",
        safety_policy=policy,
    )

    assert result.error_type == QueryExecutionErrorType.SAFETY
    assert result.execution_started is False
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM fact_orders").fetchone() == (
        3,
    )
    connection.close()


def test_executor_rejects_database_schema_drift_before_star_can_leak(tmp_path):
    database = tmp_path / "drift.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample (id INTEGER, secret TEXT)")
    connection.execute("INSERT INTO sample VALUES (1, 'hidden')")
    connection.commit()
    connection.close()
    policy = SqlSafetyPolicy(
        global_schema={"sample": {"id"}},
        plan_schema={"sample": {"id"}},
    )

    result = execute_read_only_query(
        database,
        "SELECT * FROM sample",
        safety_policy=policy,
    )

    assert result.error_type == QueryExecutionErrorType.SAFETY
    assert result.execution_started is False
    assert result.safety_trace.error_code == (
        SqlSafetyErrorCode.DATABASE_SCHEMA_MISMATCH
    )
    assert result.rows == ()


def test_query_timeout_interrupts_work_even_when_limit_is_one(tmp_path):
    database = tmp_path / "timeout.sqlite3"
    _sample_database(database)
    timeout_policy = SqlSafetyPolicy(
        global_schema={
            "fact_orders": {"order_id", "customer_id", "order_status"}
        },
        plan_schema={
            "fact_orders": {"order_id", "customer_id", "order_status"}
        },
        max_rows=1,
        timeout_seconds=0.001,
        progress_handler_steps=1,
    )
    expensive_query_with_limit = """
        WITH RECURSIVE counter(x) AS (
            SELECT 1
            UNION ALL
            SELECT x + 1 FROM counter WHERE x < 100000000
        )
        SELECT x FROM counter ORDER BY x DESC LIMIT 1
    """

    result = execute_read_only_query(
        database,
        expensive_query_with_limit,
        safety_policy=timeout_policy,
    )

    assert result.error_type == QueryExecutionErrorType.TIMEOUT
    assert result.execution_started is True
    assert result.rows == ()
