import json
from pathlib import Path

from src.ecommerce_agent.day14_dataset_validation import (
    normalize_question_template,
    template_similarity,
)


PROJECT_ROOT = Path(__file__).parents[1]


def test_template_normalization_masks_date_only_variants():
    june = "计算 2018 年 6 月已送达订单数。"
    july = "计算 2018 年 7 月已送达订单数。"
    assert normalize_question_template(june) == normalize_question_template(july)
    assert template_similarity(june, july) == 1.0


def test_template_similarity_keeps_different_business_tasks_apart():
    orders = "计算 2018 年 7 月已送达订单数。"
    inventory = "列出实时库存并预测未来缺货日期。"
    assert template_similarity(orders, inventory) < 0.5


def test_validation_report_proves_exact_counts_integrity_and_coverage():
    report = json.loads(
        (PROJECT_ROOT / "docs/DAY14_DATASET_VALIDATION.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["passed"] is True
    assert report["case_count"] == 60
    assert report["category_counts_valid"] is True
    assert report["case_ids_unique"] is True
    assert report["case_ids_stable_and_contiguous"] is True
    assert report["reference_sql_hash_verified_count"] == 52
    assert report["result_hash_verified_count"] == 50
    assert report["coverage"]["category"] == {
        "aggregate_filter_join": 20,
        "multi_step": 10,
        "risk_ambiguous_unanswerable": 10,
        "single_metric": 20,
    }
    assert set(report["coverage"]["difficulty"]) == {"easy", "medium", "hard"}
    assert report["coverage"]["intentional_unsupported_dimensions_in_risk_cases"] == [
        "marketing_channel",
        "product",
    ]
    assert report["coverage"]["dictionary_metric_count"] == 27
    assert report["coverage"]["covered_dictionary_metric_count"] == 24
    assert report["coverage"]["uncovered_metric_ids"] == [
        "average_carrier_delivery_days",
        "delivered_gmv_mom_rate",
        "delivered_gmv_yoy_rate",
    ]
    assert report["coverage"]["covered_dictionary_dimension_count"] == 6
    assert report["coverage"]["uncovered_supported_dimension_ids"] == []


def test_template_repetition_policy_passes_without_hidden_exact_templates():
    report = json.loads(
        (PROJECT_ROOT / "docs/DAY14_DATASET_VALIDATION.json").read_text(
            encoding="utf-8"
        )
    )
    repetition = report["template_repetition"]
    assert repetition["passed"] is True
    assert repetition["exact_duplicate_groups"] == []
    assert repetition["normalized_template_duplicate_groups"] == []
    assert repetition["high_similarity_pair_ratio"] <= repetition[
        "maximum_high_similarity_pair_ratio"
    ]
    assert repetition["largest_high_similarity_cluster_size"] <= repetition[
        "maximum_high_similarity_cluster_size"
    ]
    assert len(repetition["top_similarity_pairs_for_review"]) == 10
    assert repetition["top_similarity_pairs_for_review"][0]["similarity"] < repetition[
        "high_similarity_threshold"
    ]


def test_coverage_markdown_is_generated_and_discloses_no_accuracy_claim():
    text = (PROJECT_ROOT / "docs/DAY14_COVERAGE_REPORT.md").read_text(
        encoding="utf-8"
    )
    assert "# Day 14 Coverage Report" in text
    assert "not candidate-model accuracy" in text
    assert "Independently verified business references: 0" in text
    assert "## Analysis type coverage" in text
    assert "## Uncovered dictionary metrics" in text
