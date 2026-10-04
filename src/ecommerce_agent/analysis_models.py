"""Stable contracts shared by Deterministic analysis deterministic analysis tools."""

from __future__ import annotations

import math
from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PeriodCompleteness(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class MonthlyObservation(BaseModel):
    """One explicitly observed calendar month; absence is not a zero row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period: date
    value: float
    completeness: PeriodCompleteness
    completeness_reason: str | None = None

    @field_validator("period")
    @classmethod
    def require_month_start(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("月度期间必须使用该月第一天")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def require_finite_number(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("指标值必须是有限数值且不能是布尔值")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError("指标值必须是有限数值且不能是布尔值")
        return converted

    @model_validator(mode="after")
    def require_incomplete_reason(self):
        if (
            self.completeness is PeriodCompleteness.INCOMPLETE
            and not (self.completeness_reason or "").strip()
        ):
            raise ValueError("不完整期间必须提供 completeness_reason")
        return self


class MetricProvenance(BaseModel):
    """Metric semantics must point back to the canonical dictionary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_id: str = Field(min_length=1)
    definition_source: str = "data/metadata/metric_dictionary.csv"
    metric_definition: str = Field(min_length=1)
    time_field: str = Field(min_length=1)
    filters: dict[str, str | int | float | bool] = Field(
        default_factory=dict
    )


class CalculationLineage(BaseModel):
    """Connect a deterministic step to the SQL repair request without mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_run_id: str = Field(min_length=1)
    source_sql_attempt: int = Field(ge=1)
    input_reference: str = Field(min_length=1)
