"""Checks for the assistant-authored Day 9 offline benchmark."""

from pathlib import Path

from src.ecommerce_agent.day09_benchmark import (
    load_cases,
    run_benchmark,
)


ROOT = Path(__file__).parents[1]


def test_fixture_discloses_source_and_required_stop_conditions():
    source, response_source, cases = load_cases(
        ROOT / "tests/fixtures/day09/repair_cases.json"
    )

    assert source == (
        "assistant_authored_mechanical_day9_cases_from_user_requirements"
    )
    assert response_source == (
        "fake_model_responses_with_real_sqlite_execution"
    )
    assert {case["category"] for case in cases} >= {
        "one_repair_success",
        "repair_limit_reached",
        "duplicate_candidate",
        "repair_candidate_safety_rejected",
        "environment_error",
    }


def test_benchmark_runs_real_sqlite_and_uses_honest_denominator():
    report = run_benchmark(ROOT)

    assert report["case_count"] == 9
    assert report["expectation_met_count"] == 9
    assert report["database_unchanged"] is True
    assert report["external_api_calls"] == 0
    assert report["summary"] == {
        "total_requests": 9,
        "first_attempt_successes": 1,
        "eligible_cases_entering_repair": 5,
        "repair_successes": 2,
        "repair_limit_reached": 1,
        "duplicate_candidates": 1,
        "safety_rejections": 2,
        "environment_errors": 1,
        "resource_failures": 1,
        "repair_success_rate": {
            "numerator": 2,
            "denominator": 5,
            "value": 0.4,
            "formula": (
                "repair_successes / eligible_cases_entering_repair"
            ),
        },
    }

    by_id = {case["case_id"]: case for case in report["cases"]}
    assert by_id["D9_05"]["trace"]["attempts"][-1][
        "execution_started"
    ] is False
    assert by_id["D9_06"]["fake_model_generate_calls"] == 0
    assert by_id["D9_08"]["fake_model_generate_calls"] == 0
    assert by_id["D9_09"]["fake_model_generate_calls"] == 3
    assert by_id["D9_09"]["trace"]["attempts"][-1]["model"][
        "http_attempts"
    ] == 3
    assert all(
        case["business_validation_status"] == "not_evaluated"
        for case in report["cases"]
    )
