"""Service boundary between API service HTTP routes and the Agent workflow workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.ecommerce_agent.workflow_state import WorkflowState


class WorkflowRunner(Protocol):
    """Smallest Agent workflow runner contract required by the API service."""

    def run(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> WorkflowState: ...


class AnalysisService(Protocol):
    """Operation exposed to the HTTP layer, independent of FastAPI."""

    def analyze(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> WorkflowState: ...


@dataclass(frozen=True)
class AgentService:
    """Delegate one analysis request to the canonical Agent workflow runner."""

    runner: WorkflowRunner

    def analyze(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> WorkflowState:
        return self.runner.run(question, run_id=run_id)
