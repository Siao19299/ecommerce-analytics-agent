"""Service boundary between Day 12 HTTP routes and the Day 11 workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.ecommerce_agent.day11_state import Day11WorkflowState


class WorkflowRunner(Protocol):
    """Smallest Day 11 runner contract required by the API service."""

    def run(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> Day11WorkflowState: ...


class AnalysisService(Protocol):
    """Operation exposed to the HTTP layer, independent of FastAPI."""

    def analyze(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> Day11WorkflowState: ...


@dataclass(frozen=True)
class Day11AgentService:
    """Delegate one analysis request to the canonical Day 11 runner."""

    runner: WorkflowRunner

    def analyze(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> Day11WorkflowState:
        return self.runner.run(question, run_id=run_id)
