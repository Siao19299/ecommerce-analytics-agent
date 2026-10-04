from pathlib import Path

from src.ecommerce_agent.failure_analysis import (
    FailureBoundary,
    build_mechanical_diagnostic_scenarios,
    load_empirical_failures,
    select_representative_empirical_failures,
)


ROOT = Path(__file__).resolve().parents[1]


def _scenarios():
    return {item.scenario_id: item for item in build_mechanical_diagnostic_scenarios(ROOT)}


def test_has_at_least_ten_explicitly_non_empirical_diagnostics():
    scenarios = build_mechanical_diagnostic_scenarios(ROOT)
    assert len(scenarios) >= 10
    assert all(item.provenance == "mechanical_post_seal_diagnostic" for item in scenarios)
    assert all(item.empirical_model_result is False for item in scenarios)


def test_execution_success_and_result_correctness_are_separate_layers():
    scenario = _scenarios()["FA03"]
    assert scenario.layer_results["execution_success"] is True
    assert scenario.layer_results["result_correct"] is False
    assert scenario.diagnosis.primary_boundary is FailureBoundary.RESULT_SEMANTICS


def test_ambiguity_and_unsupported_semantics_fail_at_planning_boundary():
    scenarios = _scenarios()
    assert scenarios["FA05"].diagnosis.primary_boundary is FailureBoundary.PLANNING_SEMANTICS
    assert scenarios["FA06"].diagnosis.primary_boundary is FailureBoundary.PLANNING_SEMANTICS


def test_safety_and_repair_failures_are_not_collapsed():
    scenarios = _scenarios()
    assert scenarios["FA07"].diagnosis.primary_boundary is FailureBoundary.CANDIDATE_SAFETY_CHOICE
    assert scenarios["FA08"].diagnosis.primary_boundary is FailureBoundary.REPAIR_CONTROL
    assert scenarios["FA08"].layer_results["safety_correct"] is False


def test_calculation_lineage_resource_and_environment_boundaries_are_distinct():
    scenarios = _scenarios()
    assert scenarios["FA09"].diagnosis.primary_boundary is FailureBoundary.DETERMINISTIC_CALCULATION
    assert scenarios["FA10"].diagnosis.primary_boundary is FailureBoundary.DETERMINISTIC_CALCULATION
    assert scenarios["FA11"].diagnosis.primary_boundary is FailureBoundary.LINEAGE
    assert scenarios["FA12"].diagnosis.primary_boundary is FailureBoundary.REPAIR_CONTROL
    assert scenarios["FA13"].diagnosis.primary_boundary is FailureBoundary.ENVIRONMENT_PROPAGATION


def test_output_contract_failure_never_reaches_private_case_scoring():
    scenario = _scenarios()["FA01"]
    assert scenario.layer_results == {}
    assert scenario.diagnosis.primary_boundary is FailureBoundary.OUTPUT_CONTRACT
    assert scenario.diagnosis.scorer_failure_categories == ("candidate_output_contract",)


def test_real_model_failures_are_loaded_only_from_complete_sealed_runs():
    run_directory = ROOT / "data/processed/day15/live_main_rerun1"
    if not run_directory.exists():
        return
    failures = load_empirical_failures(ROOT, run_directory)
    selected = select_representative_empirical_failures(failures, minimum_count=10)
    assert len(failures) == (54 + 38 + 35)
    assert len(selected) >= 10
    assert {item.candidate_version for item in selected} == {
        "direct_sql", "retrieval_sql", "full_agent"
    }
    assert all(item.diagnosis.scorer_failure_categories for item in selected)
