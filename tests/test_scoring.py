import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from src.ecommerce_agent.evaluator import EvaluationRunReport
from src.ecommerce_agent.batch_runner import BatchCandidateRecord
from src.ecommerce_agent.baseline_direct_sql import (
    DirectAction,
    DirectAdapterRecord,
    DirectStopReason,
)
from src.ecommerce_agent.experiment_protocol import CandidateVersion
from src.ecommerce_agent.reproducibility import (
    ExperimentConfiguration,
    SamplingConfiguration,
    build_run_manifest,
    seal_candidate_artifact,
)
from src.ecommerce_agent.scoring import (
    REGISTERED_ABLATIONS,
    _baseline_output,
    aggregate_report,
    materialize_scoring_submission,
    paired_metric_difference,
    score_sealed_submission,
)


PROJECT_ROOT = Path(__file__).parents[1]
HASH = "0" * 64


def _manifest(run_id: str):
    return build_run_manifest(
        PROJECT_ROOT,
        ExperimentConfiguration(
            candidate_version="direct_sql",
            purpose="smoke",
            result_provenance="fake_model",
            sampling=SamplingConfiguration(
                provider="offline",
                model_id="scoring-stub",
                temperature=0,
                top_p=1,
                random_seed=15,
                maximum_output_tokens=128,
                per_call_timeout_seconds=5,
            ),
            prompt_sha256=HASH,
            adapter_configuration_sha256=HASH,
        ),
        run_id=run_id,
    )


def _batch_record(case_id, payload):
    encoded = __import__("json").dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    now = datetime.now(timezone.utc)
    return BatchCandidateRecord(
        experiment_run_id="unit",
        case_run_id=f"unit-{case_id.lower()}",
        case_id=case_id,
        ordinal=1,
        candidate_version=CandidateVersion.DIRECT_SQL,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        adapter_record_type="DirectAdapterRecord",
        payload=payload,
        payload_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
    )


def test_baseline_clarification_and_refusal_do_not_enter_sqlite():
    clarify = DirectAdapterRecord(
        case_id="D14_RU_001",
        action=DirectAction.CLARIFY,
        generated_sql=None,
        named_parameters={},
        stop_message="请明确销售额口径。",
        reason_code=DirectStopReason.CLARIFICATION_REQUIRED,
        failure_type=None,
        failure_message=None,
        raw_response="{}",
        raw_response_sha256=hashlib.sha256(b"{}").hexdigest(),
        model_name="fake",
        transport_attempt_count=1,
        model_call_count=1,
    )
    refusal = clarify.model_copy(
        update={
            "case_id": "D14_RU_006",
            "action": DirectAction.REFUSE,
            "reason_code": DirectStopReason.SAFETY_FAILURE,
        }
    )

    clarify_output = _baseline_output(
        PROJECT_ROOT,
        _batch_record("D14_RU_001", clarify.model_dump(mode="json")),
        clarify,
    )
    refusal_output = _baseline_output(
        PROJECT_ROOT,
        _batch_record("D14_RU_006", refusal.model_dump(mode="json")),
        refusal,
    )

    assert clarify_output.workflow_status.value == "needs_clarification"
    assert clarify_output.execution_started is False
    assert refusal_output.workflow_status.value == "safety_rejected"
    assert refusal_output.generated_sql is None
    assert refusal_output.repair_attempt_count == 0


def test_dangerous_baseline_sql_is_blocked_but_not_credited_as_autonomous_refusal():
    adapter = DirectAdapterRecord(
        case_id="D14_RU_006",
        action=DirectAction.SQL,
        generated_sql="DELETE FROM fact_orders",
        named_parameters={},
        stop_message=None,
        reason_code=None,
        failure_type=None,
        failure_message=None,
        raw_response="{}",
        raw_response_sha256=hashlib.sha256(b"{}").hexdigest(),
        model_name="fake",
        transport_attempt_count=1,
        model_call_count=1,
    )
    output = _baseline_output(
        PROJECT_ROOT,
        _batch_record("D14_RU_006", adapter.model_dump(mode="json")),
        adapter,
    )
    assert output.execution_started is False
    assert output.safety_outcome.value == "rejected"
    assert output.generated_sql == "DELETE FROM fact_orders"


@pytest.fixture
def scoring_directory():
    path = PROJECT_ROOT / "data" / "processed" / "evaluation-scoring-tests" / uuid4().hex
    yield path
    if path.exists():
        shutil.rmtree(path)


def test_sealed_raw_batch_materializes_then_scores_only_after_second_seal(
    scoring_directory,
):
    from src.ecommerce_agent.reproducibility import load_public_cases

    manifest = _manifest("scoring-e2e")
    raw_path = scoring_directory / "candidate_outputs.jsonl"
    scoring_directory.mkdir(parents=True)
    lines = []
    now = datetime.now(timezone.utc)
    for ordinal, case in enumerate(load_public_cases(PROJECT_ROOT), 1):
        adapter = DirectAdapterRecord(
            case_id=case.case_id,
            action=None,
            generated_sql=None,
            named_parameters={},
            stop_message=None,
            reason_code=None,
            failure_type="non_json",
            failure_message="candidate response is not valid JSON",
            raw_response="invalid",
            raw_response_sha256=hashlib.sha256(b"invalid").hexdigest(),
            model_name="fake",
            transport_attempt_count=1,
            model_call_count=1,
        )
        payload = adapter.model_dump(mode="json")
        encoded = __import__("json").dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        record = BatchCandidateRecord(
            experiment_run_id=manifest.run_id,
            case_run_id=f"{manifest.run_id}-{case.case_id.lower()}",
            case_id=case.case_id,
            ordinal=ordinal,
            candidate_version=CandidateVersion.DIRECT_SQL,
            started_at=now,
            finished_at=now,
            duration_ms=0,
            adapter_record_type="DirectAdapterRecord",
            payload=payload,
            payload_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        )
        lines.append(__import__("json").dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raw_seal = seal_candidate_artifact(PROJECT_ROOT, raw_path, run_id=manifest.run_id)

    submission, submission_seal, outputs = materialize_scoring_submission(
        PROJECT_ROOT, manifest, raw_seal, scoring_directory
    )
    assert len(outputs) == 60
    assert submission_seal.complete_public_case_set is True

    report, summary, authorization = score_sealed_submission(
        PROJECT_ROOT,
        manifest,
        submission,
        submission_seal,
        scoring_directory,
        raw_path,
    )
    assert authorization.sealed_sha256 == submission_seal.sha256
    assert report.per_case_result_count == 60
    assert summary.overall.metrics["business_accuracy"].denominator == 0
    assert summary.overall.metrics["business_accuracy"].rate is None
    assert summary.operational.case_count == 60
    assert summary.operational.model_call_total == 60
    assert summary.operational.transport_attempt_total == 60


def test_aggregation_is_rebuilt_from_per_case_rows_with_wilson_intervals():
    manifest = _manifest("aggregate-self-test")
    report = EvaluationRunReport.model_validate_json(
        (PROJECT_ROOT / "docs/reports/evaluator_self_test.json").read_text(
            encoding="utf-8"
        )
    )

    summary = aggregate_report(manifest, report)

    metric = summary.overall.metrics["workflow_status_accuracy"]
    assert metric.numerator == metric.denominator == 60
    assert metric.rate == 1
    assert 0 < metric.wilson_low < 1
    assert metric.wilson_high == 1
    assert set(summary.by_category) == {
        "single_metric",
        "aggregate_filter_join",
        "multi_step",
        "risk_ambiguous_unanswerable",
    }


def test_ablation_registry_never_authorizes_execution_of_rejected_sql():
    assert len(REGISTERED_ABLATIONS) == 4
    safety = next(
        item for item in REGISTERED_ABLATIONS if item.ablation_id.value == "safety_gate_observe_only"
    )
    assert "never execute rejected SQL" in safety.execution_policy


def test_paired_bootstrap_uses_joint_case_results_and_is_reproducible():
    live = PROJECT_ROOT / "data/processed/day15/live_main_rerun1"
    if not live.exists():
        return
    direct = EvaluationRunReport.model_validate_json(
        (live / "direct_sql/scoring/scored_results.json").read_text(encoding="utf-8")
    )
    retrieval = EvaluationRunReport.model_validate_json(
        (live / "retrieval_sql/scoring/scored_results.json").read_text(encoding="utf-8")
    )
    first = paired_metric_difference(
        direct, retrieval, "case_contract_correct",
        left_label="direct_sql", right_label="retrieval_sql", bootstrap_resamples=500,
    )
    second = paired_metric_difference(
        direct, retrieval, "case_contract_correct",
        left_label="direct_sql", right_label="retrieval_sql", bootstrap_resamples=500,
    )
    assert first == second
    assert first.paired_case_count == 60
    assert first.right_correct_count - first.left_correct_count == 16
    assert first.improved_count - first.regressed_count == 16
