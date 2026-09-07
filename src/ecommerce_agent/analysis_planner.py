import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from src.ecommerce_agent.analysis_plan import (
    PLANNING_DECISION_ADAPTER,
    PlanValidationResult,
    try_validate_planning_decision,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    MessageRole,
    ModelClient,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)


class PlanningErrorType(str, Enum):
    TRANSIENT_MODEL_CLIENT_ERROR = "transient_model_client_error"
    PERMANENT_MODEL_CLIENT_ERROR = "permanent_model_client_error"


@dataclass(frozen=True)
class PlanningLogRecord:
    run_id: str
    created_at: datetime
    model_name: str
    output_attempt: int
    status: str
    latency_ms: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    error_type: str | None
    finish_reason: str | None = None


@dataclass(frozen=True)
class AnalysisPlanningResult:
    run_id: str
    validation: PlanValidationResult | None
    model_responses: tuple[ModelResponse, ...]
    log_records: tuple[PlanningLogRecord, ...]
    client_error_type: PlanningErrorType | None = None
    client_error_message: str | None = None

    @property
    def model_response(self) -> ModelResponse | None:
        return self.model_responses[-1] if self.model_responses else None

    @property
    def is_success(self) -> bool:
        return (
            self.validation is not None
            and self.validation.is_success
            and self.client_error_type is None
        )


@dataclass
class AnalysisPlanner:
    client: ModelClient
    config: ModelConfig
    metric_catalog: MetricCatalog
    max_output_corrections: int = 1

    def __post_init__(self):
        if self.max_output_corrections < 0:
            raise ValueError("max_output_corrections 不得为负数")

    def create_plan(self, question: str) -> AnalysisPlanningResult:
        if not question.strip():
            raise ValueError("经营问题不能为空")

        run_id = uuid4().hex
        messages = self._build_messages(question)
        responses = []
        log_records = []
        validation = None

        for correction_attempt in range(
            self.max_output_corrections + 1
        ):
            try:
                response = self.client.generate(messages, self.config)
            except TransientModelError:
                return self._client_failure_result(
                    run_id=run_id,
                    responses=responses,
                    log_records=log_records,
                    output_attempt=correction_attempt + 1,
                    error_type=(
                        PlanningErrorType.TRANSIENT_MODEL_CLIENT_ERROR
                    ),
                    message="模型服务在有限重试后仍不可用",
                )
            except PermanentModelError:
                return self._client_failure_result(
                    run_id=run_id,
                    responses=responses,
                    log_records=log_records,
                    output_attempt=correction_attempt + 1,
                    error_type=(
                        PlanningErrorType.PERMANENT_MODEL_CLIENT_ERROR
                    ),
                    message="模型凭据或配置无效",
                )
            responses.append(response)
            validation = try_validate_planning_decision(
                response.content,
                self.metric_catalog,
            )
            log_records.append(
                PlanningLogRecord(
                    run_id=run_id,
                    created_at=datetime.now(timezone.utc),
                    model_name=response.model_name,
                    output_attempt=correction_attempt + 1,
                    status=(
                        "succeeded"
                        if validation.is_success
                        else "failed"
                    ),
                    latency_ms=response.latency_ms,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    error_type=(
                        None
                        if validation.error_type is None
                        else validation.error_type.value
                    ),
                    finish_reason=response.finish_reason,
                )
            )
            if validation.is_success:
                break
            if correction_attempt < self.max_output_corrections:
                messages = self._add_correction_message(
                    messages,
                    validation,
                )

        assert validation is not None
        return AnalysisPlanningResult(
            run_id=run_id,
            validation=validation,
            model_responses=tuple(responses),
            log_records=tuple(log_records),
        )

    def _client_failure_result(
        self,
        run_id: str,
        responses: list[ModelResponse],
        log_records: list[PlanningLogRecord],
        output_attempt: int,
        error_type: PlanningErrorType,
        message: str,
    ) -> AnalysisPlanningResult:
        log_records.append(
            PlanningLogRecord(
                run_id=run_id,
                created_at=datetime.now(timezone.utc),
                model_name=self.config.model_name,
                output_attempt=output_attempt,
                status="failed",
                latency_ms=None,
                prompt_tokens=None,
                completion_tokens=None,
                error_type=error_type.value,
            )
        )
        return AnalysisPlanningResult(
            run_id=run_id,
            validation=None,
            model_responses=tuple(responses),
            log_records=tuple(log_records),
            client_error_type=error_type,
            client_error_message=message,
        )

    def _build_messages(self, question: str) -> tuple[ModelMessage, ...]:
        metric_ids = ", ".join(sorted(self.metric_catalog.metrics))
        dimension_ids = ", ".join(sorted(self.metric_catalog.dimensions))
        json_schema = json.dumps(
            PLANNING_DECISION_ADAPTER.json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_content = (
            "你是电商经营分析计划生成器。"
            "只返回 JSON，不要返回 SQL 或解释。"
            "时间范围明确时返回 status=ready 和 plan。"
            "时间范围缺失时不得猜测，返回 "
            "status=needs_clarification 和 clarification_question。"
            f"可用 metric_id：{metric_ids}。"
            f"可用 dimension_id：{dimension_ids}。"
            'ready 示例：{"status":"ready","plan":{"metrics":'
            '["delivered_gmv"],"dimensions":[],"filters":[],'
            '"time_range":{"mode":"all_data"}}}。'
            '澄清示例：{"status":"needs_clarification",'
            '"clarification_question":"请指定分析时间范围，'
            '或确认使用全部数据。"}。'
            f"输出必须符合以下 JSON Schema：{json_schema}"
        )
        return (
            ModelMessage(
                role=MessageRole.SYSTEM,
                content=system_content,
            ),
            ModelMessage(
                role=MessageRole.USER,
                content=question,
            ),
        )

    @staticmethod
    def _add_correction_message(
        messages: tuple[ModelMessage, ...],
        validation: PlanValidationResult,
    ) -> tuple[ModelMessage, ...]:
        correction = (
            "上次输出未通过验证。"
            f"错误类型：{validation.error_type.value}。"
            f"错误说明：{validation.error_message}。"
            "请重新生成，只返回完整且合法的 AnalysisPlan JSON。"
        )
        return messages + (
            ModelMessage(
                role=MessageRole.USER,
                content=correction,
            ),
        )
