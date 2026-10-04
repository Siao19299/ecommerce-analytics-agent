"""Deterministic grouped contribution analysis with scope reconciliation."""

from __future__ import annotations

import math
from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.ecommerce_agent.analysis_models import (
    CalculationLineage,
    MetricProvenance,
    PeriodCompleteness,
)


class ContributionStatus(str, Enum):
    COMPUTED = "computed"
    ZERO_DENOMINATOR = "zero_denominator"
    NON_POSITIVE_DENOMINATOR = "non_positive_denominator"


class ContributionScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str = Field(min_length=1)
    period: date
    filters: dict[str, str | int | float | bool] = Field(
        default_factory=dict
    )
    status_scope: str = Field(min_length=1)
    amount_basis: str = Field(min_length=1)
    completeness: PeriodCompleteness

    @field_validator("period")
    @classmethod
    def require_month_start(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("贡献度期间必须使用月份第一天")
        return value


class ContributionComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: str = Field(min_length=1)
    value: float
    is_unknown_group: bool = False

    @field_validator("value", mode="before")
    @classmethod
    def require_finite_number(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("分组值必须是有限数值且不能是布尔值")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError("分组值必须是有限数值且不能是布尔值")
        return converted


class ExternalDenominator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float
    scope: ContributionScope
    input_reference: str = Field(min_length=1)

    @field_validator("value", mode="before")
    @classmethod
    def require_finite_number(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("分母必须是有限数值且不能是布尔值")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError("分母必须是有限数值且不能是布尔值")
        return converted


class ContributionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: MetricProvenance
    dimension: str = Field(min_length=1)
    scope: ContributionScope
    components: tuple[ContributionComponent, ...] = Field(min_length=1)
    lineage: CalculationLineage
    denominator: ExternalDenominator | None = None
    sum_tolerance: float = Field(default=1e-9, gt=0)

    @model_validator(mode="after")
    def validate_scope_and_groups(self):
        if self.metric.metric_id != self.scope.metric_id:
            raise ValueError("指标定义与贡献度分析范围的 metric_id 不一致")
        groups = [component.group for component in self.components]
        if len(groups) != len(set(groups)):
            raise ValueError("贡献度输入包含重复分组")
        if self.denominator is not None and self.denominator.scope != self.scope:
            raise ValueError("贡献度分子和外部分母的指标或分析范围不一致")
        return self


class ContributionRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: str
    value: float
    contribution: float | None
    is_unknown_group: bool


class ContributionSumCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_value_sum: float
    denominator_value: float
    value_difference: float
    contribution_sum: float | None
    tolerance: float
    passed: bool
    explanation: str


class ContributionMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "group_value_divided_by_same_scope_total"
    denominator_source: str
    unknown_group_policy: str = "preserve_explicit_unknown_group"
    rounding_policy: str = "calculate_before_display_rounding"


class ContributionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: str = "contribution"
    metric: MetricProvenance
    dimension: str
    scope: ContributionScope
    lineage: CalculationLineage
    denominator_value: float
    calculation_status: ContributionStatus
    rows: tuple[ContributionRow, ...]
    sum_check: ContributionSumCheck
    method: ContributionMethod
    raw_input: tuple[ContributionComponent, ...]
    chart_data: tuple[ContributionRow, ...]
    boundary_notes: tuple[str, ...]


def calculate_contribution(
    request: ContributionRequest,
) -> ContributionResult:
    """Calculate shares only after numerator/denominator scope validation."""
    component_sum = math.fsum(item.value for item in request.components)
    if request.denominator is None:
        denominator = component_sum
        denominator_source = "derived_from_all_input_components"
    else:
        denominator = request.denominator.value
        denominator_source = request.denominator.input_reference

    if denominator == 0:
        status = ContributionStatus.ZERO_DENOMINATOR
        rows = tuple(
            ContributionRow(
                group=item.group,
                value=item.value,
                contribution=None,
                is_unknown_group=item.is_unknown_group,
            )
            for item in request.components
        )
    elif denominator < 0:
        status = ContributionStatus.NON_POSITIVE_DENOMINATOR
        rows = tuple(
            ContributionRow(
                group=item.group,
                value=item.value,
                contribution=None,
                is_unknown_group=item.is_unknown_group,
            )
            for item in request.components
        )
    else:
        status = ContributionStatus.COMPUTED
        rows = tuple(
            ContributionRow(
                group=item.group,
                value=item.value,
                contribution=item.value / denominator,
                is_unknown_group=item.is_unknown_group,
            )
            for item in request.components
        )

    contribution_sum = (
        math.fsum(
            row.contribution
            for row in rows
            if row.contribution is not None
        )
        if status is ContributionStatus.COMPUTED
        else None
    )
    value_difference = component_sum - denominator
    passed = (
        contribution_sum is not None
        and abs(contribution_sum - 1.0) <= request.sum_tolerance
        and abs(value_difference) <= request.sum_tolerance
    )
    if passed:
        explanation = "分组值与同口径分母对账，未提前舍入的贡献度之和为 1"
    elif contribution_sum is None:
        explanation = "分母非正，普通份额贡献度不可解释"
    else:
        explanation = "分组值未完全覆盖同口径分母，差额已保留"

    notes = [
        "contribution_does_not_establish_business_cause",
        "payment_amount_is_not_allocated_to_product_category",
    ]
    if any(item.value < 0 for item in request.components):
        notes.append("negative_component_requires_non_share_interpretation")
    if request.scope.completeness is PeriodCompleteness.INCOMPLETE:
        notes.append("incomplete_period_not_standard_comparable")

    return ContributionResult(
        metric=request.metric,
        dimension=request.dimension,
        scope=request.scope,
        lineage=request.lineage,
        denominator_value=denominator,
        calculation_status=status,
        rows=rows,
        sum_check=ContributionSumCheck(
            component_value_sum=component_sum,
            denominator_value=denominator,
            value_difference=value_difference,
            contribution_sum=contribution_sum,
            tolerance=request.sum_tolerance,
            passed=passed,
            explanation=explanation,
        ),
        method=ContributionMethod(
            denominator_source=denominator_source,
        ),
        raw_input=request.components,
        chart_data=rows,
        boundary_notes=tuple(notes),
    )
