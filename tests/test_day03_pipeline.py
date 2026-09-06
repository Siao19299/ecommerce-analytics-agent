import sqlite3
from pathlib import Path

from src.ecommerce_agent.day02_database import create_schema
from src.ecommerce_agent.day03_pipeline import (
    CORE_TABLE_IMPORTS,
    import_core_tables,
)


PROJECT_ROOT = Path(__file__).parents[1]
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"


def test_core_table_import_order_follows_foreign_keys():
    assert [table_name for table_name, _ in CORE_TABLE_IMPORTS] == [
        "dim_customers",
        "dim_products",
        "dim_sellers",
        "fact_orders",
        "fact_order_items",
        "fact_payments",
    ]


def test_import_core_tables_matches_csv_and_database_counts(tmp_path):
    csv_contents = {
        "olist_customers_dataset.csv": (
            "customer_id,customer_unique_id,customer_zip_code_prefix,"
            "customer_city,customer_state\n"
            "c001,u001,01001,sao paulo,SP\n"
        ),
        "olist_products_dataset.csv": (
            "product_id,product_category_name,product_name_lenght,"
            "product_description_lenght,product_photos_qty,product_weight_g,"
            "product_length_cm,product_height_cm,product_width_cm\n"
            "p001,beleza_saude,10,100,1,200,10,5,8\n"
        ),
        "olist_sellers_dataset.csv": (
            "seller_id,seller_zip_code_prefix,seller_city,seller_state\n"
            "s001,01001,sao paulo,SP\n"
        ),
        "olist_orders_dataset.csv": (
            "order_id,customer_id,order_status,order_purchase_timestamp,"
            "order_approved_at,order_delivered_carrier_date,"
            "order_delivered_customer_date,order_estimated_delivery_date\n"
            "o001,c001,delivered,2017-01-01 10:00:00,"
            "2017-01-01 11:00:00,2017-01-02 09:00:00,"
            "2017-01-05 12:00:00,2017-01-10 00:00:00\n"
        ),
        "olist_order_items_dataset.csv": (
            "order_id,order_item_id,product_id,seller_id,shipping_limit_date,"
            "price,freight_value\n"
            "o001,1,p001,s001,2017-01-03 00:00:00,100.00,10.00\n"
        ),
        "olist_order_payments_dataset.csv": (
            "order_id,payment_sequential,payment_type,payment_installments,"
            "payment_value\n"
            "o001,1,credit_card,1,110.00\n"
        ),
    }

    for file_name, content in csv_contents.items():
        (tmp_path / file_name).write_text(
            content,
            encoding="utf-8",
            newline="",
        )

    connection = sqlite3.connect(":memory:")
    create_schema(connection, str(SCHEMA_PATH))

    results = import_core_tables(connection, tmp_path)

    assert len(results) == 6
    assert all(result["csv_row_count"] == 1 for result in results)
    assert all(result["inserted_row_count"] == 1 for result in results)
    assert all(result["database_row_count"] == 1 for result in results)
    assert all(result["row_count_matches"] is True for result in results)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
