import sqlite3
from pathlib import Path

import pytest

from src.ecommerce_agent.database import create_schema
from src.ecommerce_agent.basic_metrics import (
    execute_named_queries,
    load_named_queries,
)


PROJECT_ROOT = Path(__file__).parents[1]
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
STANDARD_SQL_PATH = PROJECT_ROOT / "sql" / "standard_metrics.sql"
ADVANCED_SQL_PATH = PROJECT_ROOT / "sql" / "advanced_metrics.sql"


def build_manual_sample() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    create_schema(connection, str(SCHEMA_PATH))

    connection.executemany(
        "INSERT INTO dim_customers VALUES (?, ?, ?, ?, ?)",
        [
            (f"c{index}", f"u{index}", "01001", "city", "SP")
            for index in range(1, 8)
        ],
    )
    connection.executemany(
        "INSERT INTO dim_products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (f"p{index}", category, 10, 100, 1, 200, 10, 5, 8)
            for index, category in enumerate("ABCD", start=1)
        ],
    )
    connection.execute(
        "INSERT INTO dim_sellers VALUES (?, ?, ?, ?)",
        ("s1", "01001", "city", "SP"),
    )

    order_specs = [
        ("o2017", "c1", "2017-01-01", "2017-01-03", "2017-01-05"),
        ("oa", "c2", "2018-01-01", "2018-01-03", "2018-01-05"),
        ("ob", "c3", "2018-01-02", "2018-01-04", "2018-01-06"),
        ("oc", "c4", "2018-01-03", "2018-01-05", "2018-01-07"),
        ("od", "c5", "2018-01-04", "2018-01-06", "2018-01-08"),
        ("oe", "c6", "2018-02-01", "2018-02-03", "2018-02-05"),
        ("of", "c7", "2018-02-02", "2018-02-04", "2018-02-06"),
    ]
    connection.executemany(
        """
        INSERT INTO fact_orders VALUES (?, ?, 'delivered', ?, ?, ?, ?, ?)
        """,
        [
            (
                order_id,
                customer_id,
                purchase_date,
                purchase_date,
                purchase_date,
                delivered_date,
                estimated_date,
            )
            for (
                order_id,
                customer_id,
                purchase_date,
                delivered_date,
                estimated_date,
            ) in order_specs
        ],
    )
    connection.executemany(
        "INSERT INTO fact_order_items VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("o2017", 1, "p1", "s1", "2017-01-02", 100, 10),
            ("oa", 1, "p1", "s1", "2018-01-02", 60, 6),
            ("oa", 2, "p1", "s1", "2018-01-02", 40, 4),
            ("ob", 1, "p2", "s1", "2018-01-03", 90, 9),
            ("oc", 1, "p3", "s1", "2018-01-04", 90, 9),
            ("od", 1, "p4", "s1", "2018-01-05", 80, 8),
            ("oe", 1, "p1", "s1", "2018-02-02", 40, 4),
            ("of", 1, "p2", "s1", "2018-02-03", 60, 6),
        ],
    )
    connection.executemany(
        "INSERT INTO fact_payments VALUES (?, ?, ?, ?, ?)",
        [
            ("o2017", 1, "credit_card", 1, 110),
            ("oa", 1, "credit_card", 1, 50),
            ("oa", 2, "voucher", 1, 60),
            ("ob", 1, "credit_card", 1, 99),
            ("oc", 1, "credit_card", 1, 99),
            ("od", 1, "credit_card", 1, 88),
            ("oe", 1, "credit_card", 1, 44),
            ("of", 1, "credit_card", 1, 66),
        ],
    )
    return connection


def test_ten_standard_metrics_match_manual_sample():
    connection = build_manual_sample()
    queries = load_named_queries(STANDARD_SQL_PATH)
    results = execute_named_queries(connection, queries)

    assert len(queries) == 10
    expected = {
        "standard_delivered_order_count": 7,
        "standard_delivered_gmv": 560.0,
        "standard_delivered_gmv_including_freight": 616.0,
        "standard_delivered_payment_amount": 616.0,
        "standard_delivered_customer_count": 7,
        "standard_delivered_average_order_value": 80.0,
        "standard_delivered_freight_amount": 56.0,
        "standard_terminal_cancellation_rate": 0.0,
        "standard_on_time_delivery_rate": 1.0,
        "standard_average_order_delivery_days": 2.0,
    }
    actual = {
        name: next(iter(result["rows"][0].values()))
        for name, result in results.items()
    }
    assert actual == expected


def test_five_advanced_queries_match_manual_sample_and_grain_rules():
    connection = build_manual_sample()
    queries = load_named_queries(ADVANCED_SQL_PATH)
    results = execute_named_queries(connection, queries)

    assert list(queries) == [
        "monthly_gmv_mom",
        "monthly_gmv_yoy",
        "monthly_category_top3",
        "monthly_category_gmv_contribution",
        "monthly_gmv_payment_reconciliation",
    ]

    mom_rows = {
        row["purchase_month"]: row
        for row in results["monthly_gmv_mom"]["rows"]
    }
    assert mom_rows["2018-01-01"]["mom_rate"] is None
    assert mom_rows["2018-02-01"]["mom_rate"] == pytest.approx(-0.722222)

    yoy_rows = {
        row["purchase_month"]: row
        for row in results["monthly_gmv_yoy"]["rows"]
    }
    assert yoy_rows["2018-01-01"]["previous_year_month"] == "2017-01-01"
    assert yoy_rows["2018-01-01"]["yoy_rate"] == pytest.approx(2.6)
    assert yoy_rows["2018-02-01"]["yoy_rate"] is None

    top_rows = [
        row
        for row in results["monthly_category_top3"]["rows"]
        if row["purchase_month"] == "2018-01-01"
    ]
    assert [(row["product_category_name"], row["category_rank"])
            for row in top_rows] == [
        ("A", 1),
        ("B", 2),
        ("C", 2),
        ("D", 3),
    ]

    contribution_rows = results[
        "monthly_category_gmv_contribution"
    ]["rows"]
    for month in {row["purchase_month"] for row in contribution_rows}:
        month_total = sum(
            row["category_gmv_contribution"]
            for row in contribution_rows
            if row["purchase_month"] == month
        )
        assert month_total == pytest.approx(1.0, abs=0.000002)

    reconciliation_rows = {
        row["purchase_month"]: row
        for row in results["monthly_gmv_payment_reconciliation"]["rows"]
    }
    assert reconciliation_rows["2018-01-01"] == {
        "purchase_month": "2018-01-01",
        "monthly_gmv": 360.0,
        "monthly_freight_amount": 36.0,
        "monthly_payment_amount": 396.0,
        "monthly_gmv_including_freight": 396.0,
        "payment_minus_gmv": 36.0,
        "payment_reconciliation_difference": 0.0,
    }

    wrong_join = connection.execute(
        """
        SELECT SUM(i.price), SUM(p.payment_value)
        FROM fact_orders AS o
        JOIN fact_order_items AS i ON o.order_id = i.order_id
        JOIN fact_payments AS p ON o.order_id = p.order_id
        WHERE o.order_status = 'delivered'
          AND date(o.order_purchase_timestamp, 'start of month')
              = '2018-01-01'
        """
    ).fetchone()
    assert wrong_join == (460, 506)
    assert wrong_join != (360, 396)
