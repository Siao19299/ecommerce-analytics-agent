"""Tests for the centralized Day 11 to HTTP mapping table."""

from pathlib import Path

import pytest

from src.ecommerce_agent.day11_benchmark import (
    MONTHLY_SQL,
    PARAMETERS,
    PLAN,
    QUESTION,
    REPAIRABLE_SQL,
    _july_only_sql,
    _machine,
)
from src.ecommerce_agent.day11_state import Day11WorkflowState, WorkflowStatus
from src.ecommerce_agent.day12_mapping import (
    FAILURE_HTTP_POLICIES,
    Day12WorkflowHttpMapper,
    ResponseMappingError,
)


ROOT = Path(__file__).parents[1]


EXPECTED_HTTP_STATUS = {
    WorkflowStatus.SAFETY_REJECTED: 422,
    WorkflowStatus.RETRIEVAL_FAILED: 500,
    WorkflowStatus.PLANNING_FAILED: 502,
    WorkflowStatus.SQL_GENERATION_FAILED: 502,
    WorkflowStatus.RESOURCE_FAILED: 503,
    WorkflowStatus.ENVIRONMENT_FAILED: 503,
    WorkflowStatus.EXECUTION_FAILED: 500,
    WorkflowStatus.REPAIR_FAILED: 502,
    WorkflowStatus.REPAIR_LIMIT_REACHED: 500,
    WorkflowStatus.CALCULATION_FAILED: 500,
    WorkflowStatus.PRESENTATION_FAILED: 500,
    WorkflowStatus.INTERNAL_FAILED: 500,
}


def test_failure_mapping_table_is_complete_and_explicit():
    expected_failures = {
        status
        for status in WorkflowStatus
        if status
        not in {
            WorkflowStatus.RUNNING,
            WorkflowStatus.SUCCEEDED,
            WorkflowStatus.NEEDS_CLARIFICATION,
        }
    }

    assert set(FAILURE_HTTP_POLICIES) == expected_failures
    assert {
        status: policy.http_status
        for status, policy in FAILURE_HTTP_POLICIES.items()
    } == EXPECTED_HTTP_STATUS


@pytest.mark.parametrize("status", EXPECTED_HTTP_STATUS)
def test_failures_keep_distinct_public_status_and_hide_internal_message(status):
    state = Day11WorkflowState(
        run_id=f"run-{status.value}",
        question="内部问题不得公开",
        status=status,
        stop_reason=f"controlled-{status.value}",
        error_category="internal-category",
        error_message=(
            "C:\\Users\\private\\database.sqlite3 "
            "SECRET_PROMPT api-key-value"
        ),
    )

    mapped = Day12WorkflowHttpMapper().map(state)
    payload = mapped.body.model_dump(mode="json")
    encoded = mapped.body.model_dump_json()

    assert mapped.status_code == EXPECTED_HTTP_STATUS[status]
    assert payload["status"] == status.value
    assert payload["error"]["code"] == FAILURE_HTTP_POLICIES[status].code
    assert "C:\\\\Users" not in encoded
    assert "SECRET_PROMPT" not in encoded
    assert "api-key-value" not in encoded
    assert "question" not in payload


def test_success_uses_final_repaired_sql_and_preserves_both_lineages():
    state = _machine(
        ROOT,
        sql=REPAIRABLE_SQL,
        repair_sqls=(MONTHLY_SQL,),
    ).run(QUESTION, run_id="api-repair-success")

    mapped = Day12WorkflowHttpMapper().map(state)
    payload = mapped.body.model_dump(mode="json")

    assert mapped.status_code == 200
    assert payload["status"] == "succeeded"
    assert payload["run_id"] == "api-repair-success"
    assert payload["sql"] == {
        "statement": MONTHLY_SQL,
        "parameters": PARAMETERS,
        "sql_attempt": 2,
        "repaired": True,
    }
    assert payload["lineage"]["parent_run_id"] == "api-repair-success"
    assert payload["lineage"]["source_sql_attempt"] == 2
    assert payload["metadata"]["sql_attempt_count"] == 2
    assert payload["metadata"]["repair_attempt_count"] == 1
    assert payload["answer"]
    assert payload["table"]["rows"]
    assert payload["chart"]["data"]


def test_clarification_is_http_success_with_no_sql():
    state = _machine(
        ROOT,
        planning_payload={
            "status": "needs_clarification",
            "clarification_question": "销售额具体指哪一种口径？",
        },
    ).run("分析销售额。", run_id="api-clarification")

    mapped = Day12WorkflowHttpMapper().map(state)
    payload = mapped.body.model_dump(mode="json")

    assert mapped.status_code == 200
    assert payload["status"] == "needs_clarification"
    assert payload["clarification_question"]
    assert payload["sql"] is None
    assert payload["metadata"]["sql_attempt_count"] == 0
    assert payload["metadata"]["execution_started"] is False


def test_missing_comparison_is_successful_workflow_not_server_error():
    state = _machine(ROOT, sql=_july_only_sql()).run(
        QUESTION,
        run_id="api-missing-comparison",
    )

    mapped = Day12WorkflowHttpMapper().map(state)

    assert mapped.status_code == 200
    assert mapped.body.status == "succeeded"
    assert mapped.body.calculation_status == "missing_comparison_period"


def test_running_or_malformed_terminal_state_is_not_publicly_mapped():
    mapper = Day12WorkflowHttpMapper()
    with pytest.raises(ResponseMappingError, match="终止"):
        mapper.map(Day11WorkflowState(run_id="running", question="问题"))

    malformed = Day11WorkflowState(
        run_id="malformed",
        question="问题",
        status=WorkflowStatus.NEEDS_CLARIFICATION,
        stop_reason="clarification_required",
    )
    with pytest.raises(ResponseMappingError, match="clarification_question"):
        mapper.map(malformed)
