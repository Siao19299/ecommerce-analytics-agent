"""Checks for the honestly scoped User interface offline UI acceptance."""

from pathlib import Path

import pytest

from src.ecommerce_agent.ui_benchmark import run_benchmark


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def report():
    return run_benchmark(ROOT)


def test_benchmark_separates_evidence_and_preserves_files(report):
    assert report["case_count"] == 14
    assert report["expectation_met_count"] == 14
    assert report["streamlit_apptest_case_count"] == 14
    assert report["day12_api_integration_case_count"] == 5
    assert report["full_state_machine_case_count"] == 5
    assert report["real_sqlite_execution_case_count"] == 3
    assert report["scripted_public_response_case_count"] == 5
    assert report["scripted_client_failure_case_count"] == 4
    assert report["external_api_calls"] == 0
    assert report["model_generated_numeric_results"] == 0
    assert report["independent_user_completion"] is False
    assert report["independent_business_reference"] is False
    assert report["database_unchanged"] is True
    assert report["raw_files_unchanged"] is True


def test_benchmark_covers_ui_outputs_and_sensitive_boundaries(report):
    cases = {item["case_id"]: item for item in report["cases"]}

    success = cases["D13_REAL_SQLITE_SUCCESS"]
    assert success["success_component_count"] == 1
    assert success["sql_component_count"] == 1
    assert success["table_component_count"] == 2
    assert success["chart_component_count"] == 1
    assert success["run_id"]
    assert cases["D13_CLARIFICATION"]["sql_component_count"] == 0
    assert cases["D13_SAFETY_REJECTION"]["sql_component_count"] == 0
    assert cases["D13_MISSING_COMPARISON"]["calculation_status"] == (
        "missing_comparison_period"
    )
    assert cases["D13_ZERO_BASELINE"]["calculation_status"] == (
        "zero_baseline"
    )
    assert all(item["sensitive_markers_absent"] for item in cases.values())
    assert all(item["component_exception_absent"] for item in cases.values())
