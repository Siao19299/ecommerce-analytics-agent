import sqlite3

import pandas as pd

from src.ecommerce_agent.data_quality import (
    inspect_order_date_sequence,
    profile_table,
)


def test_profile_table_checks_counts_keys_numeric_and_dates(tmp_path):
    csv_path = tmp_path / "facts.csv"
    csv_path.write_text(
        "id,price,event_time,note\n"
        "a,10,2020-01-01,ok\n"
        "a,-2,not-a-date,\n"
        "b,bad,,ok\n",
        encoding="utf-8",
        newline="",
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE facts (id TEXT, price TEXT, event_time TEXT, note TEXT)"
    )
    connection.executemany(
        "INSERT INTO facts VALUES (?, ?, ?, ?)",
        [
            ("a", "10", "2020-01-01", "ok"),
            ("a", "-2", "not-a-date", None),
            ("b", "bad", None, "ok"),
        ],
    )

    profile, _ = profile_table(
        csv_path,
        connection,
        "facts",
        primary_key=("id",),
        numeric_columns=("price",),
        date_columns=("event_time",),
    )

    assert profile["csv_row_count"] == 3
    assert profile["database_row_count"] == 3
    assert profile["duplicate_key_row_count"] == 1
    assert profile["missing_by_column"]["note"] == 1
    assert profile["numeric_profiles"][0] == {
        "column": "price",
        "missing_count": 0,
        "invalid_count": 1,
        "negative_count": 1,
        "minimum": -2,
        "maximum": 10,
    }
    assert profile["date_profiles"][0]["missing_count"] == 1
    assert profile["date_profiles"][0]["invalid_count"] == 1


def test_order_date_sequence_counts_only_comparable_violations():
    orders = pd.DataFrame(
        {
            "order_id": ["o001", "o002"],
            "order_purchase_timestamp": ["2020-01-02", "2020-01-01"],
            "order_approved_at": ["2020-01-01", None],
            "order_delivered_carrier_date": ["2020-01-03", None],
            "order_delivered_customer_date": ["2020-01-02", None],
            "order_estimated_delivery_date": ["2020-01-10", "2020-01-10"],
        }
    )

    checks = {
        check["rule"]: check
        for check in inspect_order_date_sequence(orders)
    }

    assert checks["approved_before_purchase"]["violation_count"] == 1
    assert checks["approved_before_purchase"]["comparable_row_count"] == 1
    assert checks["approved_before_purchase"]["example_order_ids"] == [
        "o001"
    ]
    assert checks["customer_delivery_before_carrier"]["violation_count"] == 1
