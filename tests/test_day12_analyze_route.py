"""HTTP orchestration tests for POST /analyze before status mapping."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.ecommerce_agent.day11_state import Day11WorkflowState, WorkflowStatus
from src.ecommerce_agent.day12_api import create_app
from src.ecommerce_agent.day12_api_models import (
    AnalyzeClarificationResponse,
    RunMetadata,
)
from src.ecommerce_agent.day12_http import MappedHttpResponse
from src.ecommerce_agent.day12_mapping import FAILURE_HTTP_POLICIES


class RecordingService:
    def __init__(self, state: Day11WorkflowState) -> None:
        self.state = state
        self.calls: list[tuple[str, str | None]] = []

    def analyze(self, question: str, *, run_id: str | None = None):
        self.calls.append((question, run_id))
        if run_id is not None:
            self.state.run_id = run_id
        return self.state


class RecordingMapper:
    def __init__(self) -> None:
        self.states: list[Day11WorkflowState] = []

    def map(self, state: Day11WorkflowState) -> MappedHttpResponse:
        self.states.append(state)
        return MappedHttpResponse(
            status_code=200,
            body=AnalyzeClarificationResponse(
                run_id=state.run_id,
                clarification_question="请确认销售额口径。",
                stop_reason="clarification_required",
                metadata=RunMetadata(
                    created_at=state.created_at,
                    workflow_duration_ms=0,
                    node_count=0,
                    sql_attempt_count=0,
                    repair_attempt_count=0,
                    execution_started=False,
                    rows_truncated=False,
                ),
            ),
        )


def _client():
    state = Day11WorkflowState(
        run_id="workflow-owned-run-id",
        question="分析销售额。",
        created_at=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    service = RecordingService(state)
    mapper = RecordingMapper()
    return TestClient(create_app(service, mapper)), state, service, mapper


def test_analyze_calls_service_once_and_maps_the_exact_returned_state():
    client, state, service, mapper = _client()

    response = client.post(
        "/analyze",
        json={"question": "  分析销售额。  "},
    )

    assert response.status_code == 200
    assert len(response.json()["run_id"]) == 32
    assert response.json()["sql"] is None
    assert service.calls == [("分析销售额。", response.json()["run_id"])]
    assert mapper.states == [state]
    assert mapper.states[0] is state


def test_invalid_request_stops_before_service_and_mapper():
    client, _, service, mapper = _client()

    blank = client.post("/analyze", json={"question": "   "})
    extra = client.post(
        "/analyze",
        json={"question": "分析订单量。", "skip_safety": True},
    )
    malformed = client.post(
        "/analyze",
        content=b'{"question":',
        headers={"content-type": "application/json"},
    )

    assert [blank.status_code, extra.status_code, malformed.status_code] == [
        422,
        422,
        422,
    ]
    assert service.calls == []
    assert mapper.states == []


def test_openapi_documents_analyze_request_and_public_response_union():
    client, _, _, _ = _client()

    operation = client.get("/openapi.json").json()["paths"]["/analyze"][
        "post"
    ]

    assert operation["requestBody"]["required"] is True
    assert "200" in operation["responses"]


@pytest.mark.parametrize("status,policy", FAILURE_HTTP_POLICIES.items())
def test_default_mapper_applies_failure_http_policy_through_real_route(
    status,
    policy,
):
    state = Day11WorkflowState(
        run_id=f"http-{status.value}",
        question="问题",
        status=status,
        stop_reason=f"controlled-{status.value}",
        error_message="C:\\Users\\private\\secret.txt API_KEY_VALUE",
    )
    service = RecordingService(state)
    client = TestClient(create_app(service))

    response = client.post("/analyze", json={"question": "分析订单量。"})

    assert response.status_code == policy.http_status
    assert response.json()["status"] == status.value
    assert response.json()["error"]["code"] == policy.code
    assert "C:\\Users" not in response.text
    assert "API_KEY_VALUE" not in response.text
    assert service.calls == [("分析订单量。", response.json()["run_id"])]
