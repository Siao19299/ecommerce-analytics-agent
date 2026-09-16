"""Typed top-level state and trace contracts for the Day 11 workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel

from src.ecommerce_agent.analysis_plan import AnalysisPlan
from src.ecommerce_agent.analysis_planner import AnalysisPlanningResult
from src.ecommerce_agent.day09_pipeline import Day09WorkflowResult
from src.ecommerce_agent.day09_trace import RepairRunTrace
from src.ecommerce_agent.day10_presentation import DeterministicPresentation
from src.ecommerce_agent.day10_trace import CalculationTrace
from src.ecommerce_agent.retrieval import RetrievalHit
from src.ecommerce_agent.sql_generation import (
    GeneratedQuery,
    QueryExecutionResult,
    SqlGenerationContext,
    SqlGenerationResult,
)
from src.ecommerce_agent.sql_safety import SqlSafetyTrace


class WorkflowNode(str, Enum):
    RETRIEVAL = "retrieval"
    PLANNING = "planning"
    SQL_GENERATION = "sql_generation"
    SQL_SAFETY = "sql_safety"
    EXECUTION = "execution"
    BOUNDED_REPAIR = "bounded_repair"
    DETERMINISTIC_ANALYSIS = "deterministic_analysis"
    PRESENTATION = "presentation"
    FINALIZATION = "finalization"


class WorkflowStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NEEDS_CLARIFICATION = "needs_clarification"
    SAFETY_REJECTED = "safety_rejected"
    RETRIEVAL_FAILED = "retrieval_failed"
    PLANNING_FAILED = "planning_failed"
    SQL_GENERATION_FAILED = "sql_generation_failed"
    RESOURCE_FAILED = "resource_failed"
    ENVIRONMENT_FAILED = "environment_failed"
    EXECUTION_FAILED = "execution_failed"
    REPAIR_FAILED = "repair_failed"
    REPAIR_LIMIT_REACHED = "repair_limit_reached"
    CALCULATION_FAILED = "calculation_failed"
    PRESENTATION_FAILED = "presentation_failed"
    INTERNAL_FAILED = "internal_failed"

    @property
    def is_terminal(self) -> bool:
        return self is not WorkflowStatus.RUNNING


class NodeOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    BRANCHED = "branched"
    FAILED = "failed"


@dataclass(frozen=True)
class NodeTrace:
    sequence: int
    node: WorkflowNode
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    outcome: NodeOutcome
    next_node: WorkflowNode | None
    updated_fields: tuple[str, ...]
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["node"] = self.node.value
        payload["started_at"] = self.started_at.isoformat()
        payload["finished_at"] = self.finished_at.isoformat()
        payload["outcome"] = self.outcome.value
        payload["next_node"] = (
            self.next_node.value if self.next_node is not None else None
        )
        return payload


@dataclass
class Day11WorkflowState:
    """One request state; nodes may update only fields in their contract."""

    run_id: str
    question: str
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    current_node: WorkflowNode = WorkflowNode.RETRIEVAL
    status: WorkflowStatus = WorkflowStatus.RUNNING
    stop_reason: str | None = None
    pending_status: WorkflowStatus | None = None
    pending_stop_reason: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    clarification_question: str | None = None
    retrieval_payload: dict[str, Any] | None = None
    retrieval_hits: tuple[RetrievalHit, ...] = ()
    planning_result: AnalysisPlanningResult | None = None
    analysis_plan: AnalysisPlan | None = None
    evidence_document_ids: tuple[str, ...] = ()
    generation_context: SqlGenerationContext | None = None
    generation_result: SqlGenerationResult | None = None
    generated_query: GeneratedQuery | None = None
    sql_safety_trace: SqlSafetyTrace | None = None
    repair_result: Day09WorkflowResult | None = None
    execution_result: QueryExecutionResult | None = None
    sql_attempt_trace: RepairRunTrace | None = None
    analysis_result: BaseModel | None = None
    calculation_trace: CalculationTrace | None = None
    presentation: DeterministicPresentation | None = None
    node_trace: tuple[NodeTrace, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the final state without inventing unavailable metadata."""
        return {
            "run_id": self.run_id,
            "question": self.question,
            "created_at": self.created_at.isoformat(),
            "current_node": self.current_node.value,
            "status": self.status.value,
            "stop_reason": self.stop_reason,
            "error": (
                {
                    "category": self.error_category,
                    "message": self.error_message,
                }
                if self.error_category is not None
                else None
            ),
            "clarification_question": self.clarification_question,
            "retrieval": self.retrieval_payload,
            "retrieved_document_ids": [
                hit.document.document_id for hit in self.retrieval_hits
            ],
            "analysis_plan": (
                self.analysis_plan.model_dump(mode="json")
                if self.analysis_plan is not None
                else None
            ),
            "evidence_document_ids": list(self.evidence_document_ids),
            "sql_generation_context": (
                self.generation_context.to_prompt_payload()
                if self.generation_context is not None
                else None
            ),
            "sql": (
                self.generated_query.model_dump(mode="json")
                if self.generated_query is not None
                else None
            ),
            "sql_safety": (
                self.sql_safety_trace.to_dict()
                if self.sql_safety_trace is not None
                else None
            ),
            "sql_attempt_trace": (
                self.sql_attempt_trace.to_dict()
                if self.sql_attempt_trace is not None
                else None
            ),
            "calculation_trace": (
                self.calculation_trace.model_dump(mode="json")
                if self.calculation_trace is not None
                else None
            ),
            "presentation": (
                self.presentation.model_dump(mode="json")
                if self.presentation is not None
                else None
            ),
            "node_trace": [item.to_dict() for item in self.node_trace],
        }
