"""Blind-manifest export and layered per-case evaluation for Day 14."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from src.ecommerce_agent.day11_state import WorkflowStatus
from src.ecommerce_agent.day14_comparison import compare_results
from src.ecommerce_agent.day14_schema import (
    BusinessReferenceStatus,
    CalculationStatus,
    ComparisonRules,
    DatasetCategory,
    NumericTolerance,
    OrderKey,
    RowComparison,
    SafetyDecision,
    SortDirection,
    SqlReferenceKind,
    StrictEvaluationModel,
    canonical_dataset_path,
    load_dataset,
    validate_named_parameter_contract,
)
from src.ecommerce_agent.day14_single_metric import DATABASE_RELATIVE_PATH, _sha256_file
from src.ecommerce_agent.sql_generation import execute_read_only_query
from src.ecommerce_agent.sql_safety import build_global_sql_policy, validate_sql_safety


class SubmissionSource(StrEnum):
    SCRIPTED_EVALUATOR_SELF_TEST = "scripted_evaluator_self_test"
    OFFLINE_CANDIDATE = "offline_candidate"
    REAL_MODEL = "real_model"


class CandidateSafetyOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NOT_EVALUATED = "not_evaluated"


class NumericSource(StrEnum):
    NONE = "none"
    SQL = "sql"
    DETERMINISTIC_PYTHON = "deterministic_python"
    MODEL = "model"


class CandidateCaseOutput(StrictEvaluationModel):
    case_id: str
    run_id: str = Field(min_length=1)
    workflow_status: WorkflowStatus
    stop_reason: str = Field(min_length=1)
    calculation_status: CalculationStatus
    generated_sql: str | None = None
    named_parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    safety_outcome: CandidateSafetyOutcome
    execution_started: bool
    execution_succeeded: bool
    result_columns: tuple[str, ...] = ()
    result_rows: tuple[dict[str, Any], ...] = ()
    numeric_source: NumericSource
    sql_attempt_count: int = Field(ge=0)
    repair_attempt_count: int = Field(ge=0)
    generation_transport_attempt_count: int = Field(ge=0)
    repair_transport_attempt_count: int = Field(ge=0)
    calculation_parent_run_id: str | None = None
    calculation_source_sql_attempt: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_output_contract(self):
        if self.generated_sql is None and self.named_parameters:
            raise ValueError("没有 generated_sql 时不得携带命名参数")
        if self.generated_sql is None and self.sql_attempt_count:
            raise ValueError("没有 SQL 时 sql_attempt_count 必须为 0")
        if self.execution_succeeded and not self.execution_started:
            raise ValueError("执行未开始时不能声明执行成功")
        if self.repair_attempt_count == 0 and self.repair_transport_attempt_count:
            raise ValueError("没有修复轮次时不得记录修复模型传输")
        if self.result_rows and not self.result_columns:
            raise ValueError("结果行存在时必须声明结果列")
        return self


class CaseEvaluationResult(StrictEvaluationModel):
    case_id: str
    run_id: str
    category: DatasetCategory
    submission_source: SubmissionSource
    sql_generation_success: bool
    sql_behavior_correct: bool
    execution_started: bool
    execution_success: bool
    execution_behavior_correct: bool
    status_correct: bool
    stop_reason_correct: bool
    calculation_status_correct: bool
    source_result_correct: bool | None
    result_correct: bool | None
    safety_correct: bool
    lineage_correct: bool
    attempt_accounting_correct: bool
    business_correct: bool | None = None
    business_reference_status: BusinessReferenceStatus
    case_contract_correct: bool
    failure_categories: tuple[str, ...]
    diagnostic_notes: tuple[str, ...] = ()


class EvaluationRunReport(StrictEvaluationModel):
    report_schema_version: str = "1.0.0"
    dataset_id: str
    dataset_version: str
    dataset_state: str
    submission_source: SubmissionSource
    submission_sha256: str
    reference_loaded_after_submission: bool
    candidate_received_reference_assets: bool = False
    candidate_model_runs: int
    external_api_calls: int
    case_count: int
    per_case_result_count: int
    case_contract_pass_count: int
    status_correct_count: int
    execution_success_count: int
    result_correct_count: int
    result_evaluated_count: int
    safety_correct_count: int
    business_correct_count: int | None
    business_evaluated_count: int
    failure_category_counts: dict[str, int]
    cases: tuple[CaseEvaluationResult, ...]


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def export_blind_manifest(root: Path) -> Path:
    dataset = load_dataset(canonical_dataset_path(root))
    output = root / "data/evaluation/day14/cases.public.jsonl"
    lines = [
        _json_line({"case_id": case.case_id, "question": case.question})
        for case in dataset.cases
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def write_candidate_outputs(path: Path, outputs: tuple[CandidateCaseOutput, ...]) -> None:
    lines = [_json_line(output.model_dump(mode="json")) for output in outputs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_candidate_outputs(path: Path) -> tuple[CandidateCaseOutput, ...]:
    # This function intentionally reads and validates the complete submission
    # before the caller loads any internal reference assets.
    outputs = tuple(
        CandidateCaseOutput.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    case_ids = [output.case_id for output in outputs]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("candidate submission contains duplicate case_id values")
    return outputs


def _submission_hash(outputs: tuple[CandidateCaseOutput, ...]) -> str:
    payload = "\n".join(_json_line(output.model_dump(mode="json")) for output in outputs) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_rules(columns: tuple[str, ...]) -> ComparisonRules:
    return ComparisonRules(
        expected_columns=columns,
        row_comparison=RowComparison.ORDERED,
        order_keys=(OrderKey(column=columns[0], direction=SortDirection.ASCENDING),),
        numeric_tolerance=NumericTolerance(absolute=1e-6, relative=1e-9),
    )


def _attempt_accounting_correct(case, output: CandidateCaseOutput) -> bool:
    if output.repair_attempt_count > case.safety_expectation.maximum_allowed_repair_attempts:
        return False
    if not case.allows_repair and (
        output.repair_attempt_count or output.repair_transport_attempt_count
    ):
        return False
    if output.generated_sql is None:
        return output.sql_attempt_count == 0
    return output.sql_attempt_count >= 1


def _lineage_correct(case, output: CandidateCaseOutput) -> bool:
    if case.category is not DatasetCategory.MULTI_STEP:
        return True
    return (
        output.calculation_parent_run_id == output.run_id
        and output.calculation_source_sql_attempt is not None
        and 1 <= output.calculation_source_sql_attempt <= output.sql_attempt_count
        and output.numeric_source is NumericSource.DETERMINISTIC_PYTHON
    )


def _evaluate_one(root: Path, case, output: CandidateCaseOutput, source: SubmissionSource):
    failures: list[str] = []
    notes: list[str] = []
    status_correct = output.workflow_status is case.expected_workflow_status
    stop_reason_correct = output.stop_reason in case.allowed_stop_reasons
    calculation_status_correct = (
        output.calculation_status is case.expected_calculation_status
    )
    sql_expected = case.sql_reference.kind is SqlReferenceKind.STANDARD_SQL_FILE
    sql_generation_success = output.generated_sql is not None
    sql_behavior_correct = sql_generation_success == sql_expected
    safety_correct = True
    execution_started = output.execution_started
    execution_success = output.execution_succeeded
    execution_behavior_correct = True
    source_result_correct: bool | None = None
    result_correct: bool | None = None

    independent_execution = None
    if output.generated_sql is not None:
        try:
            validate_named_parameter_contract(output.generated_sql, output.named_parameters)
        except ValueError as error:
            sql_behavior_correct = False
            failures.append("named_parameter_contract_mismatch")
            notes.append(str(error))
        policy = build_global_sql_policy(root, max_rows=1000)
        safety = validate_sql_safety(output.generated_sql, policy)
        if not safety.is_safe:
            safety_correct = False
            failures.append("candidate_sql_safety_rejected")
        elif case.result_reference.path is not None:
            independent_execution = execute_read_only_query(
                root / DATABASE_RELATIVE_PATH,
                output.generated_sql,
                output.named_parameters,
                safety_policy=policy,
            )
            execution_started = independent_execution.execution_started
            execution_success = independent_execution.is_success
    elif sql_expected:
        sql_behavior_correct = False

    if case.safety_expectation.decision is SafetyDecision.REJECT_BEFORE_SQLITE:
        safety_correct = (
            output.safety_outcome is CandidateSafetyOutcome.REJECTED
            and not output.execution_started
            and output.repair_attempt_count == 0
            and output.generated_sql is None
        )
    elif case.safety_expectation.decision is SafetyDecision.NOT_APPLICABLE:
        safety_correct = (
            output.safety_outcome is CandidateSafetyOutcome.NOT_EVALUATED
            and not output.execution_started
            and output.repair_attempt_count == 0
        )
    else:
        safety_correct = safety_correct and output.safety_outcome is CandidateSafetyOutcome.ACCEPTED

    if case.result_reference.path is not None:
        saved = json.loads((root / case.result_reference.path).read_text(encoding="utf-8"))
        if independent_execution is None or not independent_execution.is_success:
            execution_behavior_correct = False
            source_result_correct = False
        else:
            expected_source = saved["source_sql"] if case.category is DatasetCategory.MULTI_STEP else saved
            expected_source_columns = tuple(expected_source["columns"])
            source_rules = (
                _source_rules(expected_source_columns)
                if case.category is DatasetCategory.MULTI_STEP
                else case.comparison_rules
            )
            source_comparison = compare_results(
                expected_columns=expected_source_columns,
                expected_rows=expected_source["rows"],
                actual_columns=independent_execution.columns,
                actual_rows=independent_execution.rows,
                rules=source_rules,
            )
            source_result_correct = source_comparison.result_correct
            execution_behavior_correct = (
                independent_execution.execution_started
                and independent_execution.is_success
                and source_result_correct
                and not independent_execution.rows_truncated
            )
        final_comparison = compare_results(
            expected_columns=saved["columns"],
            expected_rows=saved["rows"],
            actual_columns=output.result_columns,
            actual_rows=output.result_rows,
            rules=case.comparison_rules,
        )
        result_correct = final_comparison.result_correct
        if output.execution_started != execution_started or output.execution_succeeded != execution_success:
            execution_behavior_correct = False
            failures.append("candidate_execution_trace_mismatch")
    else:
        execution_behavior_correct = (
            output.execution_started == case.safety_expectation.expected_execution_started
            and output.execution_succeeded is False
        )
        if output.result_columns or output.result_rows:
            result_correct = False
            failures.append("unexpected_business_result")

    lineage_correct = _lineage_correct(case, output)
    attempt_correct = _attempt_accounting_correct(case, output)
    checks = {
        "sql_behavior_mismatch": sql_behavior_correct,
        "execution_behavior_mismatch": execution_behavior_correct,
        "workflow_status_mismatch": status_correct,
        "stop_reason_mismatch": stop_reason_correct,
        "calculation_status_mismatch": calculation_status_correct,
        "safety_behavior_mismatch": safety_correct,
        "lineage_mismatch": lineage_correct,
        "attempt_accounting_mismatch": attempt_correct,
    }
    if source_result_correct is not None:
        checks["source_result_mismatch"] = source_result_correct
    if result_correct is not None:
        checks["result_mismatch"] = result_correct
    for name, passed in checks.items():
        if not passed and name not in failures:
            failures.append(name)
    case_contract_correct = all(checks.values())
    return CaseEvaluationResult(
        case_id=case.case_id,
        run_id=output.run_id,
        category=case.category,
        submission_source=source,
        sql_generation_success=sql_generation_success,
        sql_behavior_correct=sql_behavior_correct,
        execution_started=execution_started,
        execution_success=execution_success,
        execution_behavior_correct=execution_behavior_correct,
        status_correct=status_correct,
        stop_reason_correct=stop_reason_correct,
        calculation_status_correct=calculation_status_correct,
        source_result_correct=source_result_correct,
        result_correct=result_correct,
        safety_correct=safety_correct,
        lineage_correct=lineage_correct,
        attempt_accounting_correct=attempt_correct,
        business_correct=None,
        business_reference_status=case.provenance.business_reference_status,
        case_contract_correct=case_contract_correct,
        failure_categories=tuple(failures),
        diagnostic_notes=tuple(notes),
    )


def evaluate_candidate_outputs(
    root: Path,
    outputs: tuple[CandidateCaseOutput, ...],
    *,
    submission_source: SubmissionSource,
    candidate_model_runs: int,
    external_api_calls: int,
) -> EvaluationRunReport:
    submission_sha256 = _submission_hash(outputs)
    # Reference loading deliberately happens after the validated submission is
    # materialized and hashed above.
    dataset = load_dataset(canonical_dataset_path(root))
    expected_ids = [case.case_id for case in dataset.cases]
    outputs_by_id = {output.case_id: output for output in outputs}
    if set(outputs_by_id) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(outputs_by_id))
        extra = sorted(set(outputs_by_id) - set(expected_ids))
        raise ValueError(f"submission case IDs mismatch; missing={missing}, extra={extra}")
    results = tuple(
        _evaluate_one(root, case, outputs_by_id[case.case_id], submission_source)
        for case in dataset.cases
    )
    failure_counts = Counter(
        failure for result in results for failure in result.failure_categories
    )
    return EvaluationRunReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        dataset_state=dataset.state.value,
        submission_source=submission_source,
        submission_sha256=submission_sha256,
        reference_loaded_after_submission=True,
        candidate_model_runs=candidate_model_runs,
        external_api_calls=external_api_calls,
        case_count=len(dataset.cases),
        per_case_result_count=len(results),
        case_contract_pass_count=sum(result.case_contract_correct for result in results),
        status_correct_count=sum(result.status_correct for result in results),
        execution_success_count=sum(result.execution_success for result in results),
        result_correct_count=sum(result.result_correct is True for result in results),
        result_evaluated_count=sum(result.result_correct is not None for result in results),
        safety_correct_count=sum(result.safety_correct for result in results),
        business_correct_count=None,
        business_evaluated_count=0,
        failure_category_counts=dict(sorted(failure_counts.items())),
        cases=results,
    )


def evaluate_case_output(
    root: Path,
    output: CandidateCaseOutput,
    *,
    submission_source: SubmissionSource = SubmissionSource.OFFLINE_CANDIDATE,
) -> CaseEvaluationResult:
    """Evaluate one already-materialized output for development diagnostics."""
    dataset = load_dataset(canonical_dataset_path(root))
    case = next((case for case in dataset.cases if case.case_id == output.case_id), None)
    if case is None:
        raise ValueError(f"unknown case_id: {output.case_id}")
    return _evaluate_one(root, case, output, submission_source)


def evaluate_submission(
    root: Path,
    submission_path: Path,
    output_path: Path,
    *,
    submission_source: SubmissionSource = SubmissionSource.OFFLINE_CANDIDATE,
    candidate_model_runs: int = 0,
    external_api_calls: int = 0,
) -> EvaluationRunReport:
    outputs = load_candidate_outputs(submission_path)
    report = evaluate_candidate_outputs(
        root,
        outputs,
        submission_source=submission_source,
        candidate_model_runs=candidate_model_runs,
        external_api_calls=external_api_calls,
    )
    output_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report


def build_scripted_self_test_outputs(root: Path) -> tuple[CandidateCaseOutput, ...]:
    """Build an internal oracle replay; never expose it to a candidate system."""
    dataset = load_dataset(canonical_dataset_path(root))
    outputs = []
    for case in dataset.cases:
        sql = None
        parameters = {}
        sql_attempts = 0
        result_columns: tuple[str, ...] = ()
        result_rows: tuple[dict[str, Any], ...] = ()
        execution_started = case.safety_expectation.expected_execution_started
        execution_succeeded = case.result_reference.path is not None
        safety_outcome = CandidateSafetyOutcome.NOT_EVALUATED
        numeric_source = NumericSource.NONE
        parent_run_id = None
        source_attempt = None
        run_id = f"self-test-{case.case_id.lower()}"

        if case.sql_reference.kind is SqlReferenceKind.STANDARD_SQL_FILE:
            sql = (root / case.sql_reference.path).read_text(encoding="utf-8")
            parameters = dict(case.sql_reference.named_parameters)
            sql_attempts = 1
            safety_outcome = CandidateSafetyOutcome.ACCEPTED
        if case.result_reference.path is not None:
            saved = json.loads((root / case.result_reference.path).read_text(encoding="utf-8"))
            result_columns = tuple(saved["columns"])
            result_rows = tuple(saved["rows"])
            numeric_source = (
                NumericSource.DETERMINISTIC_PYTHON
                if case.category is DatasetCategory.MULTI_STEP
                else NumericSource.SQL
            )
        if case.category is DatasetCategory.MULTI_STEP:
            parent_run_id = run_id
            source_attempt = 1
        if case.expected_workflow_status is WorkflowStatus.SAFETY_REJECTED:
            safety_outcome = CandidateSafetyOutcome.REJECTED

        outputs.append(
            CandidateCaseOutput(
                case_id=case.case_id,
                run_id=run_id,
                workflow_status=case.expected_workflow_status,
                stop_reason=case.allowed_stop_reasons[0],
                calculation_status=case.expected_calculation_status,
                generated_sql=sql,
                named_parameters=parameters,
                safety_outcome=safety_outcome,
                execution_started=execution_started,
                execution_succeeded=execution_succeeded,
                result_columns=result_columns,
                result_rows=result_rows,
                numeric_source=numeric_source,
                sql_attempt_count=sql_attempts,
                repair_attempt_count=0,
                generation_transport_attempt_count=0,
                repair_transport_attempt_count=0,
                calculation_parent_run_id=parent_run_id,
                calculation_source_sql_attempt=source_attempt,
            )
        )
    return tuple(outputs)


def run_self_test(root: Path) -> EvaluationRunReport:
    export_blind_manifest(root)
    outputs = build_scripted_self_test_outputs(root)
    report = evaluate_candidate_outputs(
        root,
        outputs,
        submission_source=SubmissionSource.SCRIPTED_EVALUATOR_SELF_TEST,
        candidate_model_runs=0,
        external_api_calls=0,
    )
    output = root / "docs/DAY14_EVALUATOR_SELF_TEST.json"
    output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    root = Path(__file__).parents[2]
    report = run_self_test(root)
    print(
        f"Day 14 evaluator self-test: {report.per_case_result_count}/60 per-case records; "
        f"contract passes={report.case_contract_pass_count}"
    )
    print("Scripted evaluator self-test only; candidate/model accuracy is not reported")


if __name__ == "__main__":
    main()
