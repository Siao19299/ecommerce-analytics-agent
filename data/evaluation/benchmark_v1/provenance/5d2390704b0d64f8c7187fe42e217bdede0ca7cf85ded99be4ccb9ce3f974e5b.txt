"""Deterministic calendar-matched month-over-month and year-over-year tools."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.ecommerce_agent.day10_models import (
    CalculationLineage,
    MetricProvenance,
    MonthlyObservation,
    PeriodCompleteness,
)


class ComparisonType(str, Enum):
    MOM = "month_over_month"
    YOY = "year_over_year"


class CalculationStatus(str, Enum):
    COMPUTED = "computed"
    MISSING_CURRENT_PERIOD = "missing_current_period"
    MISSING_COMPARISON_PERIOD = "missing_comparison_period"
    ZERO_BASELINE = "zero_baseline"
    NEGATIVE_BASELINE = "negative_baseline"


class ComparabilityStatus(str, Enum):
    COMPARABLE = "comparable"
    NOT_COMPARABLE = "not_comparable"
    NOT_APPLICABLE = "not_applicable"


class SignTransition(str, Enum):
    NONE = "none"
    NEGATIVE_TO_ZERO_OR_POSITIVE = "negative_to_zero_or_positive"
    ZERO_OR_POSITIVE_TO_NEGATIVE = "zero_or_positive_to_negative"


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: ComparisonType
    target_period: date
    metric: MetricProvenance
    observations: tuple[MonthlyObservation, ...] = Field(min_length=1)
    lineage: CalculationLineage

    @model_validator(mode="after")
    def validate_months_and_order(self):
        if self.target_period.day != 1:
            raise ValueError("target_period 必须使用月份第一天")
        periods = [item.period for item in self.observations]
        if len(set(periods)) != len(periods):
            raise ValueError("月度输入包含重复期间")
        if periods != sorted(periods):
            raise ValueError("月度输入必须按期间升序排列")
        return self


class ComparisonMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    period_match: str
    absolute_change_formula: str = "current_value - comparison_value"
    relative_change_formula: str = (
        "(current_value - comparison_value) / comparison_value"
    )
    relative_change_policy: str = (
        "computed_only_when_comparison_value_is_positive"
    )


class ChartPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    period: date
    value: float
    completeness: PeriodCompleteness


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: ComparisonType
    metric: MetricProvenance
    lineage: CalculationLineage
    target_period: date
    comparison_period: date
    current_value: float | None
    comparison_value: float | None
    absolute_change: float | None
    relative_change: float | None
    calculation_status: CalculationStatus
    comparability: ComparabilityStatus
    comparability_reasons: tuple[str, ...]
    sign_transition: SignTransition
    method: ComparisonMethod
    raw_input: tuple[MonthlyObservation, ...]
    chart_data: tuple[ChartPoint, ...]
    boundary_notes: tuple[str, ...]


def _shift_year(period: date, years: int) -> date:
    return period.replace(year=period.year + years)


def _shift_month(period: date, months: int) -> date:
    month_index = period.year * 12 + (period.month - 1) + months
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _comparison_period(kind: ComparisonType, target: date) -> date:
    if kind is ComparisonType.MOM:
        return _shift_month(target, -1)
    return _shift_year(target, -1)


def _sign_transition(current: float, baseline: float) -> SignTransition:
    if baseline < 0 <= current:
        return SignTransition.NEGATIVE_TO_ZERO_OR_POSITIVE
    if baseline >= 0 > current:
        return SignTransition.ZERO_OR_POSITIVE_TO_NEGATIVE
    return SignTransition.NONE


def calculate_period_comparison(
    request: ComparisonRequest,
) -> ComparisonResult:
    """Compare an explicit target with its exact prior calendar period."""
    expected_comparison = _comparison_period(
        request.analysis_type,
        request.target_period,
    )
    by_period = {item.period: item for item in request.observations}
    current = by_period.get(request.target_period)
    baseline = by_period.get(expected_comparison)
    period_match = (
        "previous_calendar_month"
        if request.analysis_type is ComparisonType.MOM
        else "same_calendar_month_previous_year"
    )
    method = ComparisonMethod(period_match=period_match)

    if current is None or baseline is None:
        status = (
            CalculationStatus.MISSING_CURRENT_PERIOD
            if current is None
            else CalculationStatus.MISSING_COMPARISON_PERIOD
        )
        missing_reason = (
            "current_period_missing"
            if current is None
            else "comparison_period_missing"
        )
        chart_data = tuple(
            ChartPoint(
                role=role,
                period=item.period,
                value=item.value,
                completeness=item.completeness,
            )
            for role, item in (
                ("comparison", baseline),
                ("current", current),
            )
            if item is not None
        )
        return ComparisonResult(
            analysis_type=request.analysis_type,
            metric=request.metric,
            lineage=request.lineage,
            target_period=request.target_period,
            comparison_period=expected_comparison,
            current_value=current.value if current else None,
            comparison_value=baseline.value if baseline else None,
            absolute_change=None,
            relative_change=None,
            calculation_status=status,
            comparability=ComparabilityStatus.NOT_APPLICABLE,
            comparability_reasons=(missing_reason,),
            sign_transition=SignTransition.NONE,
            method=method,
            raw_input=request.observations,
            chart_data=chart_data,
            boundary_notes=(
                "missing_period_is_not_imputed_as_zero",
                "no_standard_comparison_without_exact_calendar_match",
            ),
        )

    absolute_change = current.value - baseline.value
    transition = _sign_transition(current.value, baseline.value)
    if baseline.value == 0:
        status = CalculationStatus.ZERO_BASELINE
        relative_change = None
    elif baseline.value < 0:
        status = CalculationStatus.NEGATIVE_BASELINE
        relative_change = None
    else:
        status = CalculationStatus.COMPUTED
        relative_change = absolute_change / baseline.value

    reasons = []
    if current.completeness is PeriodCompleteness.INCOMPLETE:
        reasons.append("current_period_incomplete")
    if baseline.completeness is PeriodCompleteness.INCOMPLETE:
        reasons.append("comparison_period_incomplete")
    comparability = (
        ComparabilityStatus.NOT_COMPARABLE
        if reasons
        else ComparabilityStatus.COMPARABLE
    )
    notes = ["an_arithmetic_result_does_not_establish_business_cause"]
    if status is CalculationStatus.ZERO_BASELINE:
        notes.append("relative_change_undefined_for_zero_baseline")
    if status is CalculationStatus.NEGATIVE_BASELINE:
        notes.append("positive_scale_growth_language_not_applied")
    if transition is not SignTransition.NONE:
        notes.append("sign_flip_requires_explicit_interpretation")

    return ComparisonResult(
        analysis_type=request.analysis_type,
        metric=request.metric,
        lineage=request.lineage,
        target_period=request.target_period,
        comparison_period=expected_comparison,
        current_value=current.value,
        comparison_value=baseline.value,
        absolute_change=absolute_change,
        relative_change=relative_change,
        calculation_status=status,
        comparability=comparability,
        comparability_reasons=tuple(reasons),
        sign_transition=transition,
        method=method,
        raw_input=request.observations,
        chart_data=(
            ChartPoint(
                role="comparison",
                period=baseline.period,
                value=baseline.value,
                completeness=baseline.completeness,
            ),
            ChartPoint(
                role="current",
                period=current.period,
                value=current.value,
                completeness=current.completeness,
            ),
        ),
        boundary_notes=tuple(notes),
    )

