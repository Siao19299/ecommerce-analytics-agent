import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from src.ecommerce_agent.day03_pipeline import CORE_TABLE_IMPORTS


TABLE_QUALITY_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "dim_customers": {
        "primary_key": ("customer_id",),
        "numeric_columns": (),
        "date_columns": (),
    },
    "dim_products": {
        "primary_key": ("product_id",),
        "numeric_columns": (
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ),
        "date_columns": (),
    },
    "dim_sellers": {
        "primary_key": ("seller_id",),
        "numeric_columns": (),
        "date_columns": (),
    },
    "fact_orders": {
        "primary_key": ("order_id",),
        "numeric_columns": (),
        "date_columns": (
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
    },
    "fact_order_items": {
        "primary_key": ("order_id", "order_item_id"),
        "numeric_columns": (
            "order_item_id",
            "price",
            "freight_value",
        ),
        "date_columns": ("shipping_limit_date",),
    },
    "fact_payments": {
        "primary_key": ("order_id", "payment_sequential"),
        "numeric_columns": (
            "payment_sequential",
            "payment_installments",
            "payment_value",
        ),
        "date_columns": (),
    },
}


def _json_number(value: Any) -> int | float | None:
    if pd.isna(value):
        return None

    number = float(value)
    if number.is_integer():
        return int(number)
    return number


def _json_timestamp(value: Any) -> str | None:
    if pd.isna(value):
        return None
    return value.isoformat(sep=" ")


def profile_table(
    csv_path: str | Path,
    connection: sqlite3.Connection,
    table_name: str,
    primary_key: tuple[str, ...],
    numeric_columns: tuple[str, ...] = (),
    date_columns: tuple[str, ...] = (),
) -> tuple[dict[str, Any], pd.DataFrame]:
    """读取原始 CSV，并生成单表可序列化质量结果。"""
    dataframe = pd.read_csv(
        csv_path,
        encoding="utf-8-sig",
        dtype="string",
    )
    csv_row_count = len(dataframe)
    database_row_count = connection.execute(
        f'SELECT COUNT(*) FROM "{table_name}"'
    ).fetchone()[0]

    missing_by_column = {
        column: int(dataframe[column].isna().sum())
        for column in dataframe.columns
    }
    primary_key_missing = dataframe[list(primary_key)].isna().any(axis=1)
    duplicate_key_row_count = int(
        dataframe.loc[~primary_key_missing]
        .duplicated(subset=list(primary_key), keep="first")
        .sum()
    )

    numeric_profiles = []
    for column in numeric_columns:
        source = dataframe[column]
        parsed = pd.to_numeric(source, errors="coerce")
        numeric_profiles.append(
            {
                "column": column,
                "missing_count": int(source.isna().sum()),
                "invalid_count": int((source.notna() & parsed.isna()).sum()),
                "negative_count": int((parsed < 0).sum()),
                "minimum": _json_number(parsed.min()),
                "maximum": _json_number(parsed.max()),
            }
        )

    date_profiles = []
    for column in date_columns:
        source = dataframe[column]
        parsed = pd.to_datetime(source, errors="coerce")
        date_profiles.append(
            {
                "column": column,
                "missing_count": int(source.isna().sum()),
                "invalid_count": int((source.notna() & parsed.isna()).sum()),
                "minimum": _json_timestamp(parsed.min()),
                "maximum": _json_timestamp(parsed.max()),
            }
        )

    profile = {
        "table_name": table_name,
        "source_file": Path(csv_path).name,
        "csv_row_count": csv_row_count,
        "database_row_count": database_row_count,
        "row_count_difference": database_row_count - csv_row_count,
        "primary_key": list(primary_key),
        "primary_key_missing_row_count": int(primary_key_missing.sum()),
        "duplicate_key_row_count": duplicate_key_row_count,
        "missing_by_column": missing_by_column,
        "numeric_profiles": numeric_profiles,
        "date_profiles": date_profiles,
    }
    return profile, dataframe


def inspect_order_date_sequence(
    orders: pd.DataFrame,
) -> list[dict[str, str | int]]:
    """检查具有明确先后含义的订单事件时间，不把业务延迟误判为格式错误。"""
    date_columns = TABLE_QUALITY_RULES["fact_orders"]["date_columns"]
    parsed = {
        column: pd.to_datetime(orders[column], errors="coerce")
        for column in date_columns
    }
    rules = (
        (
            "approved_before_purchase",
            "order_approved_at",
            "order_purchase_timestamp",
        ),
        (
            "carrier_before_purchase",
            "order_delivered_carrier_date",
            "order_purchase_timestamp",
        ),
        (
            "customer_delivery_before_purchase",
            "order_delivered_customer_date",
            "order_purchase_timestamp",
        ),
        (
            "estimated_delivery_before_purchase",
            "order_estimated_delivery_date",
            "order_purchase_timestamp",
        ),
        (
            "customer_delivery_before_carrier",
            "order_delivered_customer_date",
            "order_delivered_carrier_date",
        ),
    )

    results = []
    for rule_name, later_column, earlier_column in rules:
        later = parsed[later_column]
        earlier = parsed[earlier_column]
        comparable = later.notna() & earlier.notna()
        violation_mask = comparable & (later < earlier)
        violation_count = int(violation_mask.sum())
        results.append(
            {
                "rule": rule_name,
                "expected": f"{later_column} >= {earlier_column}",
                "comparable_row_count": int(comparable.sum()),
                "violation_count": violation_count,
                "example_order_ids": orders.loc[
                    violation_mask,
                    "order_id",
                ].head(5).tolist(),
            }
        )

    return results


def build_quality_report(
    raw_dir: str | Path,
    database_path: str | Path,
) -> dict[str, Any]:
    raw_path = Path(raw_dir)
    connection = sqlite3.connect(database_path)

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        table_profiles = []
        orders_dataframe = None

        for table_name, file_name in CORE_TABLE_IMPORTS:
            rules = TABLE_QUALITY_RULES[table_name]
            profile, dataframe = profile_table(
                raw_path / file_name,
                connection,
                table_name,
                rules["primary_key"],
                rules["numeric_columns"],
                rules["date_columns"],
            )
            table_profiles.append(profile)

            if table_name == "fact_orders":
                orders_dataframe = dataframe

        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    finally:
        connection.close()

    if orders_dataframe is None:
        raise RuntimeError("质量检查未读取到订单表")

    return {
        "tables": table_profiles,
        "order_date_sequence_checks": inspect_order_date_sequence(
            orders_dataframe
        ),
        "database_foreign_key_violation_count": len(
            foreign_key_violations
        ),
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Day 3 Olist 核心表数据质量报告",
        "",
        "本报告由项目脚本从 `data/raw/` 与 SQLite 数据库重复生成；"
        "原始文件未被修改。",
        "",
        "## 行数、主键与外键",
        "",
        "| 表 | CSV 行数 | 数据库行数 | 差异 | 主键缺失行 | 主键重复行 |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for table in report["tables"]:
        lines.append(
            f'| `{table["table_name"]}` | {table["csv_row_count"]:,} | '
            f'{table["database_row_count"]:,} | '
            f'{table["row_count_difference"]:,} | '
            f'{table["primary_key_missing_row_count"]:,} | '
            f'{table["duplicate_key_row_count"]:,} |'
        )

    lines.extend(
        [
            "",
            "数据库外键违规数："
            f'**{report["database_foreign_key_violation_count"]:,}**。',
            "",
            "## 字段缺失",
            "",
            "| 表 | 字段 | 缺失数 |",
            "|---|---|---:|",
        ]
    )
    for table in report["tables"]:
        for column, missing_count in table["missing_by_column"].items():
            lines.append(
                f'| `{table["table_name"]}` | `{column}` | '
                f"{missing_count:,} |"
            )

    lines.extend(
        [
            "",
            "## 数值解析与范围",
            "",
            "| 表 | 字段 | 缺失 | 无法解析 | 负值 | 最小值 | 最大值 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for table in report["tables"]:
        for profile in table["numeric_profiles"]:
            lines.append(
                f'| `{table["table_name"]}` | `{profile["column"]}` | '
                f'{profile["missing_count"]:,} | '
                f'{profile["invalid_count"]:,} | '
                f'{profile["negative_count"]:,} | '
                f'{profile["minimum"]} | {profile["maximum"]} |'
            )

    lines.extend(
        [
            "",
            "## 日期解析",
            "",
            "| 表 | 字段 | 缺失 | 无法解析 | 最早时间 | 最晚时间 |",
            "|---|---|---:|---:|---|---|",
        ]
    )
    for table in report["tables"]:
        for profile in table["date_profiles"]:
            lines.append(
                f'| `{table["table_name"]}` | `{profile["column"]}` | '
                f'{profile["missing_count"]:,} | '
                f'{profile["invalid_count"]:,} | '
                f'{profile["minimum"]} | {profile["maximum"]} |'
            )

    lines.extend(
        [
            "",
            "## 订单事件时间顺序",
            "",
            "| 规则 | 期望关系 | 可比较行 | 违反行 | 示例订单 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for check in report["order_date_sequence_checks"]:
        lines.append(
            f'| `{check["rule"]}` | `{check["expected"]}` | '
            f'{check["comparable_row_count"]:,} | '
            f'{check["violation_count"]:,} | '
            f'{", ".join(check["example_order_ids"])} |'
        )

    lines.append("")
    return "\n".join(lines)


def write_quality_report(
    report: dict[str, Any],
    json_path: str | Path,
    markdown_path: str | Path,
) -> None:
    json_output = Path(json_path)
    markdown_output = Path(markdown_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_output.write_text(
        render_markdown_report(report),
        encoding="utf-8",
    )


if __name__ == "__main__":
    project_root = Path(__file__).parents[2]
    quality_report = build_quality_report(
        raw_dir=project_root / "data" / "raw",
        database_path=project_root / "data" / "processed" / "olist.sqlite3",
    )
    report_dir = project_root / "data" / "processed" / "quality"
    write_quality_report(
        quality_report,
        json_path=report_dir / "day03_quality_report.json",
        markdown_path=report_dir / "day03_quality_report.md",
    )
    print(render_markdown_report(quality_report))
