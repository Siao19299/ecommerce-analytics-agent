import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence
from uuid import uuid4

from src.ecommerce_agent.analysis_plan import (
    PLANNING_DECISION_ADAPTER,
    PlanValidationErrorType,
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
from src.ecommerce_agent.retrieval import RetrievalHit


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
    require_retrieval_grounding: bool = False

    def __post_init__(self):
        if self.max_output_corrections < 0:
            raise ValueError("max_output_corrections 不得为负数")

    def create_plan(
        self,
        question: str,
        retrieval_hits: Sequence[RetrievalHit] = (),
    ) -> AnalysisPlanningResult:
        if not question.strip():
            raise ValueError("经营问题不能为空")

        run_id = uuid4().hex
        messages = self._build_messages(question, retrieval_hits)
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
            if (
                validation.is_success
                and validation.plan is not None
                and self.require_retrieval_grounding
            ):
                validation = self._validate_retrieval_grounding(
                    validation,
                    retrieval_hits,
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

    def _build_messages(
        self,
        question: str,
        retrieval_hits: Sequence[RetrievalHit] = (),
    ) -> tuple[ModelMessage, ...]:
        retrieved_metric_ids = sorted(
            {
                hit.document.identifier
                for hit in retrieval_hits
                if hit.document.document_type == "metric"
            }
        )
        retrieved_dimension_ids = sorted(
            {
                dimension_id
                for hit in retrieval_hits
                for dimension_id in hit.document.available_dimensions
            }
        )
        if retrieval_hits:
            metric_ids = ", ".join(retrieved_metric_ids)
            dimension_ids = ", ".join(retrieved_dimension_ids)
            candidate_instruction = (
                f"本次只允许选择以下已召回 metric_id：{metric_ids}。"
                "不得从召回集合之外选择指标。"
                f"这些指标涉及的 dimension_id：{dimension_ids}。"
                "ready 输出必须包含 evidence_document_ids；"
                "每个所选指标都必须引用其 metric:<metric_id> 文档，"
                "且所有证据 ID 必须来自本次检索候选。"
            )
            if retrieved_metric_ids:
                example_metric = retrieved_metric_ids[0]
                ready_example = json.dumps(
                    {
                        "status": "ready",
                        "plan": {
                            "metrics": [example_metric],
                            "dimensions": [],
                            "filters": [],
                            "time_range": {"mode": "all_data"},
                        },
                        "evidence_document_ids": [
                            f"metric:{example_metric}"
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "。"
            else:
                ready_example = (
                    "本次没有指标候选，不得返回 ready，应请求澄清。"
                )
        else:
            metric_ids = ", ".join(sorted(self.metric_catalog.metrics))
            dimension_ids = ", ".join(
                sorted(self.metric_catalog.dimensions)
            )
            candidate_instruction = (
                f"可用 metric_id：{metric_ids}。"
                f"可用 dimension_id：{dimension_ids}。"
            )
            ready_example = (
                '{"status":"ready","plan":{"metrics":'
                '["delivered_gmv"],"dimensions":[],"filters":[],'
                '"time_range":{"mode":"all_data"}}}。'
            )
        json_schema = json.dumps(
            PLANNING_DECISION_ADAPTER.json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        retrieval_context = json.dumps(
            [
                {
                    "document_id": hit.document.document_id,
                    "rank": hit.rank,
                    "score": hit.score,
                    "document_type": hit.document.document_type,
                    "identifier": hit.document.identifier,
                    "chinese_name": hit.document.chinese_name,
                    "definition": hit.document.description,
                    "formula": hit.document.formula,
                    "grain": hit.document.grain,
                    "tables": hit.document.tables,
                    "fields": hit.document.fields,
                    "default_time_field": (
                        hit.document.default_time_field
                    ),
                    "available_dimensions": (
                        hit.document.available_dimensions
                    ),
                    "constraints": hit.document.constraints,
                }
                for hit in retrieval_hits
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_content = (
            "你是电商经营分析计划生成器。"
            "只返回 JSON，不要返回 SQL 或解释。"
            "检索内容只是候选，不是语义授权；必须依据其中的定义、"
            "公式、允许维度、时间口径和粒度限制制定计划。"
            "时间范围明确时返回 status=ready 和 plan。"
            "时间范围缺失时不得猜测，返回 "
            "status=needs_clarification 和 clarification_question。"
            f"{candidate_instruction}"
            f"本次检索候选：{retrieval_context}。"
            f"ready 示例：{ready_example}"
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
    def _validate_retrieval_grounding(
        validation: PlanValidationResult,
        retrieval_hits: Sequence[RetrievalHit],
    ) -> PlanValidationResult:
        assert validation.plan is not None
        retrieved_ids = {
            hit.document.document_id for hit in retrieval_hits
        }
        evidence_ids = set(validation.evidence_document_ids)
        required_metric_documents = {
            f"metric:{metric_id}"
            for metric_id in validation.plan.metrics
        }
        unknown_evidence = evidence_ids - retrieved_ids
        missing_metric_evidence = required_metric_documents - evidence_ids
        if not evidence_ids or unknown_evidence or missing_metric_evidence:
            details = []
            if not evidence_ids:
                details.append("未提供 evidence_document_ids")
            if unknown_evidence:
                details.append(
                    "证据不在召回集合："
                    + ", ".join(sorted(unknown_evidence))
                )
            if missing_metric_evidence:
                details.append(
                    "所选指标缺少证据："
                    + ", ".join(sorted(missing_metric_evidence))
                )
            return PlanValidationResult(
                plan=validation.plan,
                error_type=PlanValidationErrorType.UNGROUNDED,
                error_message="；".join(details),
                evidence_document_ids=(
                    validation.evidence_document_ids
                ),
            )
        return validation

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
