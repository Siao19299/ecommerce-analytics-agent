import json
from pathlib import Path

from src.ecommerce_agent.day14_evaluator import (
    CandidateCaseOutput,
    NumericSource,
    SubmissionSource,
    build_scripted_self_test_outputs,
    evaluate_case_output,
)


PROJECT_ROOT = Path(__file__).parents[1]


def test_blind_manifest_contains_only_case_id_and_question():
    lines = (
        PROJECT_ROOT / "data/evaluation/day14/cases.public.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 60
    for line in lines:
        assert set(json.loads(line)) == {"case_id", "question"}
    joined = "\n".join(lines)
    for forbidden in (
        "sql_reference",
        "expected_workflow_status",
        "expected_calculation_status",
        "comparison_rules",
        "reference_sources",
    ):
        assert forbidden not in joined


def test_self_test_report_has_one_layered_record_per_case_without_accuracy_claim():
    report = json.loads(
        (PROJECT_ROOT / "docs/DAY14_EVALUATOR_SELF_TEST.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["submission_source"] == "scripted_evaluator_self_test"
    assert report["case_count"] == 60
    assert report["per_case_result_count"] == 60
    assert report["case_contract_pass_count"] == 60
    assert report["status_correct_count"] == 60
    assert report["safety_correct_count"] == 60
    assert report["business_correct_count"] is None
    assert report["business_evaluated_count"] == 0
    assert report["candidate_model_runs"] == 0
    assert report["external_api_calls"] == 0
    assert report["candidate_received_reference_assets"] is False
    assert len(report["cases"]) == 60
    required = {
        "sql_generation_success",
        "execution_success",
        "status_correct",
        "result_correct",
        "safety_correct",
        "business_correct",
        "failure_categories",
    }
    assert all(required <= set(case) for case in report["cases"])


def test_result_corruption_is_classified_without_changing_status_score():
    outputs = list(build_scripted_self_test_outputs(PROJECT_ROOT))
    index = next(index for index, item in enumerate(outputs) if item.case_id == "D14_SM_001")
    original = outputs[index]
    corrupted_rows = ({"delivered_order_count": -1},)
    outputs[index] = original.model_copy(update={"result_rows": corrupted_rows})
    case = evaluate_case_output(
        PROJECT_ROOT,
        outputs[index],
        submission_source=SubmissionSource.OFFLINE_CANDIDATE,
    )
    assert case.status_correct is True
    assert case.execution_success is True
    assert case.source_result_correct is True
    assert case.result_correct is False
    assert case.case_contract_correct is False
    assert "result_mismatch" in case.failure_categories


def test_safety_case_cannot_hide_execution_or_repair_attempts():
    outputs = list(build_scripted_self_test_outputs(PROJECT_ROOT))
    index = next(index for index, item in enumerate(outputs) if item.case_id == "D14_RU_006")
    original = outputs[index]
    outputs[index] = original.model_copy(
        update={
            "execution_started": True,
            "repair_attempt_count": 1,
            "repair_transport_attempt_count": 1,
        }
    )
    case = evaluate_case_output(
        PROJECT_ROOT,
        outputs[index],
        submission_source=SubmissionSource.OFFLINE_CANDIDATE,
    )
    assert case.status_correct is True
    assert case.safety_correct is False
    assert case.attempt_accounting_correct is False
    assert "safety_behavior_mismatch" in case.failure_categories
    assert "attempt_accounting_mismatch" in case.failure_categories


def test_attempt_and_transport_counts_remain_separate_fields():
    output = CandidateCaseOutput(
        case_id="D14_SM_001",
        run_id="separate-attempts",
        workflow_status="succeeded",
        stop_reason="repair_succeeded",
        calculation_status="not_applicable",
        generated_sql="SELECT :value AS value",
        named_parameters={"value": 1},
        safety_outcome="accepted",
        execution_started=True,
        execution_succeeded=True,
        result_columns=("value",),
        result_rows=({"value": 1},),
        numeric_source=NumericSource.SQL,
        sql_attempt_count=2,
        repair_attempt_count=1,
        generation_transport_attempt_count=3,
        repair_transport_attempt_count=2,
    )
    assert output.sql_attempt_count == 2
    assert output.repair_attempt_count == 1
    assert output.generation_transport_attempt_count == 3
    assert output.repair_transport_attempt_count == 2
