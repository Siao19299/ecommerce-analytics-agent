"""Strict adapters from guarded SQL results to Day 10 typed inputs."""

from __future__ import annotations

import csv
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError

from src.ecommerce_agent.day10_models import (
    MetricProvenance,
    MonthlyObservation,
    PeriodCompleteness,
)
from src.ecommerce_agent.sql_generation import QueryExecutionResult


class Day10InputErrorCode(str, Enum):
    EXECUTION_FAILED = "execution_failed"
    TRUNCATED_RESULT = "truncated_result"
    MISSING_COLUMN = "missing_column"
    INVALID_PERIOD = "invalid_period"
    INVALID_VALUE = "invalid_value"
    INVALID_OBSERVATION = "invalid_observation"
    UNKNOWN_METRIC = "unknown_metric"


class Day10InputError(ValueError):
    def __init__(self, code: Day10InputErrorCode, message: str):
        super().__init__(message)
        self.code = code


def load_metric_provenance(
    root: Path,
    metric_id: str,
    *,
    filters: dict[str, str | int | float | bool] | None = None,
) -> MetricProvenance:
    path = root / "data/metadata/metric_dictionary.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = [
            row for row in csv.DictReader(stream)
            if row["metric_id"] == metric_id
        ]
    if len(rows) != 1:
        raise Day10InputError(
            Day10InputErrorCode.UNKNOWN_METRIC,
            f"指标字典中必须唯一存在 metric_id={metric_id}",
        )
    row = rows[0]
    return MetricProvenance(
        metric_id=metric_id,
        metric_definition=row["definition"],
        time_field=row["default_time_field"],
        filters=filters or {},
    )


def monthly_observations_from_query_result(
    result: QueryExecutionResult,
    *,
    period_column: str,
    value_column: str,
    incomplete_period_reasons: Mapping[date, str],
) -> tuple[MonthlyObservation, ...]:
    """Adapt rows without guessing missing months, zeroes, or completeness."""
    if not result.is_success:
        raise Day10InputError(
            Day10InputErrorCode.EXECUTION_FAILED,
            "只有成功的受保护查询结果可以进入确定性计算",
        )
    if result.rows_truncated:
        raise Day10InputError(
            Day10InputErrorCode.TRUNCATED_RESULT,
            "查询结果已截断，不能据此进行确定性期间分析",
        )
    required = {period_column, value_column}
    missing = required - set(result.columns)
    if missing:
        raise Day10InputError(
            Day10InputErrorCode.MISSING_COLUMN,
            "查询结果缺少列：" + ", ".join(sorted(missing)),
        )

    observations = []
    for row_index, row in enumerate(result.rows):
        raw_period = row.get(period_column)
        try:
            period = (
                raw_period
                if isinstance(raw_period, date)
                else date.fromisoformat(str(raw_period))
            )
        except (TypeError, ValueError) as error:
            raise Day10InputError(
                Day10InputErrorCode.INVALID_PERIOD,
                f"第 {row_index} 行期间不是 ISO 日期",
            ) from error
        raw_value = row.get(value_column)
        if isinstance(raw_value, bool) or not isinstance(
            raw_value, (int, float)
        ):
            raise Day10InputError(
                Day10InputErrorCode.INVALID_VALUE,
                f"第 {row_index} 行指标值不是数值",
            )
        reason = incomplete_period_reasons.get(period)
        try:
            observations.append(
                MonthlyObservation(
                    period=period,
                    value=raw_value,
                    completeness=(
                        PeriodCompleteness.INCOMPLETE
                        if reason is not None
                        else PeriodCompleteness.COMPLETE
                    ),
                    completeness_reason=reason,
                )
            )
        except ValidationError as error:
            raise Day10InputError(
                Day10InputErrorCode.INVALID_OBSERVATION,
                f"第 {row_index} 行不符合月度输入合同",
            ) from error
    return tuple(observations)

