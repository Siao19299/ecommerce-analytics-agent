"""Deterministic trailing median/MAD anomaly detection for monthly series."""

from __future__ import annotations

import math
from datetime import date
from enum import Enum
from statistics import median

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.ecommerce_agent.day10_models import (
    CalculationLineage,
    MetricProvenance,
    MonthlyObservation,
    PeriodCompleteness,
)


class AnomalyStatus(str, Enum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    MISSING_CURRENT_PERIOD = "missing_current_period"
    INCOMPLETE_CURRENT_PERIOD = "incomplete_current_period"
    INSUFFICIENT_HISTORY = "insufficient_history"
    NON_CONTIGUOUS_HISTORY = "non_contiguous_history"
    INCOMPLETE_HISTORY = "incomplete_history"
    ZERO_DISPERSION = "zero_dispersion"


class AnomalyDirection(str, Enum):
    ABOVE_BASELINE = "above_baseline"
    BELOW_BASELINE = "below_baseline"
    AT_BASELINE = "at_baseline"


class AnomalyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: MetricProvenance
    target_period: date
    observations: tuple[MonthlyObservation, ...] = Field(min_length=1)
    lineage: CalculationLineage
    history_window: int = Field(default=6, ge=1)
    minimum_history: int = Field(default=5, ge=2)
    threshold: float = Field(default=3.5, gt=0)

    @field_validator("threshold", mode="before")
    @classmethod
    def require_finite_threshold(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("异常阈值必须是有限正数")
        converted = float(value)
        if not math.isfinite(converted) or converted <= 0:
            raise ValueError("异常阈值必须是有限正数")
        return converted

    @model_validator(mode="after")
    def validate_periods_and_parameters(self):
        if self.target_period.day != 1:
            raise ValueError("target_period 必须使用月份第一天")
        periods = [item.period for item in self.observations]
        if len(periods) != len(set(periods)):
            raise ValueError("异常检测输入包含重复期间")
        if periods != sorted(periods):
            raise ValueError("异常检测输入必须按期间升序排列")
        if self.minimum_history > self.history_window:
            raise ValueError("minimum_history 不得大于 history_window")
        return self


class AnomalyMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "trailing_median_mad"
    history_window: int
    minimum_history: int
    threshold: float
    score_formula: str = "0.6745 * (current - median) / MAD"
    current_in_baseline: bool = False
    requires_contiguous_complete_months: bool = True


class AnomalyBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    periods: tuple[date, ...]
    values: tuple[float, ...]
    median: float | None
    mad: float | None


class AnomalyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: str = "anomaly_detection"
    metric: MetricProvenance
    lineage: CalculationLineage
    target_period: date
    current_value: float | None
    calculation_status: AnomalyStatus
    is_anomaly: bool | None
    direction: AnomalyDirection | None
    robust_z_score: float | None
    method: AnomalyMethod
    baseline: AnomalyBaseline
    raw_input: tuple[MonthlyObservation, ...]
    chart_data: tuple[MonthlyObservation, ...]
    boundary_notes: tuple[str, ...]


def _shift_month(period: date, months: int) -> date:
    month_index = period.year * 12 + period.month - 1 + months
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def _empty_result(
    request: AnomalyRequest,
    status: AnomalyStatus,
    *,
    current: MonthlyObservation | None,
    history: tuple[MonthlyObservation, ...] = (),
) -> AnomalyResult:
    return AnomalyResult(
        metric=request.metric,
        lineage=request.lineage,
        target_period=request.target_period,
        current_value=current.value if current else None,
        calculation_status=status,
        is_anomaly=None,
        direction=None,
        robust_z_score=None,
        method=AnomalyMethod(
            history_window=request.history_window,
            minimum_history=request.minimum_history,
            threshold=request.threshold,
        ),
        baseline=AnomalyBaseline(
            periods=tuple(item.period for item in history),
            values=tuple(item.value for item in history),
            median=None,
            mad=None,
        ),
        raw_input=request.observations,
        chart_data=history + ((current,) if current else ()),
        boundary_notes=(
            "insufficient_or_ineligible_data_is_not_no_anomaly",
            "anomaly_does_not_establish_cause",
        ),
    )


def detect_monthly_anomaly(request: AnomalyRequest) -> AnomalyResult:
    """Evaluate one complete month against prior contiguous complete months."""
    by_period = {item.period: item for item in request.observations}
    current = by_period.get(request.target_period)
    if current is None:
        return _empty_result(
            request,
            AnomalyStatus.MISSING_CURRENT_PERIOD,
            current=None,
        )
    if current.completeness is PeriodCompleteness.INCOMPLETE:
        return _empty_result(
            request,
            AnomalyStatus.INCOMPLETE_CURRENT_PERIOD,
            current=current,
        )

    newest_first: list[MonthlyObservation] = []
    interruption: AnomalyStatus | None = None
    for offset in range(1, request.history_window + 1):
        expected = _shift_month(request.target_period, -offset)
        observation = by_period.get(expected)
        if observation is None:
            interruption = AnomalyStatus.NON_CONTIGUOUS_HISTORY
            break
        if observation.completeness is PeriodCompleteness.INCOMPLETE:
            interruption = AnomalyStatus.INCOMPLETE_HISTORY
            break
        newest_first.append(observation)

    history = tuple(reversed(newest_first))
    if len(history) < request.minimum_history:
        status = interruption or AnomalyStatus.INSUFFICIENT_HISTORY
        return _empty_result(request, status, current=current, history=history)

    values = tuple(item.value for item in history)
    baseline_median = float(median(values))
    mad = float(median(abs(value - baseline_median) for value in values))
    method = AnomalyMethod(
        history_window=request.history_window,
        minimum_history=request.minimum_history,
        threshold=request.threshold,
    )
    baseline = AnomalyBaseline(
        periods=tuple(item.period for item in history),
        values=values,
        median=baseline_median,
        mad=mad,
    )
    if mad == 0:
        return AnomalyResult(
            metric=request.metric,
            lineage=request.lineage,
            target_period=request.target_period,
            current_value=current.value,
            calculation_status=AnomalyStatus.ZERO_DISPERSION,
            is_anomaly=None,
            direction=None,
            robust_z_score=None,
            method=method,
            baseline=baseline,
            raw_input=request.observations,
            chart_data=history + (current,),
            boundary_notes=(
                "zero_mad_has_no_implicit_absolute_threshold",
                "anomaly_does_not_establish_cause",
            ),
        )

    score = 0.6745 * (current.value - baseline_median) / mad
    is_anomaly = abs(score) >= request.threshold
    if current.value > baseline_median:
        direction = AnomalyDirection.ABOVE_BASELINE
    elif current.value < baseline_median:
        direction = AnomalyDirection.BELOW_BASELINE
    else:
        direction = AnomalyDirection.AT_BASELINE
    return AnomalyResult(
        metric=request.metric,
        lineage=request.lineage,
        target_period=request.target_period,
        current_value=current.value,
        calculation_status=(
            AnomalyStatus.DETECTED
            if is_anomaly
            else AnomalyStatus.NOT_DETECTED
        ),
        is_anomaly=is_anomaly,
        direction=direction,
        robust_z_score=score,
        method=method,
        baseline=baseline,
        raw_input=request.observations,
        chart_data=history + (current,),
        boundary_notes=(
            "anomaly_does_not_establish_cause",
            "no_model_generated_numeric_result",
        ),
    )

