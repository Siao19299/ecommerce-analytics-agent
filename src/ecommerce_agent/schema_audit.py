import csv


def inspect_single_column_key(
    path: str,
    key_column: str,
) -> dict[str, int]:
    row_count = 0
    missing_key_count = 0
    duplicate_row_count = 0
    seen_keys = set()

    with open(
        path,
        mode="r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            row_count += 1

            key_value = row[key_column].strip()

            if key_value == "":
                missing_key_count += 1
                continue

            if key_value in seen_keys:
                duplicate_row_count += 1
            else:
                seen_keys.add(key_value)

    return {
        "row_count": row_count,
        "missing_key_count": missing_key_count,
        "unique_key_count": len(seen_keys),
        "duplicate_row_count": duplicate_row_count,
    }


def inspect_composite_key(
    path: str,
    key_columns: list[str],
) -> dict[str, int]:
    row_count = 0
    missing_key_count = 0
    duplicate_row_count = 0
    seen_keys = set()

    with open(
        path,
        mode="r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            row_count += 1
            key_parts = []

            for column in key_columns:
                key_parts.append(row[column].strip())

            if "" in key_parts:
                missing_key_count += 1
                continue

            key_value = tuple(key_parts)

            if key_value in seen_keys:
                duplicate_row_count += 1
            else:
                seen_keys.add(key_value)

    return {
        "row_count": row_count,
        "missing_key_count": missing_key_count,
        "unique_key_count": len(seen_keys),
        "duplicate_row_count": duplicate_row_count,
    }


def read_non_empty_values(
    path: str,
    column: str,
) -> set[str]:
    values = set()

    with open(
        path,
        mode="r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            value = row[column].strip()
            if value != "":
                values.add(value)

    return values


def inspect_foreign_key(
    child_path: str,
    child_column: str,
    parent_path: str,
    parent_column: str,
) -> dict[str, int]:
    parent_values = read_non_empty_values(
        parent_path,
        parent_column,
    )

    child_row_count = 0
    missing_foreign_key_count = 0
    orphan_row_count = 0

    with open(
        child_path,
        mode="r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            child_row_count += 1
            value = row[child_column].strip()

            if value == "":
                missing_foreign_key_count += 1
            elif value not in parent_values:
                orphan_row_count += 1

    return {
        "child_row_count": child_row_count,
        "parent_key_count": len(parent_values),
        "missing_foreign_key_count": missing_foreign_key_count,
        "orphan_row_count": orphan_row_count,
    }
