"""Tests for the API service HTTP-to-workflow service boundary."""

from src.ecommerce_agent.workflow_state import WorkflowState
from src.ecommerce_agent.analysis_service import AgentService


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def run(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> WorkflowState:
        self.calls.append((question, run_id))
        return WorkflowState(
            run_id=run_id or "runner-generated-id",
            question=question,
        )


def test_service_delegates_once_and_preserves_explicit_run_id():
    runner = RecordingRunner()
    service = AgentService(runner)

    state = service.analyze("比较月度 GMV。", run_id="api-run-001")

    assert runner.calls == [("比较月度 GMV。", "api-run-001")]
    assert state.run_id == "api-run-001"
    assert state.question == "比较月度 GMV。"


def test_service_allows_runner_to_own_run_id_generation():
    runner = RecordingRunner()
    service = AgentService(runner)

    state = service.analyze("分析订单量。")

    assert runner.calls == [("分析订单量。", None)]
    assert state.run_id == "runner-generated-id"
