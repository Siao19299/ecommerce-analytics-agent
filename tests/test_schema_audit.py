from src.ecommerce_agent.schema_audit import (
    inspect_single_column_key,
    inspect_composite_key,
    inspect_foreign_key,
)


def test_inspect_single_column_key_counts_missing_and_duplicates(
    tmp_path,
):
    file_path = tmp_path / "orders.csv"
    file_path.write_bytes(
        b"order_id,order_status\n"
        b"o001,delivered\n"
        b"o002,canceled\n"
        b"o001,delivered\n"
        b",delivered\n"
    )

    result = inspect_single_column_key(
        str(file_path),
        "order_id",
    )

    assert result["row_count"] == 4
    assert result["missing_key_count"] == 1
    assert result["unique_key_count"] == 2
    assert result["duplicate_row_count"] == 1


def test_inspect_composite_key_counts_missing_and_duplicates(
    tmp_path,
):
    file_path = tmp_path / "order_items.csv"
    file_path.write_bytes(
        b"order_id,order_item_id\n"
        b"o001,1\n"
        b"o001,2\n"
        b"o002,1\n"
        b"o001,1\n"
        b"o003,\n"
    )

    result = inspect_composite_key(
        str(file_path),
        ["order_id", "order_item_id"],
    )

    assert result["row_count"] == 5
    assert result["missing_key_count"] == 1
    assert result["unique_key_count"] == 3
    assert result["duplicate_row_count"] == 1


def test_inspect_foreign_key_counts_missing_and_orphan_rows(
    tmp_path,
):
    parent_path = tmp_path / "orders.csv"
    parent_path.write_bytes(
        b"order_id\n"
        b"o001\n"
        b"o002\n"
    )

    child_path = tmp_path / "order_items.csv"
    child_path.write_bytes(
        b"order_id,product_id\n"
        b"o001,p001\n"
        b"o999,p002\n"
        b",p003\n"
        b"o999,p004\n"
    )

    result = inspect_foreign_key(
        str(child_path),
        "order_id",
        str(parent_path),
        "order_id",
    )

    assert result["child_row_count"] == 4
    assert result["parent_key_count"] == 2
    assert result["missing_foreign_key_count"] == 1
    assert result["orphan_row_count"] == 2
