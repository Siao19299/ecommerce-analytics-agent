import json
from pathlib import Path

from src.ecommerce_agent.day14_schema import (
    BusinessReferenceStatus,
    DatasetCategory,
    SqliteVerificationStatus,
    UserReviewStatus,
    load_dataset,
    validate_named_parameter_contract,
)
from src.ecommerce_agent.day14_single_metric import SPECS


PROJECT_ROOT = Path(__file__).parents[1]


def test_single_metric_specs_are_exactly_twenty_distinct_metrics_and_ids():
    assert len(SPECS) == 20
    assert len({spec.case_id for spec in SPECS}) == 20
    assert len({spec.metric_id for spec in SPECS}) == 20
    assert [spec.case_id for spec in SPECS] == [
        f"D14_SM_{index:03d}" for index in range(1, 21)
    ]


def test_single_metric_sql_uses_exact_named_parameter_contract():
    for spec in SPECS:
        validate_named_parameter_contract(spec.sql, spec.parameters)
        assert ":start_date" in spec.sql
        assert ":end_date_exclusive" in spec.sql


def test_saved_single_metric_dataset_has_strict_provenance_and_references():
    dataset = load_dataset(
        PROJECT_ROOT / "data/evaluation/day14/dataset.v1.draft.json"
    )
    cases = [
        case for case in dataset.cases
        if case.category is DatasetCategory.SINGLE_METRIC
    ]
    assert len(cases) == 20
    assert len({case.metric_ids[0] for case in cases}) == 20
    for case in cases:
        assert case.should_enter_sqlite is True
        assert case.allows_repair is True
        assert case.provenance.user_review_status is UserReviewStatus.NOT_REVIEWED
        assert case.provenance.sqlite_verification_status is (
            SqliteVerificationStatus.VERIFIED_REAL_SQLITE
        )
        assert case.provenance.business_reference_status is (
            BusinessReferenceStatus.NOT_INDEPENDENTLY_EVALUATED
        )
        sql_path = PROJECT_ROOT / case.sql_reference.path
        result_path = PROJECT_ROOT / case.result_reference.path
        assert sql_path.is_file()
        assert result_path.is_file()
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["safety_gate_accepted"] is True
        assert result["execution_started"] is True
        assert result["rows_truncated"] is False


def test_single_metric_report_records_real_sqlite_without_model_claims():
    report = json.loads(
        (PROJECT_ROOT / "docs/DAY14_SINGLE_METRIC_RESULTS.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["case_count"] == 20
    assert report["unique_metric_count"] == 20
    assert report["named_parameter_contract_pass_count"] == 20
    assert report["safety_gate_pass_count"] == 20
    assert report["real_sqlite_execution_count"] == 20
    assert report["external_api_calls"] == 0
    assert report["model_generated_numeric_results"] == 0
    assert report["user_reviewed_case_count"] == 0
    assert report["independent_business_reference_case_count"] == 0
    assert report["database_unchanged"] is True
    assert report["raw_files_unchanged"] is True
