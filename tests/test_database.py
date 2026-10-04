import sqlite3
from pathlib import Path

from src.ecommerce_agent.database import (
    create_schema,
    import_csv,
)


PROJECT_ROOT = Path(__file__).parents[1]
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
DICTIONARY_PATH = (
    PROJECT_ROOT / "data" / "metadata" / "database_data_dictionary.csv"
)


def test_schema_can_be_executed_twice():
    connection = sqlite3.connect(":memory:")

    create_schema(connection, str(SCHEMA_PATH))
    create_schema(connection, str(SCHEMA_PATH))

    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert table_names == {
        "dim_customers",
        "dim_products",
        "dim_sellers",
        "fact_orders",
        "fact_order_items",
        "fact_payments",
    }


def test_small_csv_import_is_repeatable(tmp_path):
    samples = {
        "dim_customers": (
            "customer_id,customer_unique_id,customer_zip_code_prefix,"
            "customer_city,customer_state\n"
            "c001,u001,01001,sao paulo,SP\n"
        ),
        "dim_products": (
            "product_id,product_category_name,product_name_lenght,"
            "product_description_lenght,product_photos_qty,product_weight_g,"
            "product_length_cm,product_height_cm,product_width_cm\n"
            "p001,beleza_saude,10,100,1,200,10,5,8\n"
        ),
        "dim_sellers": (
            "seller_id,seller_zip_code_prefix,seller_city,seller_state\n"
            "s001,01001,sao paulo,SP\n"
        ),
        "fact_orders": (
            "order_id,customer_id,order_status,order_purchase_timestamp,"
            "order_approved_at,order_delivered_carrier_date,"
            "order_delivered_customer_date,order_estimated_delivery_date\n"
            "o001,c001,delivered,2017-01-01 10:00:00,"
            "2017-01-01 11:00:00,2017-01-02 09:00:00,"
            "2017-01-05 12:00:00,2017-01-10 00:00:00\n"
        ),
        "fact_order_items": (
            "order_id,order_item_id,product_id,seller_id,shipping_limit_date,"
            "price,freight_value\n"
            "o001,1,p001,s001,2017-01-03 00:00:00,100.00,10.00\n"
        ),
        "fact_payments": (
            "order_id,payment_sequential,payment_type,payment_installments,"
            "payment_value\n"
            "o001,1,credit_card,1,110.00\n"
        ),
    }

    connection = sqlite3.connect(":memory:")
    create_schema(connection, str(SCHEMA_PATH))

    sample_paths = {}
    for table_name, content in samples.items():
        sample_path = tmp_path / f"{table_name}.csv"
        sample_path.write_text(content, encoding="utf-8", newline="")
        sample_paths[table_name] = sample_path

    first_import_counts = {
        table_name: import_csv(
            connection,
            str(sample_paths[table_name]),
            table_name,
        )
        for table_name in samples
    }
    second_import_counts = {
        table_name: import_csv(
            connection,
            str(sample_paths[table_name]),
            table_name,
        )
        for table_name in samples
    }

    assert first_import_counts == {
        table_name: 1 for table_name in samples
    }
    assert second_import_counts == {
        table_name: 0 for table_name in samples
    }

    for table_name in samples:
        row_count = connection.execute(
            f'SELECT COUNT(*) FROM "{table_name}"'
        ).fetchone()[0]
        assert row_count == 1


def test_database_dictionary_matches_import_columns():
    import csv

    from src.ecommerce_agent.database import TABLE_COLUMNS

    with open(
        DICTIONARY_PATH,
        mode="r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        rows = list(csv.DictReader(file))

    dictionary_columns = {
        table_name: tuple(
            row["column_name"]
            for row in rows
            if row["table_name"] == table_name
        )
        for table_name in TABLE_COLUMNS
    }

    assert dictionary_columns == TABLE_COLUMNS
    assert len(rows) == 38


def test_wrong_join_inflates_order_and_amount_metrics():
    connection = sqlite3.connect(":memory:")
    create_schema(connection, str(SCHEMA_PATH))

    connection.execute(
        "INSERT INTO dim_customers VALUES (?, ?, ?, ?, ?)",
        ("c001", "u001", "01001", "sao paulo", "SP"),
    )
    connection.execute(
        "INSERT INTO dim_products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("p001", "category", 10, 100, 1, 200, 10, 5, 8),
    )
    connection.execute(
        "INSERT INTO dim_sellers VALUES (?, ?, ?, ?)",
        ("s001", "01001", "sao paulo", "SP"),
    )
    connection.execute(
        "INSERT INTO fact_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "o001",
            "c001",
            "delivered",
            "2017-01-01 10:00:00",
            None,
            None,
            None,
            "2017-01-10 00:00:00",
        ),
    )
    connection.executemany(
        "INSERT INTO fact_order_items VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("o001", 1, "p001", "s001", "2017-01-03", 100, 10),
            ("o001", 2, "p001", "s001", "2017-01-03", 50, 5),
        ],
    )
    connection.executemany(
        "INSERT INTO fact_payments VALUES (?, ?, ?, ?, ?)",
        [
            ("o001", 1, "credit_card", 1, 120),
            ("o001", 2, "voucher", 1, 40),
        ],
    )

    wrong_result = connection.execute(
        """
        SELECT
            COUNT(o.order_id),
            SUM(i.price),
            SUM(p.payment_value)
        FROM fact_orders AS o
        JOIN fact_order_items AS i ON o.order_id = i.order_id
        JOIN fact_payments AS p ON o.order_id = p.order_id
        """
    ).fetchone()

    correct_result = connection.execute(
        """
        WITH item_totals AS (
            SELECT order_id, SUM(price) AS gmv
            FROM fact_order_items
            GROUP BY order_id
        ),
        payment_totals AS (
            SELECT order_id, SUM(payment_value) AS payment_amount
            FROM fact_payments
            GROUP BY order_id
        )
        SELECT COUNT(o.order_id), i.gmv, p.payment_amount
        FROM fact_orders AS o
        LEFT JOIN item_totals AS i ON o.order_id = i.order_id
        LEFT JOIN payment_totals AS p ON o.order_id = p.order_id
        GROUP BY o.order_id, i.gmv, p.payment_amount
        """
    ).fetchone()

    assert wrong_result == (4, 300, 320)
    assert correct_result == (1, 150, 160)


def test_import_does_not_hide_check_constraint_errors(tmp_path):
    connection = sqlite3.connect(":memory:")
    create_schema(connection, str(SCHEMA_PATH))
    connection.execute(
        "INSERT INTO dim_customers VALUES (?, ?, ?, ?, ?)",
        ("c001", "u001", "01001", "sao paulo", "SP"),
    )

    csv_path = tmp_path / "fact_payments.csv"
    csv_path.write_text(
        "order_id,payment_sequential,payment_type,"
        "payment_installments,payment_value\n"
        "missing-order,1,credit_card,1,-10.00\n",
        encoding="utf-8",
        newline="",
    )

    try:
        import_csv(connection, str(csv_path), "fact_payments")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("无效金额或孤儿外键不应被静默忽略")
