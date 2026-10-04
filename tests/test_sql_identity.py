"""Assistant-authored mechanical tests for SQL repair loop prevention."""

import pytest

from src.ecommerce_agent.sql_identity import (
    SqlCandidateRegistry,
    SqlNormalizationError,
    identify_sql,
)


def test_format_case_and_trailing_semicolon_share_one_identity():
    original = identify_sql(
        "SELECT order_id FROM fact_orders "
        "WHERE order_status = :status"
    )
    reformatted = identify_sql(
        " select  ORDER_ID\nFROM FACT_ORDERS "
        "where ORDER_STATUS=:status; "
    )

    assert original.normalized_sql == (
        "SELECT order_id FROM fact_orders WHERE order_status = :status"
    )
    assert reformatted == original


def test_literal_case_is_not_erased_by_identifier_normalization():
    upper_value = identify_sql(
        "SELECT order_id FROM fact_orders WHERE order_status = 'SP'"
    )
    lower_value = identify_sql(
        "SELECT order_id FROM fact_orders WHERE order_status = 'sp'"
    )

    assert upper_value.sha256 != lower_value.sha256


def test_registry_detects_original_sql_after_an_intermediate_candidate():
    registry = SqlCandidateRegistry()
    first = registry.register(
        "SELECT order_id FROM fact_orders",
        sql_attempt=1,
    )
    second = registry.register(
        "SELECT customer_id FROM fact_orders",
        sql_attempt=2,
    )
    repeated = registry.register(
        " select ORDER_ID from FACT_ORDERS; ",
        sql_attempt=3,
    )

    assert first.is_duplicate is False
    assert second.is_duplicate is False
    assert repeated.is_duplicate is True
    assert repeated.first_seen_sql_attempt == 1
    assert repeated.identity.sha256 == first.identity.sha256


def test_structural_change_is_a_distinct_candidate():
    registry = SqlCandidateRegistry()
    registry.register(
        "SELECT order_id FROM fact_orders",
        sql_attempt=1,
    )
    changed = registry.register(
        "SELECT order_id FROM fact_orders ORDER BY order_id",
        sql_attempt=2,
    )

    assert changed.is_duplicate is False


@pytest.mark.parametrize("sql", ["", "SELECT 1; SELECT 2", "SELECT '"])
def test_identity_requires_one_parseable_statement(sql):
    with pytest.raises(SqlNormalizationError):
        identify_sql(sql)


def test_registry_requires_consecutive_attempt_numbers():
    registry = SqlCandidateRegistry()

    with pytest.raises(ValueError, match="连续递增"):
        registry.register("SELECT 1", sql_attempt=2)
