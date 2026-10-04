"""HTTP mapping boundary kept separate from FastAPI route orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.ecommerce_agent.workflow_state import WorkflowState
from src.ecommerce_agent.api_models import AnalyzeResponse


@dataclass(frozen=True)
class MappedHttpResponse:
    status_code: int
    body: AnalyzeResponse

    def __post_init__(self) -> None:
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("status_code 必须是有效 HTTP 状态码")


class WorkflowHttpMapper(Protocol):
    """Convert one completed workflow state into a public HTTP contract."""

    def map(self, state: WorkflowState) -> MappedHttpResponse: ...
