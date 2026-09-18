import json
from collections import Counter
from pathlib import Path

from src.ecommerce_agent.day11_state import WorkflowStatus
from src.ecommerce_agent.day14_risk_cases import SPECS
from src.ecommerce_agent.day14_schema import (
    BusinessReferenceStatus,
    DatasetCategory,
    SqlReferenceKind,
    load_dataset,
)


PROJECT_ROOT = Path(__file__).parents[1]


def test_specs_are_exactly_ten_fixed_and_nonduplicated():
    assert len(SPECS) == 10
    assert [spec.case_id for spec in SPECS] == [
        f"D14_RU_{index:03d}" for index in range(1, 11)
    ]
    assert len({spec.analysis_type for spec in SPECS}) == 10


def test_draft_now_has_exact_required_60_case_distribution():
    dataset = load_dataset(
        PROJECT_ROOT / "data/evaluation/day14/dataset.v1.draft.json"
    )
    counts = Counter(case.category for case in dataset.cases)
    assert len(dataset.cases) == 60
    assert counts == {
        DatasetCategory.SINGLE_METRIC: 20,
        DatasetCategory.AGGREGATE_FILTER_JOIN: 20,
        DatasetCategory.MULTI_STEP: 10,
        DatasetCategory.RISK_AMBIGUOUS_UNANSWERABLE: 10,
    }


def test_clarification_and_unsupported_questions_never_fabricate_sql():
    dataset = load_dataset(
        PROJECT_ROOT / "data/evaluation/day14/dataset.v1.draft.json"
    )
    cases = {case.case_id: case for case in dataset.cases}
    for case_id in ("D14_RU_001", "D14_RU_002", "D14_RU_003", "D14_RU_004", "D14_RU_005"):
        case = cases[case_id]
        assert case.sql_reference.kind is SqlReferenceKind.NO_SQL_EXPECTED
        assert case.should_enter_sqlite is False
        assert case.allows_repair is False
        assert case.safety_expectation.maximum_allowed_repair_attempts == 0
    assert cases["D14_RU_001"].expected_workflow_status is WorkflowStatus.NEEDS_CLARIFICATION
    assert cases["D14_RU_002"].allowed_stop_reasons == (
        "unsupported_metric_dimension_combination",
    )


def test_safety_rejections_stop_before_sqlite_and_repair():
    dataset = load_dataset(
        PROJECT_ROOT / "data/evaluation/day14/dataset.v1.draft.json"
    )
    risk_cases = [
        case
        for case in dataset.cases
        if case.category is DatasetCategory.RISK_AMBIGUOUS_UNANSWERABLE
    ]
    rejected = [
        case
        for case in risk_cases
        if case.expected_workflow_status is WorkflowStatus.SAFETY_REJECTED
    ]
    assert len(rejected) == 3
    assert all(not case.should_enter_sqlite for case in rejected)
    assert all(not case.allows_repair for case in rejected)
    assert all(
        case.provenance.business_reference_status
        is BusinessReferenceStatus.NOT_APPLICABLE_FIXED_CONTRACT
        for case in risk_cases
    )


def test_timeout_and_environment_failures_remain_nonrepairable_system_states():
    report = json.loads(
        (PROJECT_ROOT / "docs/DAY14_RISK_RESULTS.json").read_text(encoding="utf-8")
    )
    records = {row["case_id"]: row for row in report["cases"]}
    timeout = records["D14_RU_009"]
    environment = records["D14_RU_010"]
    assert timeout["observed_deterministic_category"] == "resource_failure"
    assert timeout["execution_started"] is True
    assert timeout["repair_eligible"] is False
    assert environment["observed_deterministic_category"] == "environment_error"
    assert environment["execution_started"] is False
    assert environment["repair_eligible"] is False


def test_risk_report_discloses_scope_and_protected_data_integrity():
    report = json.loads(
        (PROJECT_ROOT / "docs/DAY14_RISK_RESULTS.json").read_text(encoding="utf-8")
    )
    assert report["case_count"] == 10
    assert report["no_sql_expected_count"] == 8
    assert report["safety_rejection_count"] == 3
    assert report["safety_rejections_entering_sqlite"] == 0
    assert report["cases_allowing_repair"] == 0
    assert report["external_api_calls"] == 0
    assert report["real_model_runs"] == 0
    assert report["model_generated_numeric_results"] == 0
    assert report["database_unchanged"] is True
    assert report["raw_files_unchanged"] is True
