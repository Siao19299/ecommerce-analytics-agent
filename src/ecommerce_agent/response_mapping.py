"""Central Agent workflow workflow-state to API service HTTP response mapping."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus

from src.ecommerce_agent.workflow_state import (
    WorkflowState,
    WorkflowStatus,
)
from src.ecommerce_agent.api_models import (
    AnalyzeClarificationResponse,
    AnalyzeErrorResponse,
    AnalyzeSuccessResponse,
    ApiErrorDetail,
    CalculationLineageSummary,
    ChartArtifact,
    PublicFailureStatus,
    RunMetadata,
    SqlArtifact,
    TableArtifact,
)
from src.ecommerce_agent.http_contract import MappedHttpResponse


class ResponseMappingError(RuntimeError):
    """Raised when a workflow state violates its public response contract."""


@dataclass(frozen=True)
class FailureHttpPolicy:
    http_status: int
    code: str
    public_message: str
    retryable: bool


FAILURE_HTTP_POLICIES: dict[WorkflowStatus, FailureHttpPolicy] = {
    WorkflowStatus.SAFETY_REJECTED: FailureHttpPolicy(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "sql_safety_rejected",
        "生成的查询未通过安全策略，未执行分析。",
        False,
    ),
    WorkflowStatus.RETRIEVAL_FAILED: FailureHttpPolicy(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "retrieval_failed",
        "分析所需信息检索失败。",
        False,
    ),
    WorkflowStatus.PLANNING_FAILED: FailureHttpPolicy(
        HTTPStatus.BAD_GATEWAY,
        "planning_failed",
        "未能生成通过验证的分析计划。",
        False,
    ),
    WorkflowStatus.SQL_GENERATION_FAILED: FailureHttpPolicy(
        HTTPStatus.BAD_GATEWAY,
        "sql_generation_failed",
        "未能生成通过验证的查询。",
        False,
    ),
    WorkflowStatus.RESOURCE_FAILED: FailureHttpPolicy(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "resource_failed",
        "分析受到当前资源限制，未完成执行。",
        True,
    ),
    WorkflowStatus.ENVIRONMENT_FAILED: FailureHttpPolicy(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "environment_failed",
        "分析所需运行环境当前不可用。",
        True,
    ),
    WorkflowStatus.EXECUTION_FAILED: FailureHttpPolicy(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "execution_failed",
        "查询执行未能受控完成。",
        False,
    ),
    WorkflowStatus.REPAIR_FAILED: FailureHttpPolicy(
        HTTPStatus.BAD_GATEWAY,
        "repair_failed",
        "查询修复流程未产生可接受结果。",
        False,
    ),
    WorkflowStatus.REPAIR_LIMIT_REACHED: FailureHttpPolicy(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "repair_limit_reached",
        "查询在有限修复次数内未能成功。",
        False,
    ),
    WorkflowStatus.CALCULATION_FAILED: FailureHttpPolicy(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "calculation_failed",
        "确定性计算未能完成，未生成业务结论。",
        False,
    ),
    WorkflowStatus.PRESENTATION_FAILED: FailureHttpPolicy(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "presentation_failed",
        "分析结果未能转换为稳定展示结构。",
        False,
    ),
    WorkflowStatus.INTERNAL_FAILED: FailureHttpPolicy(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "internal_failed",
        "分析服务发生内部错误。",
        False,
    ),
}


class AgentWorkflowHttpMapper:
    """Project a completed internal state onto a minimal public contract."""

    def map(self, state: WorkflowState) -> MappedHttpResponse:
        if state.status is WorkflowStatus.SUCCEEDED:
            return MappedHttpResponse(
                status_code=HTTPStatus.OK,
                body=self._success(state),
            )
        if state.status is WorkflowStatus.NEEDS_CLARIFICATION:
            return MappedHttpResponse(
                status_code=HTTPStatus.OK,
                body=self._clarification(state),
            )
        policy = FAILURE_HTTP_POLICIES.get(state.status)
        if policy is None:
            raise ResponseMappingError(
                "只允许映射已经终止的 Agent workflow workflow status"
            )
        stop_reason = self._required_stop_reason(state)
        return MappedHttpResponse(
            status_code=policy.http_status,
            body=AnalyzeErrorResponse(
                status=PublicFailureStatus(state.status.value),
                run_id=state.run_id,
                stop_reason=stop_reason,
                error=ApiErrorDetail(
                    code=policy.code,
                    message=policy.public_message,
                    retryable=policy.retryable,
                ),
                metadata=self._metadata(state),
            ),
        )

    def _success(self, state: WorkflowState) -> AnalyzeSuccessResponse:
        if (
            state.presentation is None
            or state.analysis_result is None
            or state.calculation_trace is None
            or state.sql_attempt_trace is None
            or state.execution_result is None
            or not state.sql_attempt_trace.attempts
        ):
            raise ResponseMappingError(
                "成功状态缺少 SQL、计算或展示合同"
            )
        attempt = state.sql_attempt_trace.attempts[-1]
        calculation_status = getattr(
            state.analysis_result,
            "calculation_status",
            None,
        )
        if calculation_status is None:
            raise ResponseMappingError("成功状态缺少 calculation_status")
        if isinstance(calculation_status, Enum):
            calculation_status = calculation_status.value
        presentation = state.presentation
        trace = state.calculation_trace
        return AnalyzeSuccessResponse(
            run_id=state.run_id,
            answer=presentation.conclusion,
            sql=SqlArtifact(
                statement=attempt.candidate_sql,
                parameters=attempt.parameters,
                sql_attempt=attempt.sql_attempt,
                repaired=attempt.repair_attempt is not None,
            ),
            table=TableArtifact(
                columns=presentation.table.columns,
                rows=presentation.table.rows,
            ),
            chart=ChartArtifact(
                chart_type=presentation.chart.chart_type.value,
                title=presentation.chart.title,
                x_field=presentation.chart.x_field,
                y_fields=presentation.chart.y_fields,
                data=presentation.chart.data,
                notes=presentation.chart.notes,
            ),
            calculation_status=str(calculation_status),
            stop_reason=self._required_stop_reason(state),
            lineage=CalculationLineageSummary(
                parent_run_id=trace.parent_run_id,
                source_sql_attempt=trace.source_sql_attempt,
                calculation_id=trace.calculation_id,
                input_sha256=trace.input_sha256,
            ),
            metadata=self._metadata(state),
        )

    def _clarification(
        self,
        state: WorkflowState,
    ) -> AnalyzeClarificationResponse:
        if not (state.clarification_question or "").strip():
            raise ResponseMappingError(
                "澄清状态缺少 clarification_question"
            )
        if state.generated_query is not None or state.sql_attempt_trace is not None:
            raise ResponseMappingError("澄清状态不得包含 SQL 或 SQL trace")
        return AnalyzeClarificationResponse(
            run_id=state.run_id,
            clarification_question=state.clarification_question,
            stop_reason=self._required_stop_reason(state),
            metadata=self._metadata(state),
        )

    @staticmethod
    def _required_stop_reason(state: WorkflowState) -> str:
        if not (state.stop_reason or "").strip():
            raise ResponseMappingError("终止状态缺少 stop_reason")
        return state.stop_reason

    @staticmethod
    def _metadata(state: WorkflowState) -> RunMetadata:
        trace = state.sql_attempt_trace
        attempts = trace.attempts if trace is not None else ()
        return RunMetadata(
            created_at=state.created_at,
            workflow_duration_ms=sum(
                item.duration_ms for item in state.node_trace
            ),
            node_count=len(state.node_trace),
            sql_attempt_count=len(attempts),
            repair_attempt_count=(
                len(trace.repair_model_events) if trace is not None else 0
            ),
            execution_started=any(
                item.execution_started for item in attempts
            ),
            rows_truncated=(
                state.execution_result.rows_truncated
                if state.execution_result is not None
                else False
            ),
        )
