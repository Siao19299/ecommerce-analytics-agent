import sqlite3
from pathlib import Path

from src.ecommerce_agent.day02_database import create_schema
from src.ecommerce_agent.day03_metrics import (
    execute_named_queries,
    load_named_queries,
)


PROJECT_ROOT = Path(__file__).parents[1]
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
METRICS_SQL_PATH = PROJECT_ROOT / "sql" / "day03_basic_metrics.sql"


def test_five_business_queries_match_manual_sample_calculation():
    connection = sqlite3.connect(":memory:")
    create_schema(connection, str(SCHEMA_PATH))
    connection.executescript(
        """
        INSERT INTO dim_customers VALUES
            ('c001', 'u001', '01001', 'sao paulo', 'SP'),
            ('c002', 'u001', '01001', 'sao paulo', 'SP'),
            ('c003', 'u002', '20001', 'rio de janeiro', 'RJ');

        INSERT INTO dim_products VALUES
            ('p001', 'category_a', 10, 100, 1, 200, 10, 5, 8),
            ('p002', NULL, 10, 100, 1, 200, 10, 5, 8);

        INSERT INTO dim_sellers VALUES
            ('s001', '01001', 'sao paulo', 'SP');

        INSERT INTO fact_orders VALUES
            ('o001', 'c001', 'delivered', '2017-01-01', NULL, NULL, NULL,
             '2017-01-10'),
            ('o002', 'c002', 'delivered', '2017-01-02', NULL, NULL, NULL,
             '2017-01-11'),
            ('o003', 'c003', 'canceled', '2017-01-03', NULL, NULL, NULL,
             '2017-01-12');

        INSERT INTO fact_order_items VALUES
            ('o001', 1, 'p001', 's001', '2017-01-03', 100, 10),
            ('o001', 2, 'p001', 's001', '2017-01-03', 50, 5),
            ('o002', 1, 'p002', 's001', '2017-01-04', 20, 2),
            ('o003', 1, 'p001', 's001', '2017-01-05', 999, 99);
        """
    )

    queries = load_named_queries(METRICS_SQL_PATH)
    results = execute_named_queries(connection, queries)

    assert list(queries) == [
        "delivered_order_count",
        "delivered_gmv",
        "delivered_average_order_value",
        "delivered_customer_count",
        "delivered_category_gmv",
    ]
    assert results["delivered_order_count"]["rows"] == [
        {"delivered_order_count": 2}
    ]
    assert results["delivered_gmv"]["rows"] == [
        {"delivered_gmv": 170.0}
    ]
    assert results["delivered_average_order_value"]["rows"] == [
        {"delivered_average_order_value": 85.0}
    ]
    assert results["delivered_customer_count"]["rows"] == [
        {"delivered_customer_count": 1}
    ]
    assert results["delivered_category_gmv"]["rows"] == [
        {"product_category_name": "category_a", "delivered_gmv": 150.0},
        {"product_category_name": "unknown", "delivered_gmv": 20.0},
    ]
