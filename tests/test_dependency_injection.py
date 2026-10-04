"""Dependency-injection and offline HTTP tests for the API service API."""

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from src.ecommerce_agent.workflow_benchmark import QUESTION, _machine
from src.ecommerce_agent.workflow_state import WorkflowState, WorkflowStatus
from src.ecommerce_agent.api import create_app, get_analysis_service
from src.ecommerce_agent.analysis_service import AgentService
from src.ecommerce_agent.model_client import FakeModelClient


ROOT = Path(__file__).parents[1]
DATABASE = ROOT / "data/processed/olist.sqlite3"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ForbiddenProductionService:
    def __init__(self) -> None:
        self.call_count = 0

    def analyze(self, question: str, *, run_id: str | None = None):
        self.call_count += 1
        raise AssertionError("dependency override 未替换原始服务")


class ScriptedAnalysisService:
    def __init__(self, state: WorkflowState) -> None:
        self.state = state
        self.calls: list[tuple[str, str | None]] = []

    def analyze(self, question: str, *, run_id: str | None = None):
        self.calls.append((question, run_id))
        if run_id is not None:
            self.state.run_id = run_id
        return self.state


def test_fastapi_dependency_override_replaces_service_without_route_changes():
    original = ForbiddenProductionService()
    injected = ScriptedAnalysisService(
        WorkflowState(
            run_id="injected-safety-run",
            question="内部问题",
            status=WorkflowStatus.SAFETY_REJECTED,
            stop_reason="safety_failure",
            error_message="C:\\Users\\private\\secret.sqlite3",
        )
    )
    app = create_app(original)
    app.dependency_overrides[get_analysis_service] = lambda: injected

    try:
        response = TestClient(app).post(
            "/analyze",
            json={"question": "删除订单表"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["status"] == "safety_rejected"
    assert len(response.json()["run_id"]) == 32
    assert original.call_count == 0
    assert injected.calls == [("删除订单表", response.json()["run_id"])]
    assert app.state.analysis_service is original


def test_factory_injected_fake_service_is_isolated_from_another_app():
    first = ScriptedAnalysisService(
        WorkflowState(
            run_id="first-run",
            question="问题",
            status=WorkflowStatus.RESOURCE_FAILED,
            stop_reason="resource_failure",
        )
    )
    second = ScriptedAnalysisService(
        WorkflowState(
            run_id="second-run",
            question="问题",
            status=WorkflowStatus.ENVIRONMENT_FAILED,
            stop_reason="environment_error",
        )
    )

    first_response = TestClient(create_app(first)).post(
        "/analyze",
        json={"question": "第一个请求"},
    )
    second_response = TestClient(create_app(second)).post(
        "/analyze",
        json={"question": "第二个请求"},
    )

    assert first_response.status_code == 503
    assert first_response.json()["status"] == "resource_failed"
    assert second_response.status_code == 503
    assert second_response.json()["status"] == "environment_failed"
    assert first.calls == [("第一个请求", first_response.json()["run_id"])]
    assert second.calls == [("第二个请求", second_response.json()["run_id"])]


def test_real_http_runs_complete_fake_model_state_machine_on_real_sqlite():
    before = _sha256(DATABASE)
    machine = _machine(ROOT)
    assert isinstance(machine.services.planner.client, FakeModelClient)
    assert isinstance(machine.services.sql_generator.client, FakeModelClient)
    assert isinstance(
        machine.services.repair_workflow.repairer.client,
        FakeModelClient,
    )
    client = TestClient(create_app(AgentService(machine)))

    response = client.post("/analyze", json={"question": QUESTION})

    after = _sha256(DATABASE)
    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "succeeded"
    assert payload["run_id"] == payload["lineage"]["parent_run_id"]
    assert payload["sql"]["sql_attempt"] == (
        payload["lineage"]["source_sql_attempt"]
    )
    assert payload["sql"]["parameters"] == {
        "start_date": "2018-06-01",
        "end_date_exclusive": "2018-08-01",
    }
    assert payload["answer"]
    assert payload["table"]["rows"]
    assert payload["chart"]["data"]
    assert payload["calculation_status"] == "computed"
    assert payload["metadata"]["sql_attempt_count"] == 1
    assert payload["metadata"]["repair_attempt_count"] == 0
    assert payload["metadata"]["execution_started"] is True
    assert len(machine.services.planner.client.requests) == 1
    assert len(machine.services.sql_generator.client.requests) == 1
    assert len(machine.services.repair_workflow.repairer.client.requests) == 0
    assert before == after
