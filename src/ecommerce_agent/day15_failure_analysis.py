"""Post-seal failure attribution and mechanical diagnostic cases for Day 15."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field

from src.ecommerce_agent.day11_state import WorkflowStatus
from src.ecommerce_agent.day14_evaluator import (
    CandidateCaseOutput,
    CandidateSafetyOutcome,
    CaseEvaluationResult,
    EvaluationRunReport,
    NumericSource,
    SubmissionSource,
    build_scripted_self_test_outputs,
    evaluate_case_output,
)
from src.ecommerce_agent.day14_schema import (
    CalculationStatus,
    EvaluationCase,
    StrictEvaluationModel,
    load_dataset,
    canonical_dataset_path,
)


class FailureBoundary(StrEnum):
    OUTPUT_CONTRACT = "output_contract"
    RETRIEVAL_EVIDENCE = "retrieval_evidence"
    PLANNING_SEMANTICS = "planning_semantics"
    SQL_GENERATION = "sql_generation"
    CANDIDATE_SAFETY_CHOICE = "candidate_safety_choice"
    SAFETY_GATE = "safety_gate"
    SQL_EXECUTION = "sql_execution"
    REPAIR_CONTROL = "repair_control"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"
    WORKFLOW_STATE = "workflow_state"
    RESULT_SEMANTICS = "result_semantics"
    LINEAGE = "lineage"
    TELEMETRY = "telemetry"
    ENVIRONMENT_PROPAGATION = "environment_propagation"


class FailureDiagnosis(StrictEvaluationModel):
    primary_boundary: FailureBoundary
    affected_boundaries: tuple[FailureBoundary, ...]
    scorer_failure_categories: tuple[str, ...]
    rationale: str


class DiagnosticScenario(StrictEvaluationModel):
    scenario_id: str
    case_id: str
    title: str
    provenance: str = "mechanical_post_seal_diagnostic"
    injected_fault: str
    empirical_model_result: bool = False
    case_contract_correct: bool
    layer_results: dict[str, bool | None] = Field(default_factory=dict)
    diagnosis: FailureDiagnosis


class EmpiricalFailureCase(StrictEvaluationModel):
    """One real-model failure, joined only after the candidate batch was sealed."""

    candidate_version: str
    case_id: str
    category: str
    question: str
    workflow_status: str
    stop_reason: str
    generated_sql: str | None
    layer_results: dict[str, bool | None]
    diagnosis: FailureDiagnosis


_FAILURE_MAP = {
    "named_parameter_contract": FailureBoundary.SQL_GENERATION,
    "sql_behavior": FailureBoundary.SQL_GENERATION,
    "sql_behavior_mismatch": FailureBoundary.SQL_GENERATION,
    "candidate_sql_safety_rejected": FailureBoundary.SAFETY_GATE,
    "execution_trace": FailureBoundary.SQL_EXECUTION,
    "execution_behavior": FailureBoundary.SQL_EXECUTION,
    "execution_behavior_mismatch": FailureBoundary.SQL_EXECUTION,
    "source_result_mismatch": FailureBoundary.RESULT_SEMANTICS,
    "result_mismatch": FailureBoundary.RESULT_SEMANTICS,
    "workflow_status": FailureBoundary.WORKFLOW_STATE,
    "workflow_status_mismatch": FailureBoundary.WORKFLOW_STATE,
    "stop_reason": FailureBoundary.WORKFLOW_STATE,
    "stop_reason_mismatch": FailureBoundary.WORKFLOW_STATE,
    "calculation_status": FailureBoundary.DETERMINISTIC_CALCULATION,
    "calculation_status_mismatch": FailureBoundary.DETERMINISTIC_CALCULATION,
    "safety_behavior": FailureBoundary.CANDIDATE_SAFETY_CHOICE,
    "safety_behavior_mismatch": FailureBoundary.CANDIDATE_SAFETY_CHOICE,
    "lineage": FailureBoundary.LINEAGE,
    "lineage_mismatch": FailureBoundary.LINEAGE,
    "attempt_accounting": FailureBoundary.REPAIR_CONTROL,
    "attempt_accounting_mismatch": FailureBoundary.REPAIR_CONTROL,
    "unexpected_business_result": FailureBoundary.RESULT_SEMANTICS,
}


def _dedupe(values: list[FailureBoundary]) -> tuple[FailureBoundary, ...]:
    return tuple(dict.fromkeys(values))


def diagnose_failure(
    case: EvaluationCase,
    output: CandidateCaseOutput | None,
    result: CaseEvaluationResult | None,
    *,
    output_contract_error: str | None = None,
) -> FailureDiagnosis:
    """Locate the earliest violated boundary while retaining downstream effects."""
    if output_contract_error is not None:
        return FailureDiagnosis(
            primary_boundary=FailureBoundary.OUTPUT_CONTRACT,
            affected_boundaries=(FailureBoundary.OUTPUT_CONTRACT,),
            scorer_failure_categories=("candidate_output_contract",),
            rationale="候选输出未通过结构校验，私有评分不能把缺字段猜成业务结果。",
        )
    if output is None or result is None:
        raise ValueError("validated output and scorer result are required")

    affected = [
        _FAILURE_MAP[name]
        for name in result.failure_categories
        if name in _FAILURE_MAP
    ]
    if result.execution_success and (
        result.source_result_correct is False or result.result_correct is False
    ):
        # Day 14's execution_behavior also includes source-result equality.
        # Preserve technical execution_success as a separate layer and attribute
        # a successfully executed but numerically wrong query to semantics.
        affected.insert(0, FailureBoundary.RESULT_SEMANTICS)
    expected = case.expected_workflow_status
    if expected in {
        WorkflowStatus.NEEDS_CLARIFICATION,
        WorkflowStatus.PLANNING_FAILED,
    } and output.generated_sql is not None:
        affected.insert(0, FailureBoundary.PLANNING_SEMANTICS)
    if expected is WorkflowStatus.SAFETY_REJECTED and output.generated_sql is not None:
        affected.insert(0, FailureBoundary.CANDIDATE_SAFETY_CHOICE)
    if output.repair_attempt_count and (
        expected is WorkflowStatus.RESOURCE_FAILED or not case.allows_repair
    ):
        affected.insert(0, FailureBoundary.REPAIR_CONTROL)
    if expected is WorkflowStatus.ENVIRONMENT_FAILED and output.execution_started:
        affected.insert(0, FailureBoundary.ENVIRONMENT_PROPAGATION)
    if not affected:
        affected.append(FailureBoundary.WORKFLOW_STATE)

    affected_tuple = _dedupe(affected)
    return FailureDiagnosis(
        primary_boundary=affected_tuple[0],
        affected_boundaries=affected_tuple,
        scorer_failure_categories=result.failure_categories,
        rationale=(
            "主分类取最早可观察到的失守边界；其余分类保留为后续影响，"
            "避免把一个根因重复计成多个独立失败。"
        ),
    )


def _layers(result: CaseEvaluationResult) -> dict[str, bool | None]:
    return {
        "sql_generation_success": result.sql_generation_success,
        "sql_behavior_correct": result.sql_behavior_correct,
        "execution_success": result.execution_success,
        "result_correct": result.result_correct,
        "workflow_status_correct": result.status_correct,
        "stop_reason_correct": result.stop_reason_correct,
        "calculation_status_correct": result.calculation_status_correct,
        "safety_correct": result.safety_correct,
        "lineage_correct": result.lineage_correct,
        "attempt_accounting_correct": result.attempt_accounting_correct,
        "business_correct": result.business_correct,
    }


def build_mechanical_diagnostic_scenarios(root: Path) -> tuple[DiagnosticScenario, ...]:
    """Create non-empirical faults after seal to exercise failure attribution."""
    dataset = load_dataset(canonical_dataset_path(root))
    cases = {case.case_id: case for case in dataset.cases}
    oracle = {item.case_id: item for item in build_scripted_self_test_outputs(root)}
    definitions: list[tuple[str, str, str, str, CandidateCaseOutput | None, str | None]] = []

    definitions.append((
        "FA01", "D14_SM_001", "输出格式损坏", "非 JSON 响应无法进入候选输出 Schema", None,
        "JSON decode error",
    ))
    definitions.append((
        "FA02", "D14_SM_001", "可执行题未生成 SQL", "清空 SQL 并错误结束在生成失败",
        oracle["D14_SM_001"].model_copy(update={
            "generated_sql": None, "named_parameters": {}, "sql_attempt_count": 0,
            "workflow_status": WorkflowStatus.SQL_GENERATION_FAILED,
            "stop_reason": "non_json_response", "safety_outcome": CandidateSafetyOutcome.NOT_EVALUATED,
            "execution_started": False, "execution_succeeded": False,
            "result_columns": (), "result_rows": (), "numeric_source": NumericSource.NONE,
        }), None,
    ))
    definitions.append((
        "FA03", "D14_SM_001", "SQL 可执行但结果错误", "用返回 -1 的安全 SQL 替换正确查询",
        oracle["D14_SM_001"].model_copy(update={
            "generated_sql": "SELECT -1 AS delivered_order_count", "named_parameters": {},
            "result_rows": ({"delivered_order_count": -1},),
        }), None,
    ))
    definitions.append((
        "FA04", "D14_SM_002", "真实客户标识误用", "把 customer_unique_id 语义替换为 customer_id",
        oracle["D14_SM_002"].model_copy(update={
            "generated_sql": (
                "SELECT COUNT(DISTINCT o.customer_id) AS delivered_customer_count "
                "FROM fact_orders AS o WHERE o.order_status = 'delivered'"
            ),
            "named_parameters": {},
        }), None,
    ))
    definitions.append((
        "FA05", "D14_RU_001", "销售额歧义被静默选择", "应澄清时生成 SELECT 1",
        oracle["D14_RU_001"].model_copy(update={
            "workflow_status": WorkflowStatus.SUCCEEDED, "stop_reason": "completed",
            "generated_sql": "SELECT 1 AS value", "sql_attempt_count": 1,
            "safety_outcome": CandidateSafetyOutcome.ACCEPTED,
            "execution_started": True, "execution_succeeded": True,
            "result_columns": ("value",), "result_rows": ({"value": 1},),
            "numeric_source": NumericSource.SQL,
        }), None,
    ))
    definitions.append((
        "FA06", "D14_RU_002", "支付金额按品类拆分", "不可支持的指标维度组合仍生成 SQL",
        oracle["D14_RU_002"].model_copy(update={
            "workflow_status": WorkflowStatus.SUCCEEDED, "stop_reason": "completed",
            "generated_sql": "SELECT 1 AS payment_by_category", "sql_attempt_count": 1,
            "safety_outcome": CandidateSafetyOutcome.ACCEPTED,
            "execution_started": True, "execution_succeeded": True,
            "result_columns": ("payment_by_category",),
            "result_rows": ({"payment_by_category": 1},), "numeric_source": NumericSource.SQL,
        }), None,
    ))
    definitions.append((
        "FA07", "D14_RU_006", "破坏性请求未自主拒绝", "候选输出 DELETE，由评分沙箱拦截",
        oracle["D14_RU_006"].model_copy(update={
            "generated_sql": "DELETE FROM fact_orders", "sql_attempt_count": 1,
        }), None,
    ))
    definitions.append((
        "FA08", "D14_RU_006", "安全拒绝进入修复", "安全题错误记录一次修复和模型传输",
        oracle["D14_RU_006"].model_copy(update={
            "repair_attempt_count": 1, "repair_transport_attempt_count": 1,
        }), None,
    ))
    definitions.append((
        "FA09", "D14_MS_005", "缺失比较期被伪装为已计算", "把边界状态改成 computed",
        oracle["D14_MS_005"].model_copy(update={"calculation_status": CalculationStatus.COMPUTED}), None,
    ))
    definitions.append((
        "FA10", "D14_MS_006", "零基期被伪装为相对变化", "把 zero_baseline 改成 computed",
        oracle["D14_MS_006"].model_copy(update={"calculation_status": CalculationStatus.COMPUTED}), None,
    ))
    definitions.append((
        "FA11", "D14_MS_001", "确定性计算 lineage 断裂", "计算父 run_id 指向不存在的运行",
        oracle["D14_MS_001"].model_copy(update={"calculation_parent_run_id": "wrong-parent"}), None,
    ))
    definitions.append((
        "FA12", "D14_RU_009", "资源失败误入修复", "超时题被标成修复耗尽并增加修复轮次",
        oracle["D14_RU_009"].model_copy(update={
            "workflow_status": WorkflowStatus.REPAIR_LIMIT_REACHED,
            "stop_reason": "repair_limit_reached", "repair_attempt_count": 1,
            "repair_transport_attempt_count": 1,
        }), None,
    ))
    definitions.append((
        "FA13", "D14_RU_010", "环境失败错误进入 SQLite", "数据库不可用题却声明已开始执行",
        oracle["D14_RU_010"].model_copy(update={
            "workflow_status": WorkflowStatus.EXECUTION_FAILED,
            "stop_reason": "execution_failed", "execution_started": True,
        }), None,
    ))

    scenarios = []
    for scenario_id, case_id, title, fault, output, contract_error in definitions:
        case = cases[case_id]
        if contract_error is not None:
            diagnosis = diagnose_failure(
                case, None, None, output_contract_error=contract_error
            )
            scenarios.append(DiagnosticScenario(
                scenario_id=scenario_id, case_id=case_id, title=title,
                injected_fault=fault, case_contract_correct=False,
                diagnosis=diagnosis,
            ))
            continue
        assert output is not None
        result = evaluate_case_output(
            root, output, submission_source=SubmissionSource.OFFLINE_CANDIDATE
        )
        scenarios.append(DiagnosticScenario(
            scenario_id=scenario_id, case_id=case_id, title=title,
            injected_fault=fault, case_contract_correct=result.case_contract_correct,
            layer_results=_layers(result),
            diagnosis=diagnose_failure(case, output, result),
        ))
    return tuple(scenarios)


def load_empirical_failures(
    root: Path,
    live_run_directory: Path,
) -> tuple[EmpiricalFailureCase, ...]:
    """Join sealed real-model submissions to private scores for post-hoc analysis.

    This function is intentionally downstream of ``candidate_outputs.seal.json``;
    it refuses incomplete versions so private results cannot leak into generation.
    """

    dataset = load_dataset(canonical_dataset_path(root))
    cases = {case.case_id: case for case in dataset.cases}
    failures: list[EmpiricalFailureCase] = []
    for version in ("direct_sql", "retrieval_sql", "full_agent"):
        directory = live_run_directory / version
        if not (directory / "candidate_outputs.seal.json").is_file():
            raise ValueError(f"unsealed empirical candidate batch: {version}")
        submission_path = directory / "scoring" / "candidate_submission.jsonl"
        report_path = directory / "scoring" / "scored_results.json"
        outputs = {
            output.case_id: output
            for output in (
                CandidateCaseOutput.model_validate_json(line)
                for line in submission_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }
        report = EvaluationRunReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
        for result in report.cases:
            if result.case_contract_correct:
                continue
            case = cases[result.case_id]
            output = outputs[result.case_id]
            failures.append(EmpiricalFailureCase(
                candidate_version=version,
                case_id=result.case_id,
                category=result.category.value,
                question=case.question,
                workflow_status=output.workflow_status.value,
                stop_reason=output.stop_reason,
                generated_sql=output.generated_sql,
                layer_results=_layers(result),
                diagnosis=diagnose_failure(case, output, result),
            ))
    return tuple(failures)


def select_representative_empirical_failures(
    failures: tuple[EmpiricalFailureCase, ...],
    *,
    minimum_count: int = 10,
) -> tuple[EmpiricalFailureCase, ...]:
    """Select diverse real failures without using them to tune the candidate."""

    if minimum_count < 1:
        raise ValueError("minimum_count must be positive")
    selected: list[EmpiricalFailureCase] = []
    seen: set[tuple[str, str]] = set()
    # First cover distinct versions and primary boundaries, then fill by case order.
    for failure in failures:
        key = (failure.candidate_version, failure.diagnosis.primary_boundary.value)
        if key not in seen:
            selected.append(failure)
            seen.add(key)
    for failure in failures:
        if len(selected) >= minimum_count:
            break
        if failure not in selected:
            selected.append(failure)
    if len(selected) < minimum_count:
        raise ValueError("fewer empirical failures than requested")
    return tuple(selected)
