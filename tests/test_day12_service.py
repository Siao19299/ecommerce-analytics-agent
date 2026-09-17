"""Tests for the Day 12 HTTP-to-workflow service boundary."""

from src.ecommerce_agent.day11_state import Day11WorkflowState
from src.ecommerce_agent.day12_service import Day11AgentService


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def run(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> Day11WorkflowState:
        self.calls.append((question, run_id))
        return Day11WorkflowState(
            run_id=run_id or "runner-generated-id",
            question=question,
        )


def test_service_delegates_once_and_preserves_explicit_run_id():
    runner = RecordingRunner()
    service = Day11AgentService(runner)

    state = service.analyze("比较月度 GMV。", run_id="api-run-001")

    assert runner.calls == [("比较月度 GMV。", "api-run-001")]
    assert state.run_id == "api-run-001"
    assert state.question == "比较月度 GMV。"


def test_service_allows_runner_to_own_run_id_generation():
    runner = RecordingRunner()
    service = Day11AgentService(runner)

    state = service.analyze("分析订单量。")

    assert runner.calls == [("分析订单量。", None)]
    assert state.run_id == "runner-generated-id"
