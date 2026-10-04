"""Deterministic table, chart, and narrative views for Deterministic analysis results."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.ecommerce_agent.anomaly_detection import AnomalyResult, AnomalyStatus
from src.ecommerce_agent.period_comparison import (
    CalculationStatus,
    ComparabilityStatus,
    ComparisonResult,
)
from src.ecommerce_agent.contribution_analysis import (
    ContributionResult,
    ContributionStatus,
)
from src.ecommerce_agent.analysis_models import CalculationLineage


class ChartType(str, Enum):
    BAR = "bar"
    LINE = "line"


class TableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chart_type: ChartType
    title: str
    x_field: str
    y_fields: tuple[str, ...]
    data: tuple[dict[str, Any], ...]
    notes: tuple[str, ...] = ()


class DeterministicPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: str
    metric_id: str
    metric_definition_source: str
    lineage: CalculationLineage
    table: TableSpec
    chart: ChartSpec
    conclusion: str
    boundary_notes: tuple[str, ...]
    generated_by: str = "deterministic_python_template"


def _number(value: float) -> str:
    return f"{value:.2f}"


def _percent(value: float) -> str:
    return f"{value * 100:.4f}%"


def present_comparison(result: ComparisonResult) -> DeterministicPresentation:
    rows = tuple(
        {
            "role": point.role,
            "period": point.period.isoformat(),
            "value": point.value,
            "completeness": point.completeness.value,
        }
        for point in result.chart_data
    )
    if result.calculation_status in {
        CalculationStatus.MISSING_CURRENT_PERIOD,
        CalculationStatus.MISSING_COMPARISON_PERIOD,
    }:
        conclusion = (
            f"{result.target_period.isoformat()} 的"
            f"{result.analysis_type.value}缺少精确日历比较期或当前期，"
            "因此不可计算；缺失期间未补零，也未使用最近一条记录替代。"
        )
    else:
        assert result.current_value is not None
        assert result.comparison_value is not None
        assert result.absolute_change is not None
        conclusion = (
            f"{result.target_period.isoformat()} 的 {result.metric.metric_id} "
            f"为 {_number(result.current_value)}，比较期 "
            f"{result.comparison_period.isoformat()} 为 "
            f"{_number(result.comparison_value)}，绝对变化为 "
            f"{_number(result.absolute_change)}。"
        )
        if result.relative_change is not None:
            conclusion += f"相对变化为 {_percent(result.relative_change)}。"
        elif result.calculation_status is CalculationStatus.ZERO_BASELINE:
            conclusion += "比较期为零，相对变化不可计算。"
        else:
            conclusion += "比较期为负，不使用正数规模指标的增长率解释。"
        if result.comparability is ComparabilityStatus.COMPARABLE:
            conclusion += "两期通过本次完整性检查，可作标准期间比较。"
        else:
            conclusion += (
                "机械变化已保留，但至少一个期间不完整，"
                "不应解释为标准同比或环比。"
            )
    return DeterministicPresentation(
        analysis_type=result.analysis_type.value,
        metric_id=result.metric.metric_id,
        metric_definition_source=result.metric.definition_source,
        lineage=result.lineage,
        table=TableSpec(
            columns=("role", "period", "value", "completeness"),
            rows=rows,
        ),
        chart=ChartSpec(
            chart_type=ChartType.BAR,
            title=(
                f"{result.metric.metric_id}: "
                f"{result.comparison_period.isoformat()} vs "
                f"{result.target_period.isoformat()}"
            ),
            x_field="period",
            y_fields=("value",),
            data=rows,
            notes=("chart_uses_precomputed_values_only",),
        ),
        conclusion=conclusion,
        boundary_notes=result.boundary_notes,
    )


def present_contribution(
    result: ContributionResult,
) -> DeterministicPresentation:
    rows = tuple(
        {
            "group": row.group,
            "value": row.value,
            "contribution": row.contribution,
            "is_unknown_group": row.is_unknown_group,
        }
        for row in sorted(
            result.rows,
            key=lambda item: (-item.value, item.group),
        )
    )
    if result.calculation_status is ContributionStatus.COMPUTED:
        conclusion = (
            f"{result.scope.period.isoformat()} 的 {result.metric.metric_id} "
            f"同口径分母为 {_number(result.denominator_value)}，"
            f"共保留 {len(result.rows)} 个 {result.dimension} 分组。"
        )
        if result.sum_check.passed:
            conclusion += "未提前舍入的贡献度之和通过 1.0 校验。"
        else:
            conclusion += (
                "分组值没有完全覆盖分母，差额已记录，"
                "不得把当前分组贡献度解释为完整总体。"
            )
    else:
        conclusion = (
            f"{result.scope.period.isoformat()} 的贡献度分母非正，"
            "普通份额贡献度不可计算。"
        )
    if any(row.is_unknown_group for row in result.rows):
        conclusion += "缺失分类已作为 unknown 分组保留。"
    return DeterministicPresentation(
        analysis_type=result.analysis_type,
        metric_id=result.metric.metric_id,
        metric_definition_source=result.metric.definition_source,
        lineage=result.lineage,
        table=TableSpec(
            columns=(
                "group",
                "value",
                "contribution",
                "is_unknown_group",
            ),
            rows=rows,
        ),
        chart=ChartSpec(
            chart_type=ChartType.BAR,
            title=(
                f"{result.scope.period.isoformat()} "
                f"{result.dimension} contribution"
            ),
            x_field="group",
            y_fields=("value", "contribution"),
            data=rows,
            notes=(
                "bar_chart_preferred_for_many_categories",
                "unknown_group_is_not_dropped",
            ),
        ),
        conclusion=conclusion,
        boundary_notes=result.boundary_notes,
    )


def present_anomaly(result: AnomalyResult) -> DeterministicPresentation:
    rows = tuple(
        {
            "period": item.period.isoformat(),
            "value": item.value,
            "completeness": item.completeness.value,
            "role": (
                "current"
                if item.period == result.target_period
                else "baseline_history"
            ),
            "baseline_median": result.baseline.median,
        }
        for item in result.chart_data
    )
    if result.calculation_status is AnomalyStatus.DETECTED:
        assert result.robust_z_score is not None
        conclusion = (
            f"{result.target_period.isoformat()} 的 {result.metric.metric_id} "
            f"被 {result.method.name} 规则标记为异常，稳健 z 分数为 "
            f"{result.robust_z_score:.4f}，阈值为 "
            f"{result.method.threshold:.4f}。该结果只表示偏离规则基线，"
            "不证明异常原因。"
        )
    elif result.calculation_status is AnomalyStatus.NOT_DETECTED:
        conclusion = (
            f"{result.target_period.isoformat()} 未达到预设异常阈值；"
            "这只表示本规则未检测到异常，不证明数据或业务没有问题。"
        )
    else:
        conclusion = (
            f"{result.target_period.isoformat()} 的异常状态为 "
            f"{result.calculation_status.value}，证据不足，"
            "不能表述为未发现异常。"
        )
    return DeterministicPresentation(
        analysis_type=result.analysis_type,
        metric_id=result.metric.metric_id,
        metric_definition_source=result.metric.definition_source,
        lineage=result.lineage,
        table=TableSpec(
            columns=(
                "period",
                "value",
                "completeness",
                "role",
                "baseline_median",
            ),
            rows=rows,
        ),
        chart=ChartSpec(
            chart_type=ChartType.LINE,
            title=f"{result.metric.metric_id} anomaly evidence",
            x_field="period",
            y_fields=("value", "baseline_median"),
            data=rows,
            notes=(
                "current_value_is_not_in_baseline",
                "anomaly_marker_does_not_encode_cause",
            ),
        ),
        conclusion=conclusion,
        boundary_notes=result.boundary_notes,
    )
