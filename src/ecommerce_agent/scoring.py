"""Post-seal public execution, private scoring, aggregation, and ablation contracts."""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from pydantic import Field

from src.ecommerce_agent.workflow_state import WorkflowStatus
from src.ecommerce_agent.evaluator import (
    CandidateCaseOutput,
    CandidateSafetyOutcome,
    EvaluationRunReport,
    NumericSource,
    SubmissionSource,
    evaluate_submission,
    write_candidate_outputs,
)
from src.ecommerce_agent.evaluation_schema import CalculationStatus, StrictEvaluationModel
from src.ecommerce_agent.batch_runner import (
    BatchCandidateRecord,
    load_batch_candidate_records,
)
from src.ecommerce_agent.baseline_direct_sql import (
    DirectAction,
    DirectAdapterRecord,
    DirectStopReason,
)
from src.ecommerce_agent.agent_adapter import (
    AgentSafetyOutcome,
    FullAgentAdapterRecord,
)
from src.ecommerce_agent.experiment_protocol import CandidateVersion
from src.ecommerce_agent.reproducibility import (
    RunManifest,
    ScoringAuthorization,
    SealedCandidateArtifact,
    authorize_scoring,
    load_public_cases,
    seal_candidate_artifact,
)
from src.ecommerce_agent.baseline_retrieval_sql import RetrievalSqlAdapterRecord
from src.ecommerce_agent.sql_generation import QueryExecutionErrorType, execute_read_only_query
from src.ecommerce_agent.sql_safety import build_global_sql_policy, validate_sql_safety


NORMALIZED_FILENAME = "candidate_submission.jsonl"
NORMALIZED_SEAL_FILENAME = "candidate_submission.seal.json"
SCORED_FILENAME = "scored_results.json"
SUMMARY_FILENAME = "aggregate_summary.json"


class MetricEstimate(StrictEvaluationModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float | None
    wilson_low: float | None
    wilson_high: float | None


class AggregateSlice(StrictEvaluationModel):
    case_count: int
    metrics: dict[str, MetricEstimate]
    failure_category_counts: dict[str, int]


class OperationalSummary(StrictEvaluationModel):
    case_count: int
    latency_available_count: int
    mean_end_to_end_latency_ms: float | None
    median_end_to_end_latency_ms: float | None
    p95_end_to_end_latency_ms: float | None
    model_call_total: int
    cases_with_model_calls: int
    transport_attempt_total: int | None
    prompt_tokens_available_count: int
    prompt_tokens_total: int | None
    completion_tokens_available_count: int
    completion_tokens_total: int | None
    cost_available_count: int
    cost_total: float | None
    cost_currency: str | None


class AggregateSummary(StrictEvaluationModel):
    summary_schema_version: str = "1.0.0"
    experiment_run_id: str
    candidate_version: CandidateVersion
    overall: AggregateSlice
    by_category: dict[str, AggregateSlice]
    operational: OperationalSummary
    business_accuracy_note: str
    uncertainty_note: str


class PairedMetricDifference(StrictEvaluationModel):
    metric: str
    left_label: str
    right_label: str
    paired_case_count: int = Field(ge=1)
    left_correct_count: int = Field(ge=0)
    right_correct_count: int = Field(ge=0)
    improved_count: int = Field(ge=0)
    regressed_count: int = Field(ge=0)
    tied_correct_count: int = Field(ge=0)
    tied_incorrect_count: int = Field(ge=0)
    right_minus_left: float
    bootstrap_low: float
    bootstrap_high: float
    confidence_level: float = 0.95
    bootstrap_resamples: int = Field(ge=1)
    bootstrap_seed: int


class AblationId(StrEnum):
    RETRIEVAL_CONTEXT = "retrieval_context"
    BOUNDED_REPAIR = "bounded_repair"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"
    SAFETY_GATE_OBSERVE_ONLY = "safety_gate_observe_only"


class AblationDefinition(StrictEvaluationModel):
    ablation_id: AblationId
    treatment: str
    control: str
    isolates: str
    known_confounders: tuple[str, ...]
    execution_policy: str


REGISTERED_ABLATIONS = (
    AblationDefinition(
        ablation_id=AblationId.RETRIEVAL_CONTEXT,
        treatment="retrieval_sql",
        control="direct_sql",
        isolates="association with the existing Retrieval retrieval package",
        known_confounders=("additional input tokens", "prompt layout change"),
        execution_policy="same read-only sandbox and frozen scoring",
    ),
    AblationDefinition(
        ablation_id=AblationId.BOUNDED_REPAIR,
        treatment="full_agent",
        control="full_agent_without_repair",
        isolates="bounded repair path contribution and cost",
        known_confounders=("different model call count on repair-eligible failures",),
        execution_policy="rerun every case; never rerun failures only",
    ),
    AblationDefinition(
        ablation_id=AblationId.DETERMINISTIC_CALCULATION,
        treatment="full_agent",
        control="full_agent_without_deterministic_calculation",
        isolates="deterministic calculation and boundary-state contribution",
        known_confounders=("different output representation",),
        execution_policy="same source SQL execution; no model-generated numeric substitute",
    ),
    AblationDefinition(
        ablation_id=AblationId.SAFETY_GATE_OBSERVE_ONLY,
        treatment="full_agent",
        control="full_agent_safety_observe_only",
        isolates="safety-gate decisions without executing rejected SQL",
        known_confounders=("counterfactual execution is intentionally unobserved",),
        execution_policy="record gate decision only; never execute rejected SQL",
    ),
)


def _verify_raw_seal(root: Path, seal: SealedCandidateArtifact) -> Path:
    path = root / seal.relative_path
    if not seal.complete_public_case_set:
        raise ValueError("raw candidate batch is incomplete")
    verified = seal_candidate_artifact(root, path, run_id=seal.run_id)
    if verified.sha256 != seal.sha256 or verified.byte_count != seal.byte_count:
        raise ValueError("raw candidate batch changed after sealing")
    return path


def _default_reason(action: DirectAction) -> DirectStopReason:
    return {
        DirectAction.CLARIFY: DirectStopReason.CLARIFICATION_REQUIRED,
        DirectAction.REFUSE: DirectStopReason.SAFETY_FAILURE,
        DirectAction.UNANSWERABLE: DirectStopReason.REQUIRED_DATA_NOT_AVAILABLE,
    }[action]


def _failed_output(record: BatchCandidateRecord, reason: str) -> CandidateCaseOutput:
    return CandidateCaseOutput(
        case_id=record.case_id,
        run_id=record.case_run_id,
        workflow_status=WorkflowStatus.SQL_GENERATION_FAILED,
        stop_reason=reason,
        calculation_status=CalculationStatus.NOT_APPLICABLE,
        safety_outcome=CandidateSafetyOutcome.NOT_EVALUATED,
        execution_started=False,
        execution_succeeded=False,
        numeric_source=NumericSource.NONE,
        sql_attempt_count=0,
        repair_attempt_count=0,
        generation_transport_attempt_count=0,
        repair_transport_attempt_count=0,
    )


def _baseline_output(
    root: Path,
    batch: BatchCandidateRecord,
    adapter: DirectAdapterRecord | RetrievalSqlAdapterRecord,
) -> CandidateCaseOutput:
    if adapter.failure_type is not None or adapter.action is None:
        return _failed_output(batch, adapter.failure_type.value if adapter.failure_type else "invalid")
    if adapter.action is not DirectAction.SQL:
        reason = adapter.reason_code or _default_reason(adapter.action)
        status = {
            DirectAction.CLARIFY: WorkflowStatus.NEEDS_CLARIFICATION,
            DirectAction.REFUSE: WorkflowStatus.SAFETY_REJECTED,
            DirectAction.UNANSWERABLE: WorkflowStatus.PLANNING_FAILED,
        }[adapter.action]
        return CandidateCaseOutput(
            case_id=batch.case_id,
            run_id=batch.case_run_id,
            workflow_status=status,
            stop_reason=reason.value,
            calculation_status=CalculationStatus.NOT_APPLICABLE,
            safety_outcome=(
                CandidateSafetyOutcome.REJECTED
                if adapter.action is DirectAction.REFUSE
                else CandidateSafetyOutcome.NOT_EVALUATED
            ),
            execution_started=False,
            execution_succeeded=False,
            numeric_source=NumericSource.NONE,
            sql_attempt_count=0,
            repair_attempt_count=0,
            generation_transport_attempt_count=adapter.transport_attempt_count,
            repair_transport_attempt_count=0,
        )

    assert adapter.generated_sql is not None
    policy = build_global_sql_policy(root, max_rows=1000)
    safety = validate_sql_safety(adapter.generated_sql, policy)
    if not safety.is_safe:
        return CandidateCaseOutput(
            case_id=batch.case_id,
            run_id=batch.case_run_id,
            workflow_status=WorkflowStatus.SAFETY_REJECTED,
            stop_reason="sql_safety_rejected",
            calculation_status=CalculationStatus.NOT_APPLICABLE,
            generated_sql=adapter.generated_sql,
            named_parameters=adapter.named_parameters,
            safety_outcome=CandidateSafetyOutcome.REJECTED,
            execution_started=False,
            execution_succeeded=False,
            numeric_source=NumericSource.NONE,
            sql_attempt_count=1,
            repair_attempt_count=0,
            generation_transport_attempt_count=adapter.transport_attempt_count,
            repair_transport_attempt_count=0,
        )
    execution = execute_read_only_query(
        root / "data/processed/olist.sqlite3",
        adapter.generated_sql,
        adapter.named_parameters,
        safety_policy=policy,
    )
    if execution.is_success:
        status = WorkflowStatus.SUCCEEDED
        stop_reason = "completed"
    elif execution.error_type is QueryExecutionErrorType.TIMEOUT:
        status = WorkflowStatus.RESOURCE_FAILED
        stop_reason = "sqlite_timeout"
    elif execution.error_type is QueryExecutionErrorType.DATABASE:
        status = WorkflowStatus.ENVIRONMENT_FAILED
        stop_reason = "environment_error"
    else:
        status = WorkflowStatus.EXECUTION_FAILED
        stop_reason = execution.error_type.value if execution.error_type else "execution_failed"
    return CandidateCaseOutput(
        case_id=batch.case_id,
        run_id=batch.case_run_id,
        workflow_status=status,
        stop_reason=stop_reason,
        calculation_status=CalculationStatus.NOT_APPLICABLE,
        generated_sql=adapter.generated_sql,
        named_parameters=adapter.named_parameters,
        safety_outcome=CandidateSafetyOutcome.ACCEPTED,
        execution_started=execution.execution_started,
        execution_succeeded=execution.is_success,
        result_columns=execution.columns,
        result_rows=execution.rows,
        numeric_source=NumericSource.SQL if execution.is_success else NumericSource.NONE,
        sql_attempt_count=1,
        repair_attempt_count=0,
        generation_transport_attempt_count=adapter.transport_attempt_count,
        repair_transport_attempt_count=0,
    )


def _full_agent_output(
    batch: BatchCandidateRecord, adapter: FullAgentAdapterRecord
) -> CandidateCaseOutput:
    if adapter.adapter_failure is not None or adapter.workflow_status is None:
        return _failed_output(batch, adapter.adapter_failure.value if adapter.adapter_failure else "invalid")
    calculation = adapter.calculation_status or CalculationStatus.NOT_APPLICABLE.value
    safety = {
        AgentSafetyOutcome.ACCEPTED: CandidateSafetyOutcome.ACCEPTED,
        AgentSafetyOutcome.REJECTED: CandidateSafetyOutcome.REJECTED,
        AgentSafetyOutcome.NOT_EVALUATED: CandidateSafetyOutcome.NOT_EVALUATED,
    }[adapter.safety_outcome]
    return CandidateCaseOutput(
        case_id=batch.case_id,
        run_id=adapter.run_id,
        workflow_status=WorkflowStatus(adapter.workflow_status),
        stop_reason=adapter.stop_reason or "missing_stop_reason",
        calculation_status=CalculationStatus(calculation),
        generated_sql=adapter.final_sql,
        named_parameters=adapter.final_named_parameters,
        safety_outcome=safety,
        execution_started=adapter.execution_started,
        execution_succeeded=adapter.execution_succeeded,
        result_columns=adapter.result_columns,
        result_rows=adapter.result_rows,
        numeric_source=(
            NumericSource.DETERMINISTIC_PYTHON
            if adapter.calculation_parent_run_id is not None
            else (NumericSource.SQL if adapter.execution_succeeded else NumericSource.NONE)
        ),
        sql_attempt_count=adapter.sql_attempt_count,
        repair_attempt_count=adapter.repair_attempt_count,
        generation_transport_attempt_count=(
            adapter.generation_transport_attempt_count or 0
        ),
        repair_transport_attempt_count=(adapter.repair_transport_attempt_count or 0),
        calculation_parent_run_id=adapter.calculation_parent_run_id,
        calculation_source_sql_attempt=adapter.calculation_source_sql_attempt,
    )


def materialize_scoring_submission(
    root: Path,
    manifest: RunManifest,
    raw_seal: SealedCandidateArtifact,
    output_directory: Path,
) -> tuple[Path, SealedCandidateArtifact, tuple[CandidateCaseOutput, ...]]:
    raw_path = _verify_raw_seal(root, raw_seal)
    records = load_batch_candidate_records(raw_path)
    public_cases = load_public_cases(root)
    if [record.case_id for record in records] != [case.case_id for case in public_cases]:
        raise ValueError("raw records do not match the frozen public case order")
    outputs = []
    for record in records:
        if record.adapter_error is not None:
            outputs.append(_failed_output(record, record.adapter_error))
            continue
        try:
            if manifest.configuration.candidate_version is CandidateVersion.DIRECT_SQL:
                parsed = DirectAdapterRecord.model_validate(record.payload)
                outputs.append(_baseline_output(root, record, parsed))
            elif manifest.configuration.candidate_version is CandidateVersion.RETRIEVAL_SQL:
                parsed = RetrievalSqlAdapterRecord.model_validate(record.payload)
                outputs.append(_baseline_output(root, record, parsed))
            else:
                parsed = FullAgentAdapterRecord.model_validate(record.payload)
                outputs.append(_full_agent_output(record, parsed))
        except ValueError:
            outputs.append(_failed_output(record, "adapter_output_invalid"))
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / NORMALIZED_FILENAME
    write_candidate_outputs(path, tuple(outputs))
    seal = seal_candidate_artifact(root, path, run_id=manifest.run_id)
    (output_directory / NORMALIZED_SEAL_FILENAME).write_text(
        seal.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return path, seal, tuple(outputs)


def score_sealed_submission(
    root: Path,
    manifest: RunManifest,
    submission_path: Path,
    submission_seal: SealedCandidateArtifact,
    output_directory: Path,
    raw_candidate_path: Path,
) -> tuple[EvaluationRunReport, AggregateSummary, ScoringAuthorization]:
    authorization = authorize_scoring(root, submission_seal)
    raw_records = load_batch_candidate_records(raw_candidate_path)
    operational = aggregate_operational(raw_records)
    is_real = manifest.configuration.result_provenance.value == "real_model"
    if is_real and operational.transport_attempt_total is None:
        raise ValueError("real-model scoring requires complete transport-attempt telemetry")
    report = evaluate_submission(
        root,
        submission_path,
        output_directory / SCORED_FILENAME,
        submission_source=(
            SubmissionSource.REAL_MODEL
            if manifest.configuration.result_provenance.value == "real_model"
            else SubmissionSource.OFFLINE_CANDIDATE
        ),
        candidate_model_runs=(
            operational.cases_with_model_calls if is_real else 0
        ),
        external_api_calls=(operational.transport_attempt_total or 0) if is_real else 0,
    )
    summary = aggregate_report(manifest, report, raw_records=raw_records)
    (output_directory / SUMMARY_FILENAME).write_text(
        summary.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return report, summary, authorization


def _wilson(numerator: int, denominator: int) -> MetricEstimate:
    if denominator == 0:
        return MetricEstimate(
            numerator=numerator,
            denominator=0,
            rate=None,
            wilson_low=None,
            wilson_high=None,
        )
    z = 1.959963984540054
    rate = numerator / denominator
    denominator_term = 1 + z * z / denominator
    center = (rate + z * z / (2 * denominator)) / denominator_term
    margin = (
        z
        * math.sqrt(
            rate * (1 - rate) / denominator + z * z / (4 * denominator * denominator)
        )
        / denominator_term
    )
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    if numerator == 0:
        low = 0.0
    if numerator == denominator:
        high = 1.0
    return MetricEstimate(
        numerator=numerator,
        denominator=denominator,
        rate=rate,
        wilson_low=low,
        wilson_high=high,
    )


def _optional_total(values: list[int | float | None]) -> int | float | None:
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def aggregate_operational(
    records: tuple[BatchCandidateRecord, ...],
) -> OperationalSummary:
    latencies = [record.duration_ms for record in records]
    model_calls: list[int] = []
    transport_attempts: list[int | None] = []
    prompt_tokens: list[int | None] = []
    completion_tokens: list[int | None] = []
    costs: list[float | None] = []
    currencies: set[str] = set()
    for record in records:
        payload = record.payload
        calls = payload.get("model_call_count")
        model_calls.append(calls if isinstance(calls, int) and not isinstance(calls, bool) else 0)
        if record.candidate_version is CandidateVersion.FULL_AGENT:
            generation = payload.get("generation_transport_attempt_count")
            repair = payload.get("repair_transport_attempt_count")
            transport_attempts.append(
                generation + repair
                if isinstance(generation, int) and isinstance(repair, int)
                else None
            )
        else:
            value = payload.get("transport_attempt_count")
            transport_attempts.append(value if isinstance(value, int) else None)
        prompt_tokens.append(
            payload.get("prompt_tokens") if isinstance(payload.get("prompt_tokens"), int) else None
        )
        completion_tokens.append(
            payload.get("completion_tokens")
            if isinstance(payload.get("completion_tokens"), int)
            else None
        )
        cost = payload.get("cost")
        costs.append(float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None)
        currency = payload.get("cost_currency")
        if isinstance(currency, str) and currency:
            currencies.add(currency)
    sorted_latencies = sorted(latencies)
    p95 = (
        sorted_latencies[max(0, math.ceil(0.95 * len(sorted_latencies)) - 1)]
        if sorted_latencies
        else None
    )
    return OperationalSummary(
        case_count=len(records),
        latency_available_count=len(latencies),
        mean_end_to_end_latency_ms=(statistics.fmean(latencies) if latencies else None),
        median_end_to_end_latency_ms=(statistics.median(latencies) if latencies else None),
        p95_end_to_end_latency_ms=p95,
        model_call_total=sum(model_calls),
        cases_with_model_calls=sum(value > 0 for value in model_calls),
        transport_attempt_total=(
            int(_optional_total(transport_attempts))
            if _optional_total(transport_attempts) is not None
            else None
        ),
        prompt_tokens_available_count=sum(value is not None for value in prompt_tokens),
        prompt_tokens_total=(
            int(_optional_total(prompt_tokens))
            if _optional_total(prompt_tokens) is not None
            else None
        ),
        completion_tokens_available_count=sum(
            value is not None for value in completion_tokens
        ),
        completion_tokens_total=(
            int(_optional_total(completion_tokens))
            if _optional_total(completion_tokens) is not None
            else None
        ),
        cost_available_count=sum(value is not None for value in costs),
        cost_total=(
            float(_optional_total(costs)) if _optional_total(costs) is not None else None
        ),
        cost_currency=(next(iter(currencies)) if len(currencies) == 1 else None),
    )


def _slice(cases) -> AggregateSlice:
    rows = tuple(cases)
    result_evaluated = [row for row in rows if row.result_correct is not None]
    source_evaluated = [row for row in rows if row.source_result_correct is not None]
    business_evaluated = [row for row in rows if row.business_correct is not None]
    metrics = {
        "case_contract_accuracy": _wilson(sum(row.case_contract_correct for row in rows), len(rows)),
        "sql_generation_rate": _wilson(sum(row.sql_generation_success for row in rows), len(rows)),
        "sql_behavior_accuracy": _wilson(sum(row.sql_behavior_correct for row in rows), len(rows)),
        "sqlite_execution_rate": _wilson(sum(row.execution_started for row in rows), len(rows)),
        "sql_execution_success_rate": _wilson(sum(row.execution_success for row in rows), len(rows)),
        "execution_behavior_accuracy": _wilson(sum(row.execution_behavior_correct for row in rows), len(rows)),
        "source_result_accuracy": _wilson(sum(row.source_result_correct is True for row in source_evaluated), len(source_evaluated)),
        "result_accuracy": _wilson(sum(row.result_correct is True for row in result_evaluated), len(result_evaluated)),
        "workflow_status_accuracy": _wilson(sum(row.status_correct for row in rows), len(rows)),
        "stop_reason_accuracy": _wilson(sum(row.stop_reason_correct for row in rows), len(rows)),
        "calculation_status_accuracy": _wilson(sum(row.calculation_status_correct for row in rows), len(rows)),
        "safety_behavior_accuracy": _wilson(sum(row.safety_correct for row in rows), len(rows)),
        "lineage_accuracy": _wilson(sum(row.lineage_correct for row in rows), len(rows)),
        "attempt_accounting_accuracy": _wilson(sum(row.attempt_accounting_correct for row in rows), len(rows)),
        "business_accuracy": _wilson(sum(row.business_correct is True for row in business_evaluated), len(business_evaluated)),
    }
    failures = Counter(failure for row in rows for failure in row.failure_categories)
    return AggregateSlice(
        case_count=len(rows),
        metrics=metrics,
        failure_category_counts=dict(sorted(failures.items())),
    )


def aggregate_report(
    manifest: RunManifest,
    report: EvaluationRunReport,
    *,
    raw_records: tuple[BatchCandidateRecord, ...] = (),
) -> AggregateSummary:
    categories = sorted({row.category.value for row in report.cases})
    return AggregateSummary(
        experiment_run_id=manifest.run_id,
        candidate_version=manifest.configuration.candidate_version,
        overall=_slice(report.cases),
        by_category={
            category: _slice(row for row in report.cases if row.category.value == category)
            for category in categories
        },
        operational=aggregate_operational(raw_records),
        business_accuracy_note=(
            "No case has an independently verified business reference; business accuracy "
            "is not independently evaluated and has denominator 0."
        ),
        uncertainty_note=(
            "Wilson 95% intervals describe uncertainty over this fixed case sample only. "
            "They do not measure model sampling variability or population generalization."
        ),
    )


def paired_metric_difference(
    left: EvaluationRunReport,
    right: EvaluationRunReport,
    metric: str,
    *,
    left_label: str,
    right_label: str,
    bootstrap_resamples: int = 10_000,
    bootstrap_seed: int = 15_015,
) -> PairedMetricDifference:
    """Compare the same cases and retain only pairs evaluated by both systems."""

    if bootstrap_resamples < 1:
        raise ValueError("bootstrap_resamples must be positive")
    left_rows = {row.case_id: row for row in left.cases}
    right_rows = {row.case_id: row for row in right.cases}
    if set(left_rows) != set(right_rows):
        raise ValueError("paired reports must contain identical case IDs")
    differences: list[int] = []
    pairs: list[tuple[bool, bool]] = []
    for case_id in sorted(left_rows):
        left_value = getattr(left_rows[case_id], metric)
        right_value = getattr(right_rows[case_id], metric)
        if left_value is None or right_value is None:
            continue
        if not isinstance(left_value, bool) or not isinstance(right_value, bool):
            raise ValueError(f"paired metric is not boolean: {metric}")
        pairs.append((left_value, right_value))
        differences.append(int(right_value) - int(left_value))
    if not pairs:
        raise ValueError(f"paired metric has no jointly evaluated cases: {metric}")

    rng = random.Random(bootstrap_seed)
    sample_size = len(differences)
    estimates = sorted(
        sum(differences[rng.randrange(sample_size)] for _ in range(sample_size))
        / sample_size
        for _ in range(bootstrap_resamples)
    )
    low_index = max(0, math.floor(0.025 * bootstrap_resamples))
    high_index = min(bootstrap_resamples - 1, math.ceil(0.975 * bootstrap_resamples) - 1)
    return PairedMetricDifference(
        metric=metric,
        left_label=left_label,
        right_label=right_label,
        paired_case_count=sample_size,
        left_correct_count=sum(left_value for left_value, _ in pairs),
        right_correct_count=sum(right_value for _, right_value in pairs),
        improved_count=sum(not left_value and right_value for left_value, right_value in pairs),
        regressed_count=sum(left_value and not right_value for left_value, right_value in pairs),
        tied_correct_count=sum(left_value and right_value for left_value, right_value in pairs),
        tied_incorrect_count=sum(not left_value and not right_value for left_value, right_value in pairs),
        right_minus_left=sum(differences) / sample_size,
        bootstrap_low=estimates[low_index],
        bootstrap_high=estimates[high_index],
        bootstrap_resamples=bootstrap_resamples,
        bootstrap_seed=bootstrap_seed,
    )
