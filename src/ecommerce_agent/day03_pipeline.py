import sqlite3
from pathlib import Path

from src.ecommerce_agent.day02_data_audit import inspect_csv
from src.ecommerce_agent.day02_database import create_schema, import_csv


CORE_TABLE_IMPORTS: tuple[tuple[str, str], ...] = (
    ("dim_customers", "olist_customers_dataset.csv"),
    ("dim_products", "olist_products_dataset.csv"),
    ("dim_sellers", "olist_sellers_dataset.csv"),
    ("fact_orders", "olist_orders_dataset.csv"),
    ("fact_order_items", "olist_order_items_dataset.csv"),
    ("fact_payments", "olist_order_payments_dataset.csv"),
)


def import_core_tables(
    connection: sqlite3.Connection,
    raw_dir: str | Path,
) -> list[dict[str, str | int | bool]]:
    """按外键依赖顺序导入六张核心表，并核对 CSV 与数据库行数。"""
    raw_path = Path(raw_dir)
    results: list[dict[str, str | int | bool]] = []

    try:
        connection.execute("BEGIN")

        for table_name, file_name in CORE_TABLE_IMPORTS:
            csv_path = raw_path / file_name
            _, csv_row_count = inspect_csv(str(csv_path))
            inserted_row_count = import_csv(
                connection,
                str(csv_path),
                table_name,
                commit=False,
            )
            database_row_count = connection.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]

            results.append(
                {
                    "table_name": table_name,
                    "source_file": file_name,
                    "csv_row_count": csv_row_count,
                    "inserted_row_count": inserted_row_count,
                    "database_row_count": database_row_count,
                    "row_count_matches": (
                        csv_row_count == database_row_count
                    ),
                }
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return results


def rebuild_database(
    raw_dir: str | Path,
    database_path: str | Path,
    schema_path: str | Path,
) -> list[dict[str, str | int | bool]]:
    """先生成临时数据库，全部成功后再替换可再生成的目标数据库。"""
    target_path = Path(database_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_name(f".{target_path.name}.tmp")
    temporary_path.unlink(missing_ok=True)

    connection = sqlite3.connect(temporary_path)

    try:
        create_schema(connection, str(schema_path))
        results = import_core_tables(connection, raw_dir)

        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_violations:
            raise sqlite3.IntegrityError(
                "全量导入后发现外键违规："
                f"{foreign_key_violations[:5]}"
            )
    except Exception:
        connection.close()
        temporary_path.unlink(missing_ok=True)
        raise
    else:
        connection.close()
        temporary_path.replace(target_path)

    return results


if __name__ == "__main__":
    project_root = Path(__file__).parents[2]
    import_results = rebuild_database(
        raw_dir=project_root / "data" / "raw",
        database_path=project_root / "data" / "processed" / "olist.sqlite3",
        schema_path=project_root / "sql" / "schema.sql",
    )

    for result in import_results:
        print(
            result["table_name"],
            f'CSV={result["csv_row_count"]}',
            f'DB={result["database_row_count"]}',
            f'match={result["row_count_matches"]}',
        )
