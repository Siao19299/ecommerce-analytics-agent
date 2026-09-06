import csv
import sqlite3
from pathlib import Path


TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "dim_customers": (
        "customer_id",
        "customer_unique_id",
        "customer_zip_code_prefix",
        "customer_city",
        "customer_state",
    ),
    "dim_products": (
        "product_id",
        "product_category_name",
        "product_name_lenght",
        "product_description_lenght",
        "product_photos_qty",
        "product_weight_g",
        "product_length_cm",
        "product_height_cm",
        "product_width_cm",
    ),
    "dim_sellers": (
        "seller_id",
        "seller_zip_code_prefix",
        "seller_city",
        "seller_state",
    ),
    "fact_orders": (
        "order_id",
        "customer_id",
        "order_status",
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ),
    "fact_order_items": (
        "order_id",
        "order_item_id",
        "product_id",
        "seller_id",
        "shipping_limit_date",
        "price",
        "freight_value",
    ),
    "fact_payments": (
        "order_id",
        "payment_sequential",
        "payment_type",
        "payment_installments",
        "payment_value",
    ),
}


def create_schema(
    connection: sqlite3.Connection,
    schema_path: str,
) -> None:
    """开启外键检查，并在 SQLite 连接中执行建表脚本。"""
    sql = Path(schema_path).read_text(encoding="utf-8")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(sql)


def import_csv(
    connection: sqlite3.Connection,
    csv_path: str,
    table_name: str,
    *,
    commit: bool = True,
) -> int:
    """将允许列表中的 CSV 导入表中，返回本次新增行数。"""
    try:
        columns = TABLE_COLUMNS[table_name]
    except KeyError as error:
        raise ValueError(f"不允许导入未知表：{table_name}") from error

    with open(
        csv_path,
        mode="r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError(
                f"{Path(csv_path).name} 的字段与 {table_name} 不一致"
            )

        records = [
            tuple(row[column] or None for column in columns)
            for row in reader
        ]

    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = (
        f'INSERT INTO "{table_name}" '
        f"({quoted_columns}) VALUES ({placeholders}) "
        "ON CONFLICT DO NOTHING"
    )

    changes_before = connection.total_changes
    connection.executemany(insert_sql, records)
    if commit:
        connection.commit()

    return connection.total_changes - changes_before
