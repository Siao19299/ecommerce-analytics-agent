"""Checks for the assistant-authored Day 11 real-SQLite closure."""

from pathlib import Path

from src.ecommerce_agent.day11_benchmark import run_benchmark


ROOT = Path(__file__).parents[1]


def test_day11_real_sqlite_cases_are_traceable_and_honestly_scoped():
    report = run_benchmark(ROOT)

    assert report["case_count"] == 7
    assert report["expectation_met_count"] == 7
    assert report["database_unchanged"] is True
    assert report["external_api_calls"] == 0
    assert report["model_generated_numeric_results"] == 0
    assert "fake_model_responses" in report["measurement_scope"]
    assert all(
        item["business_reference_status"]
        == "not_independently_evaluated"
        for item in report["cases"]
    )


def test_benchmark_distinguishes_branches_and_connects_success_lineage():
    report = run_benchmark(ROOT)
    cases = {item["case_id"]: item for item in report["cases"]}

    assert cases["D11_NEEDS_CLARIFICATION"]["sql_attempt_count"] == 0
    assert cases["D11_SAFETY_REJECTION"]["sql_attempt_count"] == 0
    assert cases["D11_ONE_REPAIR_SUCCESS"]["sql_attempt_count"] == 2
    assert cases["D11_ONE_REPAIR_SUCCESS"]["lineage_connected"] is True
    assert cases["D11_REPAIR_LIMIT"]["stop_reason"] == (
        "repair_limit_reached"
    )
    assert cases["D11_MISSING_COMPARISON_STATE"][
        "calculation_status"
    ] == "missing_comparison_period"
    assert cases["D11_CONTROLLED_CALCULATION_FAILURE"][
        "observed_status"
    ] == "calculation_failed"
