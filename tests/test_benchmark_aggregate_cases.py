import json
from pathlib import Path

from src.ecommerce_agent.artifact_paths import resolve_artifact_path
from src.ecommerce_agent.benchmark_aggregate_cases import SPECS
from src.ecommerce_agent.evaluation_schema import (
    BusinessReferenceStatus,
    DatasetCategory,
    RowComparison,
    SqliteVerificationStatus,
    UserReviewStatus,
    load_dataset,
    validate_named_parameter_contract,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog


PROJECT_ROOT = Path(__file__).parents[1]


def test_aggregate_join_specs_are_exactly_twenty_with_stable_ids():
    assert len(SPECS) == 20
    assert len({spec.case_id for spec in SPECS}) == 20
    assert [spec.case_id for spec in SPECS] == [
        f"D14_AJ_{index:03d}" for index in range(1, 21)
    ]
    assert len({spec.analysis_type for spec in SPECS}) >= 15


def test_every_metric_dimension_pair_is_allowed_by_the_canonical_catalog():
    catalog = MetricCatalog.from_csv(
        PROJECT_ROOT / "data/metadata/metric_dictionary.csv",
        PROJECT_ROOT / "data/metadata/dimension_dictionary.csv",
    )
    for spec in SPECS:
        assert spec.metric_id in catalog.metrics
        assert spec.dimension in catalog.metrics[spec.metric_id].available_dimensions
        validate_named_parameter_contract(spec.sql, spec.parameters)


def test_saved_aggregate_cases_have_stable_order_and_verified_assets():
    dataset = load_dataset(
        PROJECT_ROOT / "data/evaluation/benchmark_v1/dataset.v1.draft.json"
    )
    cases = [
        case
        for case in dataset.cases
        if case.category is DatasetCategory.AGGREGATE_FILTER_JOIN
    ]
    assert len(cases) == 20
    for case in cases:
        assert case.comparison_rules.row_comparison is RowComparison.ORDERED
        assert case.comparison_rules.order_keys
        assert case.should_enter_sqlite is True
        assert case.provenance.user_review_status is UserReviewStatus.NOT_REVIEWED
        assert case.provenance.sqlite_verification_status is (
            SqliteVerificationStatus.VERIFIED_REAL_SQLITE
        )
        assert case.provenance.business_reference_status is (
            BusinessReferenceStatus.NOT_INDEPENDENTLY_EVALUATED
        )
        result = json.loads(
            (resolve_artifact_path(PROJECT_ROOT, case.result_reference.path)).read_text(encoding="utf-8")
        )
        assert result["row_count"] >= 1
        assert result["safety_gate_accepted"] is True
        assert result["execution_started"] is True
        assert result["rows_truncated"] is False


def test_aggregate_report_is_honest_and_preserves_data():
    report = json.loads(
        (PROJECT_ROOT / "docs/reports/benchmark_aggregate_cases.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["case_count"] == 20
    assert report["metric_dimension_compatibility_pass_count"] == 20
    assert report["named_parameter_contract_pass_count"] == 20
    assert report["stable_order_contract_count"] == 20
    assert report["safety_gate_pass_count"] == 20
    assert report["real_sqlite_execution_count"] == 20
    assert report["external_api_calls"] == 0
    assert report["model_generated_numeric_results"] == 0
    assert report["user_reviewed_case_count"] == 0
    assert report["independent_business_reference_case_count"] == 0
    assert report["database_unchanged"] is True
    assert report["raw_files_unchanged"] is True
