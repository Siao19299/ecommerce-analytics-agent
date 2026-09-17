"""Strict public request and response contracts for the Day 12 API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


QUESTION_MAX_LENGTH = 2000
JsonScalar: TypeAlias = str | int | float | bool | None
JsonRow: TypeAlias = dict[str, JsonScalar]


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalyzeRequest(StrictApiModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    question: str = Field(min_length=1, max_length=QUESTION_MAX_LENGTH)


class HealthChecks(StrictApiModel):
    api_process: Literal["alive"] = "alive"
    analysis_service: Literal["configured"] = "configured"
    external_dependencies: Literal["not_checked"] = "not_checked"


class HealthResponse(StrictApiModel):
    status: Literal["ok"] = "ok"
    service: Literal["ecommerce-analytics-agent"] = (
        "ecommerce-analytics-agent"
    )
    api_version: Literal["day12"] = "day12"
    checks: HealthChecks = Field(default_factory=HealthChecks)


class SqlArtifact(StrictApiModel):
    statement: str = Field(min_length=1)
    parameters: dict[str, JsonScalar]
    sql_attempt: int = Field(ge=1)
    repaired: bool


class TableArtifact(StrictApiModel):
    columns: tuple[str, ...]
    rows: tuple[JsonRow, ...]


class ChartArtifact(StrictApiModel):
    chart_type: Literal["bar", "line"]
    title: str = Field(min_length=1)
    x_field: str = Field(min_length=1)
    y_fields: tuple[str, ...] = Field(min_length=1)
    data: tuple[JsonRow, ...]
    notes: tuple[str, ...] = ()


class CalculationLineageSummary(StrictApiModel):
    parent_run_id: str = Field(min_length=1)
    source_sql_attempt: int = Field(ge=1)
    calculation_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunMetadata(StrictApiModel):
    created_at: datetime
    workflow_duration_ms: float = Field(ge=0)
    node_count: int = Field(ge=0)
    sql_attempt_count: int = Field(ge=0)
    repair_attempt_count: int = Field(ge=0)
    execution_started: bool
    rows_truncated: bool


class AnalyzeSuccessResponse(StrictApiModel):
    status: Literal["succeeded"] = "succeeded"
    run_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    sql: SqlArtifact
    table: TableArtifact
    chart: ChartArtifact
    calculation_status: str = Field(min_length=1)
    stop_reason: str = Field(min_length=1)
    lineage: CalculationLineageSummary
    metadata: RunMetadata

    @model_validator(mode="after")
    def preserve_lineage(self):
        if self.lineage.parent_run_id != self.run_id:
            raise ValueError("calculation parent_run_id 必须等于顶层 run_id")
        if self.lineage.source_sql_attempt != self.sql.sql_attempt:
            raise ValueError("calculation 必须引用最终公开的 SQL attempt")
        return self


class AnalyzeClarificationResponse(StrictApiModel):
    status: Literal["needs_clarification"] = "needs_clarification"
    run_id: str = Field(min_length=1)
    clarification_question: str = Field(min_length=1)
    sql: None = None
    stop_reason: str = Field(min_length=1)
    metadata: RunMetadata


class PublicFailureStatus(str, Enum):
    SAFETY_REJECTED = "safety_rejected"
    RETRIEVAL_FAILED = "retrieval_failed"
    PLANNING_FAILED = "planning_failed"
    SQL_GENERATION_FAILED = "sql_generation_failed"
    RESOURCE_FAILED = "resource_failed"
    ENVIRONMENT_FAILED = "environment_failed"
    EXECUTION_FAILED = "execution_failed"
    REPAIR_FAILED = "repair_failed"
    REPAIR_LIMIT_REACHED = "repair_limit_reached"
    CALCULATION_FAILED = "calculation_failed"
    PRESENTATION_FAILED = "presentation_failed"
    INTERNAL_FAILED = "internal_failed"


class ApiErrorDetail(StrictApiModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool


class ValidationIssue(StrictApiModel):
    location: tuple[str | int, ...]
    error_type: str = Field(min_length=1)


class RequestValidationErrorResponse(StrictApiModel):
    status: Literal["invalid_request"] = "invalid_request"
    run_id: str = Field(min_length=1)
    error: ApiErrorDetail
    issues: tuple[ValidationIssue, ...]


class AnalyzeErrorResponse(StrictApiModel):
    status: PublicFailureStatus
    run_id: str = Field(min_length=1)
    stop_reason: str = Field(min_length=1)
    error: ApiErrorDetail
    metadata: RunMetadata


AnalyzeResponse: TypeAlias = (
    AnalyzeSuccessResponse
    | AnalyzeClarificationResponse
    | AnalyzeErrorResponse
)
