"""Tests for strict API service public API contracts."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.ecommerce_agent.api_models import (
    QUESTION_MAX_LENGTH,
    AnalyzeClarificationResponse,
    AnalyzeErrorResponse,
    AnalyzeRequest,
    AnalyzeSuccessResponse,
    ApiErrorDetail,
    CalculationLineageSummary,
    ChartArtifact,
    PublicFailureStatus,
    RunMetadata,
    SqlArtifact,
    TableArtifact,
)


def _metadata() -> RunMetadata:
    return RunMetadata(
        created_at=datetime(2026, 9, 17, tzinfo=timezone.utc),
        workflow_duration_ms=12.5,
        node_count=8,
        sql_attempt_count=1,
        repair_attempt_count=0,
        execution_started=True,
        rows_truncated=False,
    )


def _success_payload() -> dict:
    return {
        "run_id": "run-001",
        "answer": "2018 年 7 月月度 GMV 环比上升。",
        "sql": SqlArtifact(
            statement="SELECT :start_date AS start_date",
            parameters={"start_date": "2018-07-01"},
            sql_attempt=1,
            repaired=False,
        ),
        "table": TableArtifact(
            columns=("period", "value"),
            rows=({"period": "2018-07-01", "value": 120.0},),
        ),
        "chart": ChartArtifact(
            chart_type="bar",
            title="Monthly GMV",
            x_field="period",
            y_fields=("value",),
            data=({"period": "2018-07-01", "value": 120.0},),
        ),
        "calculation_status": "computed",
        "stop_reason": "completed",
        "lineage": CalculationLineageSummary(
            parent_run_id="run-001",
            source_sql_attempt=1,
            calculation_id="run-001-calculation",
            input_sha256="a" * 64,
        ),
        "metadata": _metadata(),
    }


def test_request_strips_whitespace_and_forbids_unexpected_controls():
    request = AnalyzeRequest(question="  比较月度 GMV。  ")

    assert request.question == "比较月度 GMV。"
    with pytest.raises(ValidationError):
        AnalyzeRequest(
            question="比较月度 GMV。",
            max_repair_attempts=99,
        )


@pytest.mark.parametrize(
    "question",
    ["", "   ", "问" * (QUESTION_MAX_LENGTH + 1)],
)
def test_request_rejects_blank_or_excessive_question(question):
    with pytest.raises(ValidationError):
        AnalyzeRequest(question=question)


def test_success_contract_is_explicit_and_preserves_lineage():
    response = AnalyzeSuccessResponse(**_success_payload())
    payload = response.model_dump(mode="json")

    assert payload["status"] == "succeeded"
    assert payload["run_id"] == payload["lineage"]["parent_run_id"]
    assert payload["sql"]["sql_attempt"] == 1
    assert set(payload) == {
        "status",
        "run_id",
        "answer",
        "sql",
        "table",
        "chart",
        "calculation_status",
        "stop_reason",
        "lineage",
        "metadata",
    }
    forbidden = {
        "question",
        "retrieval",
        "analysis_plan",
        "sql_generation_context",
        "node_trace",
        "model_response",
        "database_path",
        "prompt",
    }
    assert forbidden.isdisjoint(payload)


def test_success_contract_rejects_broken_run_or_attempt_lineage():
    bad_run = _success_payload()
    bad_run["run_id"] = "different-run"
    with pytest.raises(ValidationError):
        AnalyzeSuccessResponse(**bad_run)

    bad_attempt = _success_payload()
    bad_attempt["sql"] = bad_attempt["sql"].model_copy(
        update={"sql_attempt": 2}
    )
    with pytest.raises(ValidationError):
        AnalyzeSuccessResponse(**bad_attempt)


def test_clarification_has_question_but_no_sql_or_success_answer():
    response = AnalyzeClarificationResponse(
        run_id="clarify-001",
        clarification_question="销售额具体指哪一种口径？",
        stop_reason="clarification_required",
        metadata=_metadata().model_copy(
            update={"sql_attempt_count": 0, "execution_started": False}
        ),
    )

    assert response.sql is None
    with pytest.raises(ValidationError):
        AnalyzeClarificationResponse(
            run_id="clarify-001",
            clarification_question="销售额具体指哪一种口径？",
            stop_reason="clarification_required",
            metadata=_metadata(),
            answer="伪造结论",
        )


def test_failure_contract_keeps_distinct_status_and_bounded_error():
    response = AnalyzeErrorResponse(
        status=PublicFailureStatus.RESOURCE_FAILED,
        run_id="failure-001",
        stop_reason="resource_failure",
        error=ApiErrorDetail(
            code="query_timeout",
            message="查询超过资源限制。",
            retryable=True,
        ),
        metadata=_metadata(),
    )

    assert response.status is PublicFailureStatus.RESOURCE_FAILED
    assert response.error.code == "query_timeout"
    with pytest.raises(ValidationError):
        ApiErrorDetail(
            code="internal_failure",
            message="x" * 501,
            retryable=False,
        )
