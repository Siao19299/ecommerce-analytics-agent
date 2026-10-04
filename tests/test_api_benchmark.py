"""Checks for the honestly scoped API service offline HTTP acceptance."""

from pathlib import Path

import pytest

from src.ecommerce_agent.api_benchmark import run_benchmark


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def report():
    return run_benchmark(ROOT)


def test_benchmark_separates_evidence_scopes_and_preserves_files(report):

    assert report["case_count"] == 15
    assert report["expectation_met_count"] == 15
    assert report["full_state_machine_case_count"] == 9
    assert report["real_sqlite_execution_case_count"] == 6
    assert report["scripted_state_case_count"] == 2
    assert report["unhandled_exception_case_count"] == 1
    assert report["request_validation_case_count"] == 3
    assert report["external_api_calls"] == 0
    assert report["model_generated_numeric_results"] == 0
    assert report["database_unchanged"] is True
    assert report["raw_files_unchanged"] is True


def test_benchmark_covers_required_http_and_calculation_branches(report):
    cases = {item["case_id"]: item for item in report["cases"]}

    assert cases["D12_REPAIR_SUCCESS"]["sql_attempt_count"] == 2
    assert cases["D12_REPAIR_LIMIT"]["observed_workflow_status"] == (
        "repair_limit_reached"
    )
    assert cases["D12_MISSING_COMPARISON"]["calculation_status"] == (
        "missing_comparison_period"
    )
    assert cases["D12_ZERO_BASELINE"]["calculation_status"] == (
        "zero_baseline"
    )
    assert cases["D12_ENVIRONMENT_FAILURE"]["observed_http_status"] == 503
    assert cases["D12_UNHANDLED_EXCEPTION"]["sensitive_markers_absent"] is True
    assert cases["D12_MALFORMED_JSON"]["observed_workflow_status"] == (
        "invalid_request"
    )
    assert all(item["sensitive_markers_absent"] for item in cases.values())
