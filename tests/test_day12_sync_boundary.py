"""Tests that make the Day 12 sync/async execution choice explicit."""

import inspect
import threading

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.ecommerce_agent.day11_state import Day11WorkflowState, WorkflowStatus
from src.ecommerce_agent.day12_api import (
    create_app,
    get_analysis_service,
    get_workflow_http_mapper,
)
from src.ecommerce_agent.day12_mapping import Day12WorkflowHttpMapper


class ThreadRecordingService:
    def __init__(self) -> None:
        self.thread_id: int | None = None

    def analyze(self, question: str, *, run_id: str | None = None):
        self.thread_id = threading.get_ident()
        return Day11WorkflowState(
            run_id=run_id or "sync-boundary-run",
            question=question,
            status=WorkflowStatus.NEEDS_CLARIFICATION,
            stop_reason="clarification_required",
            clarification_question="请确认销售额口径。",
        )


class ThreadRecordingMapper:
    def __init__(self) -> None:
        self.thread_id: int | None = None
        self.delegate = Day12WorkflowHttpMapper()

    def map(self, state):
        self.thread_id = threading.get_ident()
        return self.delegate.map(state)


def _route(app, path: str) -> APIRoute:
    return next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path
    )


def test_nonblocking_health_and_dependencies_are_async_but_analyze_is_sync():
    app = create_app(ThreadRecordingService(), ThreadRecordingMapper())

    assert inspect.iscoroutinefunction(_route(app, "/health").endpoint)
    assert not inspect.iscoroutinefunction(_route(app, "/analyze").endpoint)
    assert inspect.iscoroutinefunction(get_analysis_service)
    assert inspect.iscoroutinefunction(get_workflow_http_mapper)


def test_sync_analyze_service_and_mapper_run_off_the_test_event_loop_thread():
    service = ThreadRecordingService()
    mapper = ThreadRecordingMapper()
    client = TestClient(create_app(service, mapper))
    caller_thread = threading.get_ident()

    response = client.post("/analyze", json={"question": "分析销售额。"})

    assert response.status_code == 200
    assert service.thread_id is not None
    assert service.thread_id != caller_thread
    assert mapper.thread_id == service.thread_id
