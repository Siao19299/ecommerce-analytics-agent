"""HTTP tests for the shallow Day 12 health boundary."""

import pytest
from fastapi.testclient import TestClient

from src.ecommerce_agent.day12_api import create_app


class MustNotRunAnalysisService:
    def __init__(self) -> None:
        self.call_count = 0

    def analyze(self, question, *, run_id=None):
        self.call_count += 1
        raise AssertionError("/health 不得调用完整 Agent")


class MustNotMapWorkflow:
    def __init__(self) -> None:
        self.call_count = 0

    def map(self, state):
        self.call_count += 1
        raise AssertionError("/health 不得映射工作流状态")


def test_health_is_http_200_and_does_not_call_agent_dependencies():
    service = MustNotRunAnalysisService()
    mapper = MustNotMapWorkflow()
    client = TestClient(create_app(service, mapper))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ecommerce-analytics-agent",
        "api_version": "day12",
        "checks": {
            "api_process": "alive",
            "analysis_service": "configured",
            "external_dependencies": "not_checked",
        },
    }
    assert service.call_count == 0
    assert mapper.call_count == 0


def test_application_factory_creates_isolated_apps_and_documents_health():
    first_service = MustNotRunAnalysisService()
    second_service = MustNotRunAnalysisService()
    first_mapper = MustNotMapWorkflow()
    second_mapper = MustNotMapWorkflow()
    first = create_app(first_service, first_mapper)
    second = create_app(second_service, second_mapper)

    assert first is not second
    assert first.state.analysis_service is first_service
    assert second.state.analysis_service is second_service
    assert first.state.workflow_http_mapper is first_mapper
    assert second.state.workflow_http_mapper is second_mapper
    schema = TestClient(first).get("/openapi.json").json()
    assert schema["paths"]["/health"]["get"]["responses"]["200"]
    assert first_service.call_count == 0


def test_application_factory_rejects_unconfigured_service():
    with pytest.raises(TypeError, match="analyze"):
        create_app(object(), MustNotMapWorkflow())

    with pytest.raises(TypeError, match="map"):
        create_app(MustNotRunAnalysisService(), object())
