import json
from pathlib import Path

from src.ecommerce_agent.artifact_paths import resolve_artifact_path
from src.ecommerce_agent.benchmark_analysis_cases import SPECS
from src.ecommerce_agent.evaluation_schema import (
    BusinessReferenceStatus,
    CalculationStatus,
    DatasetCategory,
    SqliteVerificationStatus,
    UserReviewStatus,
    load_dataset,
    validate_named_parameter_contract,
)


PROJECT_ROOT = Path(__file__).parents[1]


def test_multi_step_specs_are_exactly_ten_and_cover_required_boundaries():
    assert len(SPECS) == 10
    assert len({spec.case_id for spec in SPECS}) == 10
    assert [spec.case_id for spec in SPECS] == [
        f"D14_MS_{index:03d}" for index in range(1, 11)
    ]
    statuses = {spec.expected_status for spec in SPECS}
    assert {
        CalculationStatus.COMPUTED,
        CalculationStatus.DETECTED,
        CalculationStatus.MISSING_COMPARISON_PERIOD,
        CalculationStatus.ZERO_BASELINE,
        CalculationStatus.NON_CONTIGUOUS_HISTORY,
        CalculationStatus.MISSING_CURRENT_PERIOD,
        CalculationStatus.ZERO_DISPERSION,
    } <= statuses
    assert {spec.calculation_kind for spec in SPECS} == {
        "comparison_mom",
        "comparison_yoy",
        "contribution",
        "anomaly",
    }


def test_multi_step_sql_has_exact_named_parameter_contracts():
    for spec in SPECS:
        validate_named_parameter_contract(spec.sql, spec.parameters)


def test_saved_multi_step_cases_preserve_sql_python_and_provenance_layers():
    dataset = load_dataset(
        PROJECT_ROOT / "data/evaluation/benchmark_v1/dataset.v1.draft.json"
    )
    cases = [
        case for case in dataset.cases if case.category is DatasetCategory.MULTI_STEP
    ]
    assert len(cases) == 10
    for case in cases:
        result = json.loads(
            (resolve_artifact_path(PROJECT_ROOT, case.result_reference.path)).read_text(encoding="utf-8")
        )
        assert result["source_sql"]["safety_gate_accepted"] is True
        assert result["source_sql"]["execution_started"] is True
        assert result["source_sql"]["rows_truncated"] is False
        assert result["calculation_trace"]["source_sql_attempt"] == 1
        assert result["presentation"]["generated_by"] == (
            "deterministic_python_template"
        )
        assert case.provenance.user_review_status is UserReviewStatus.NOT_REVIEWED
        assert case.provenance.sqlite_verification_status is (
            SqliteVerificationStatus.VERIFIED_REAL_SQLITE
        )
        assert case.provenance.business_reference_status is (
            BusinessReferenceStatus.NOT_INDEPENDENTLY_EVALUATED
        )


def test_boundary_cases_keep_distinct_deterministic_meanings():
    result_dir = PROJECT_ROOT / "data/evaluation/benchmark_v1/references/results"

    missing = json.loads((result_dir / "D14_MS_005.json").read_text(encoding="utf-8"))
    assert missing["calculation"]["calculation_status"] == "missing_comparison_period"
    assert missing["calculation"]["relative_change"] is None

    zero = json.loads((result_dir / "D14_MS_006.json").read_text(encoding="utf-8"))
    assert zero["calculation"]["calculation_status"] == "zero_baseline"
    assert zero["calculation"]["comparison_value"] == 0
    assert zero["calculation"]["relative_change"] is None

    incomplete = json.loads((result_dir / "D14_MS_007.json").read_text(encoding="utf-8"))
    assert incomplete["calculation"]["calculation_status"] == "computed"
    assert incomplete["calculation"]["comparability"] == "not_comparable"

    insufficient = json.loads((result_dir / "D14_MS_009.json").read_text(encoding="utf-8"))
    assert insufficient["calculation"]["calculation_status"] == "missing_current_period"
    assert "证据不足" in insufficient["presentation"]["conclusion"]

    zero_dispersion = json.loads(
        (result_dir / "D14_MS_010.json").read_text(encoding="utf-8")
    )
    assert zero_dispersion["calculation"]["calculation_status"] == "zero_dispersion"
    assert zero_dispersion["calculation"]["robust_z_score"] is None


def test_multi_step_report_separates_sql_python_and_model_claims():
    report = json.loads(
        (PROJECT_ROOT / "docs/reports/benchmark_analysis_cases.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["case_count"] == 10
    assert report["named_parameter_contract_pass_count"] == 10
    assert report["safety_gate_pass_count"] == 10
    assert report["real_sqlite_execution_count"] == 10
    assert report["deterministic_python_calculation_count"] == 10
    assert report["external_api_calls"] == 0
    assert report["model_generated_numeric_results"] == 0
    assert report["user_reviewed_case_count"] == 0
    assert report["independent_business_reference_case_count"] == 0
    assert report["database_unchanged"] is True
    assert report["raw_files_unchanged"] is True
