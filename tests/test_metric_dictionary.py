import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
METRIC_DICTIONARY_PATH = (
    PROJECT_ROOT / "data" / "metadata" / "metric_dictionary.csv"
)
REQUIRED_COLUMNS = {
    "metric_id",
    "chinese_name",
    "english_name",
    "definition",
    "formula",
    "source_tables",
    "base_grain",
    "default_time_field",
    "available_dimensions",
    "constraints",
}


def load_metric_dictionary() -> list[dict[str, str]]:
    with METRIC_DICTIONARY_PATH.open(
        mode="r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        return list(csv.DictReader(file))


def test_metric_dictionary_has_at_least_twenty_complete_unique_metrics():
    rows = load_metric_dictionary()

    assert len(rows) >= 20
    assert set(rows[0]) == REQUIRED_COLUMNS
    assert len({row["metric_id"] for row in rows}) == len(rows)
    assert all(
        row[column].strip()
        for row in rows
        for column in REQUIRED_COLUMNS
    )


def test_metric_dictionary_records_core_grain_safeguards():
    metrics = {row["metric_id"]: row for row in load_metric_dictionary()}

    assert "price" in metrics["delivered_gmv"]["formula"]
    assert "不包含运费" in metrics["delivered_gmv"]["definition"]
    assert "customer_unique_id" in metrics["delivered_customer_count"][
        "formula"
    ]
    assert "order_id" in metrics["delivered_average_order_value"][
        "constraints"
    ]
    assert "不得与订单明细直接同时连接" in metrics[
        "delivered_payment_amount"
    ]["constraints"]
    assert "DENSE_RANK" in metrics[
        "delivered_monthly_category_gmv_rank"
    ]["formula"]
    assert "不完整月份" in metrics["delivered_gmv_mom_rate"][
        "constraints"
    ]
