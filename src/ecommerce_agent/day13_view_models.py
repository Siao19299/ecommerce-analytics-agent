"""UI-only projections of validated Day 12 public response models."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.ecommerce_agent.day12_api_models import (
    AnalyzeSuccessResponse,
    ChartArtifact,
    JsonScalar,
    TableArtifact,
)


@dataclass(frozen=True)
class NamedParameterRow:
    name: str
    value: JsonScalar


@dataclass(frozen=True)
class SuccessViewModel:
    workflow_status: str
    run_id: str
    conclusion: str
    sql_statement: str
    parameters: tuple[NamedParameterRow, ...]
    calculation_status: str
    stop_reason: str
    sql_attempt: int
    repaired: bool
    workflow_duration_ms: float
    node_count: int
    sql_attempt_count: int
    repair_attempt_count: int
    execution_started: bool
    rows_truncated: bool


@dataclass(frozen=True)
class ChartViewModel:
    chart_type: str
    title: str
    x_field: str
    y_fields: tuple[str, ...]
    frame: pd.DataFrame
    notes: tuple[str, ...]


def build_table_frame(table: TableArtifact) -> pd.DataFrame:
    """Preserve API row values and declared column order."""
    return pd.DataFrame(list(table.rows), columns=list(table.columns))


def build_chart_view(chart: ChartArtifact) -> ChartViewModel:
    """Adapt precomputed chart rows without aggregating or deriving values."""
    required = (chart.x_field, *chart.y_fields)
    missing = [
        field
        for field in required
        if any(field not in row for row in chart.data)
    ]
    if missing:
        raise ValueError("chart data 缺少声明字段")
    frame = pd.DataFrame(list(chart.data), columns=list(required))
    return ChartViewModel(
        chart_type=chart.chart_type,
        title=chart.title,
        x_field=chart.x_field,
        y_fields=chart.y_fields,
        frame=frame,
        notes=chart.notes,
    )


def build_success_view(response: AnalyzeSuccessResponse) -> SuccessViewModel:
    """Arrange public facts for display without recalculating them."""
    return SuccessViewModel(
        workflow_status=response.status,
        run_id=response.run_id,
        conclusion=response.answer,
        sql_statement=response.sql.statement,
        parameters=tuple(
            NamedParameterRow(name=name, value=value)
            for name, value in sorted(response.sql.parameters.items())
        ),
        calculation_status=response.calculation_status,
        stop_reason=response.stop_reason,
        sql_attempt=response.sql.sql_attempt,
        repaired=response.sql.repaired,
        workflow_duration_ms=response.metadata.workflow_duration_ms,
        node_count=response.metadata.node_count,
        sql_attempt_count=response.metadata.sql_attempt_count,
        repair_attempt_count=response.metadata.repair_attempt_count,
        execution_started=response.metadata.execution_started,
        rows_truncated=response.metadata.rows_truncated,
    )
