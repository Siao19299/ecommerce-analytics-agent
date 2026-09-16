"""Checks for the assistant-authored Day 10 real-SQLite closure."""

from pathlib import Path

import pytest

from src.ecommerce_agent.day10_benchmark import run_benchmark


ROOT = Path(__file__).parents[1]


def test_real_sqlite_benchmark_is_traceable_and_honestly_scoped():
    report = run_benchmark(ROOT)

    assert report["case_count"] == 4
    assert report["expectation_met_count"] == 4
    assert report["database_unchanged"] is True
    assert report["external_api_calls"] == 0
    assert report["model_generated_numeric_results"] == 0
    assert "not_independent_business_accuracy" in report["measurement_scope"]
    assert all(
        case["business_reference_status"] == "not_independently_evaluated"
        for case in report["cases"]
    )
    assert all(
        case["calculation_trace"]["parent_run_id"].startswith("day10-real-")
        for case in report["cases"]
    )
    assert all(
        case["presentation"]["generated_by"]
        == "deterministic_python_template"
        for case in report["cases"]
    )
    assert all(case["presentation"]["conclusion"] for case in report["cases"])


def test_real_values_match_saved_day4_scale_without_claiming_independence():
    report = run_benchmark(ROOT)
    cases = {case["case_id"]: case for case in report["cases"]}

    mom = cases["D10_REAL_MOM"]["observed"]
    assert mom["absolute_change"] == pytest.approx(11875.6)
    assert mom["relative_change"] == pytest.approx(0.013872, abs=1e-6)

    yoy = cases["D10_REAL_YOY"]["observed"]
    assert yoy["absolute_change"] == pytest.approx(386348.94)
    assert yoy["relative_change"] == pytest.approx(0.802212, abs=1e-6)

    contribution = cases["D10_REAL_CONTRIBUTION"]["observed"]
    assert contribution["group_count"] > 1
    assert contribution["contribution_sum"] == pytest.approx(1.0)

    anomaly = cases["D10_REAL_ANOMALY"]["observed"]
    assert anomaly["robust_z_score"] >= anomaly["threshold"]
