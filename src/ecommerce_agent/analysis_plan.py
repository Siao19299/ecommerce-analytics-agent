import json
from datetime import date
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from src.ecommerce_agent.metric_catalog import MetricCatalog


class FilterOperator(str, Enum):
    """第一版分析计划允许的过滤操作符。"""

    EQ = "eq"
    IN = "in"


FilterScalar = str | int | float | bool


class FilterCondition(BaseModel):
    """一个显式的普通字段过滤条件。"""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    operator: FilterOperator
    value: FilterScalar | list[FilterScalar]

    @model_validator(mode="after")
    def validate_operator_and_value(self):
        if self.operator == FilterOperator.EQ and isinstance(
            self.value,
            list,
        ):
            raise ValueError("eq 操作符要求 value 是单个值")

        if self.operator == FilterOperator.IN and (
            not isinstance(self.value, list) or not self.value
        ):
            raise ValueError("in 操作符要求 value 是非空列表")

        return self


class TimeRangeMode(str, Enum):
    """时间范围是明确区间，还是用户明确要求全部数据。"""

    BOUNDED = "bounded"
    ALL_DATA = "all_data"


class TimeRange(BaseModel):
    """分析计划中的时间范围。"""

    model_config = ConfigDict(extra="forbid")

    mode: TimeRangeMode
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_mode_and_dates(self):
        if self.mode == TimeRangeMode.BOUNDED:
            if self.start_date is None or self.end_date is None:
                raise ValueError(
                    "bounded 时间范围要求开始和结束日期同时存在"
                )
            if self.start_date > self.end_date:
                raise ValueError("开始日期不得晚于结束日期")

        if self.mode == TimeRangeMode.ALL_DATA and (
            self.start_date is not None or self.end_date is not None
        ):
            raise ValueError("all_data 时间范围不得包含具体日期")

        return self


class AnalysisPlan(BaseModel):
    """模型输出经过验证后形成的最小分析计划。"""

    model_config = ConfigDict(extra="forbid")

    metrics: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterCondition] = Field(default_factory=list)
    time_range: TimeRange

    @model_validator(mode="after")
    def validate_metric_semantics(self, info: ValidationInfo):
        context = info.context or {}
        catalog = context.get("metric_catalog")
        if catalog is None:
            return self
        if not isinstance(catalog, MetricCatalog):
            raise TypeError("metric_catalog 必须是 MetricCatalog")

        unknown_metrics = set(self.metrics) - set(catalog.metrics)
        if unknown_metrics:
            metric_ids = ", ".join(sorted(unknown_metrics))
            raise ValueError(f"分析计划包含未知指标：{metric_ids}")

        requested_dimensions = set(self.dimensions)
        requested_dimensions.update(
            condition.field for condition in self.filters
        )
        unknown_dimensions = requested_dimensions - catalog.dimensions
        if unknown_dimensions:
            dimension_ids = ", ".join(sorted(unknown_dimensions))
            raise ValueError(f"分析计划包含未知维度：{dimension_ids}")

        for metric_id in self.metrics:
            unsupported = (
                requested_dimensions
                - catalog.metrics[metric_id].available_dimensions
            )
            if unsupported:
                dimension_ids = ", ".join(sorted(unsupported))
                raise ValueError(
                    f"指标 {metric_id} 不支持维度：{dimension_ids}"
                )

        return self


class ReadyPlanningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready"]
    plan: AnalysisPlan
    evidence_document_ids: list[str] = Field(default_factory=list)


class ClarificationPlanningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["needs_clarification"]
    clarification_question: str = Field(min_length=1)


PlanningDecision = Annotated[
    ReadyPlanningDecision | ClarificationPlanningDecision,
    Field(discriminator="status"),
]
PLANNING_DECISION_ADAPTER = TypeAdapter(PlanningDecision)


def validate_analysis_plan(
    payload: str | dict[str, Any],
    metric_catalog: MetricCatalog,
) -> AnalysisPlan:
    """通过强制提供的指标目录验证模型输出。"""
    context = {"metric_catalog": metric_catalog}
    if isinstance(payload, str):
        return AnalysisPlan.model_validate_json(payload, context=context)
    return AnalysisPlan.model_validate(payload, context=context)


class PlanValidationErrorType(str, Enum):
    NON_JSON = "non_json"
    INVALID_STRUCTURE = "invalid_structure"
    INVALID_SEMANTICS = "invalid_semantics"
    UNGROUNDED = "ungrounded"


@dataclass(frozen=True)
class PlanValidationResult:
    plan: AnalysisPlan | None = None
    clarification_question: str | None = None
    evidence_document_ids: tuple[str, ...] = ()
    error_type: PlanValidationErrorType | None = None
    error_message: str | None = None

    @property
    def is_success(self) -> bool:
        return (
            self.error_type is None
            and (
                self.plan is not None
                or self.clarification_question is not None
            )
        )

    @property
    def needs_clarification(self) -> bool:
        return self.clarification_question is not None


def try_validate_analysis_plan(
    payload: str | dict[str, Any],
    metric_catalog: MetricCatalog,
) -> PlanValidationResult:
    """将不可信模型输出转换为合法计划或受控失败结果。"""
    if isinstance(payload, str):
        try:
            parsed_payload = json.loads(payload)
        except json.JSONDecodeError:
            return PlanValidationResult(
                error_type=PlanValidationErrorType.NON_JSON,
                error_message="模型输出不是有效 JSON",
            )
    else:
        parsed_payload = payload

    try:
        AnalysisPlan.model_validate(parsed_payload)
    except ValidationError as error:
        return PlanValidationResult(
            error_type=PlanValidationErrorType.INVALID_STRUCTURE,
            error_message=_summarize_validation_error(error),
        )

    try:
        plan = validate_analysis_plan(parsed_payload, metric_catalog)
    except ValidationError as error:
        return PlanValidationResult(
            error_type=PlanValidationErrorType.INVALID_SEMANTICS,
            error_message=_summarize_validation_error(error),
        )

    return PlanValidationResult(plan=plan)


def try_validate_planning_decision(
    payload: str | dict[str, Any],
    metric_catalog: MetricCatalog,
) -> PlanValidationResult:
    """验证 ready 分析计划或合法的澄清请求。"""
    if isinstance(payload, str):
        try:
            parsed_payload = json.loads(payload)
        except json.JSONDecodeError:
            return PlanValidationResult(
                error_type=PlanValidationErrorType.NON_JSON,
                error_message="模型输出不是有效 JSON",
            )
    else:
        parsed_payload = payload

    try:
        PLANNING_DECISION_ADAPTER.validate_python(parsed_payload)
    except ValidationError as error:
        return PlanValidationResult(
            error_type=PlanValidationErrorType.INVALID_STRUCTURE,
            error_message=_summarize_validation_error(error),
        )

    try:
        decision = PLANNING_DECISION_ADAPTER.validate_python(
            parsed_payload,
            context={"metric_catalog": metric_catalog},
        )
    except ValidationError as error:
        return PlanValidationResult(
            error_type=PlanValidationErrorType.INVALID_SEMANTICS,
            error_message=_summarize_validation_error(error),
        )

    if isinstance(decision, ClarificationPlanningDecision):
        return PlanValidationResult(
            clarification_question=decision.clarification_question
        )
    return PlanValidationResult(
        plan=decision.plan,
        evidence_document_ids=tuple(decision.evidence_document_ids),
    )


def _summarize_validation_error(error: ValidationError) -> str:
    first_error = error.errors(
        include_url=False,
        include_input=False,
    )[0]
    location = ".".join(str(part) for part in first_error["loc"])
    message = first_error["msg"]
    return f"{location}: {message}" if location else message
