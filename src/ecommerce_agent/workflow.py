"""Contract-enforced plain-Python state machine for the Agent workflow workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.repair_workflow import SqlRepairWorkflow
from src.ecommerce_agent.analysis_presentation import DeterministicPresentation
from src.ecommerce_agent.calculation_trace import build_calculation_trace
from src.ecommerce_agent.workflow_state import (
    WorkflowState,
    NodeOutcome,
    NodeTrace,
    WorkflowNode,
    WorkflowStatus,
)
from src.ecommerce_agent.retrieval import (
    RetrievalDocument,
    RetrievalFilter,
    Retriever,
    retrieve_payload,
)
from src.ecommerce_agent.sql_generation import (
    SqlGenerator,
    build_sql_generation_context,
)
from src.ecommerce_agent.sql_safety import validate_sql_safety


class WorkflowInvariantError(RuntimeError):
    """Raised for a broken node contract, not an expected business branch."""


@dataclass(frozen=True)
class NodeResult:
    updates: Mapping[str, Any]
    next_node: WorkflowNode | None
    outcome: NodeOutcome = NodeOutcome.SUCCEEDED
    detail: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class NodeContract:
    node: WorkflowNode
    purpose: str
    required_fields: tuple[str, ...]
    allowed_updates: frozenset[str]


DeterministicAnalyzer = Callable[[WorkflowState], BaseModel]
Presenter = Callable[[BaseModel], DeterministicPresentation]


@dataclass(frozen=True)
class WorkflowServices:
    root: Path
    database_path: Path
    documents: Sequence[RetrievalDocument]
    retriever: Retriever
    planner: AnalysisPlanner
    sql_generator: SqlGenerator
    repair_workflow: SqlRepairWorkflow
    analyzer: DeterministicAnalyzer
    presenter: Presenter
    metric_top_k: int = 5
    schema_top_k: int = 8


NODE_CONTRACTS: dict[WorkflowNode, NodeContract] = {
    WorkflowNode.RETRIEVAL: NodeContract(
        WorkflowNode.RETRIEVAL,
        "召回指标和 Schema 候选，不授予任意 JOIN 权限",
        ("question",),
        frozenset(
            {
                "retrieval_payload",
                "retrieval_hits",
                "pending_status",
                "pending_stop_reason",
                "error_category",
                "error_message",
            }
        ),
    ),
    WorkflowNode.PLANNING: NodeContract(
        WorkflowNode.PLANNING,
        "生成并验证分析计划，显式区分澄清与失败",
        ("retrieval_hits",),
        frozenset(
            {
                "planning_result",
                "analysis_plan",
                "evidence_document_ids",
                "clarification_question",
                "pending_status",
                "pending_stop_reason",
                "error_category",
                "error_message",
            }
        ),
    ),
    WorkflowNode.SQL_GENERATION: NodeContract(
        WorkflowNode.SQL_GENERATION,
        "使用规范字典上下文生成结构化 SQL，不执行 SQL",
        ("analysis_plan", "retrieval_hits"),
        frozenset(
            {
                "generation_context",
                "generation_result",
                "generated_query",
                "sql_safety_trace",
                "pending_status",
                "pending_stop_reason",
                "error_category",
                "error_message",
            }
        ),
    ),
    WorkflowNode.SQL_SAFETY: NodeContract(
        WorkflowNode.SQL_SAFETY,
        "复用 SQL security AST 与两级允许范围作独立安全门",
        ("generation_context", "generated_query"),
        frozenset(
            {
                "sql_safety_trace",
                "pending_status",
                "pending_stop_reason",
                "error_category",
                "error_message",
            }
        ),
    ),
    WorkflowNode.EXECUTION: NodeContract(
        WorkflowNode.EXECUTION,
        "调用 SQL repair 统一入口执行首次 SQL，并保留完整局部 trace",
        ("generation_context", "generated_query", "sql_safety_trace"),
        frozenset(
            {
                "repair_result",
                "execution_result",
                "sql_attempt_trace",
                "pending_status",
                "pending_stop_reason",
                "error_category",
                "error_message",
            }
        ),
    ),
    WorkflowNode.BOUNDED_REPAIR: NodeContract(
        WorkflowNode.BOUNDED_REPAIR,
        "解释 SQL repair 有限修复结果；不复制或扩大修复循环",
        ("repair_result", "execution_result", "sql_attempt_trace"),
        frozenset(
            {
                "pending_status",
                "pending_stop_reason",
                "error_category",
                "error_message",
            }
        ),
    ),
    WorkflowNode.DETERMINISTIC_ANALYSIS: NodeContract(
        WorkflowNode.DETERMINISTIC_ANALYSIS,
        "仅消费成功且合格的 SQL 结果，数值由 Deterministic analysis 工具计算",
        ("execution_result", "sql_attempt_trace"),
        frozenset(
            {
                "analysis_result",
                "calculation_trace",
                "pending_status",
                "pending_stop_reason",
                "error_category",
                "error_message",
            }
        ),
    ),
    WorkflowNode.PRESENTATION: NodeContract(
        WorkflowNode.PRESENTATION,
        "使用确定性模板展示，不重新计算数值或自动归因",
        ("analysis_result", "calculation_trace"),
        frozenset(
            {
                "presentation",
                "pending_status",
                "pending_stop_reason",
                "error_category",
                "error_message",
            }
        ),
    ),
    WorkflowNode.FINALIZATION: NodeContract(
        WorkflowNode.FINALIZATION,
        "冻结最终状态与停止原因",
        (),
        frozenset({"status", "stop_reason"}),
    ),
}


ALLOWED_EDGES: dict[WorkflowNode, frozenset[WorkflowNode | None]] = {
    WorkflowNode.RETRIEVAL: frozenset(
        {WorkflowNode.PLANNING, WorkflowNode.FINALIZATION}
    ),
    WorkflowNode.PLANNING: frozenset(
        {WorkflowNode.SQL_GENERATION, WorkflowNode.FINALIZATION}
    ),
    WorkflowNode.SQL_GENERATION: frozenset(
        {WorkflowNode.SQL_SAFETY, WorkflowNode.FINALIZATION}
    ),
    WorkflowNode.SQL_SAFETY: frozenset(
        {WorkflowNode.EXECUTION, WorkflowNode.FINALIZATION}
    ),
    WorkflowNode.EXECUTION: frozenset(
        {
            WorkflowNode.BOUNDED_REPAIR,
            WorkflowNode.DETERMINISTIC_ANALYSIS,
            WorkflowNode.FINALIZATION,
        }
    ),
    WorkflowNode.BOUNDED_REPAIR: frozenset(
        {WorkflowNode.DETERMINISTIC_ANALYSIS, WorkflowNode.FINALIZATION}
    ),
    WorkflowNode.DETERMINISTIC_ANALYSIS: frozenset(
        {WorkflowNode.PRESENTATION, WorkflowNode.FINALIZATION}
    ),
    WorkflowNode.PRESENTATION: frozenset(
        {WorkflowNode.FINALIZATION}
    ),
    WorkflowNode.FINALIZATION: frozenset({None}),
}


class AgentStateMachine:
    """Run the stable workflow with explicit contracts and hard termination."""

    def __init__(self, services: WorkflowServices, *, max_node_steps: int = 16):
        if (
            isinstance(max_node_steps, bool)
            or not isinstance(max_node_steps, int)
            or max_node_steps < 1
        ):
            raise ValueError("max_node_steps 必须是正整数")
        self.services = services
        self.max_node_steps = max_node_steps
        self._handlers = {
            WorkflowNode.RETRIEVAL: self._retrieval,
            WorkflowNode.PLANNING: self._planning,
            WorkflowNode.SQL_GENERATION: self._sql_generation,
            WorkflowNode.SQL_SAFETY: self._sql_safety,
            WorkflowNode.EXECUTION: self._execution,
            WorkflowNode.BOUNDED_REPAIR: self._bounded_repair,
            WorkflowNode.DETERMINISTIC_ANALYSIS: (
                self._deterministic_analysis
            ),
            WorkflowNode.PRESENTATION: self._presentation,
            WorkflowNode.FINALIZATION: self._finalization,
        }

    def run(
        self,
        question: str,
        *,
        run_id: str | None = None,
    ) -> WorkflowState:
        if not question.strip():
            raise ValueError("经营问题不能为空")
        state = WorkflowState(
            run_id=run_id or uuid4().hex,
            question=question,
        )
        while not state.status.is_terminal:
            self.step(state)
        return state

    def step(self, state: WorkflowState) -> WorkflowState:
        """Execute one contracted node; shared by Python and LangGraph."""
        if state.status.is_terminal:
            return state
        if len(state.node_trace) >= self.max_node_steps:
            state.status = WorkflowStatus.INTERNAL_FAILED
            state.stop_reason = "max_node_steps_reached"
            state.error_category = "loop_guard"
            state.error_message = (
                f"顶层节点执行达到硬上限 {self.max_node_steps}"
            )
            return state

        sequence = len(state.node_trace) + 1
        node = state.current_node
        contract = NODE_CONTRACTS[node]
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        try:
            self._check_preconditions(state, contract)
            result = self._handlers[node](state)
            self._validate_result(contract, result)
        except Exception as error:  # workflow boundary: controlled failure
            result = self._exception_result(node, error)
            self._validate_result(contract, result)
        finished_at = datetime.now(timezone.utc)
        event = NodeTrace(
            sequence=sequence,
            node=node,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(perf_counter() - started) * 1000,
            outcome=result.outcome,
            next_node=result.next_node,
            updated_fields=tuple(sorted(result.updates)),
            detail=dict(result.detail or {}),
        )
        for name, value in result.updates.items():
            setattr(state, name, value)
        state.node_trace = state.node_trace + (event,)
        if result.next_node is None:
            if not state.status.is_terminal:
                raise WorkflowInvariantError(
                    "只有终止状态可以结束顶层状态机"
                )
            return state
        state.current_node = result.next_node
        return state

    @staticmethod
    def _check_preconditions(
        state: WorkflowState,
        contract: NodeContract,
    ) -> None:
        missing = [
            name
            for name in contract.required_fields
            if getattr(state, name) is None
        ]
        if missing:
            raise WorkflowInvariantError(
                f"节点 {contract.node.value} 缺少前置字段："
                + ", ".join(missing)
            )

    @staticmethod
    def _validate_result(
        contract: NodeContract,
        result: NodeResult,
    ) -> None:
        illegal = set(result.updates) - contract.allowed_updates
        if illegal:
            raise WorkflowInvariantError(
                f"节点 {contract.node.value} 越权修改字段："
                + ", ".join(sorted(illegal))
            )
        if result.next_node not in ALLOWED_EDGES[contract.node]:
            target = (
                result.next_node.value
                if result.next_node is not None
                else "END"
            )
            raise WorkflowInvariantError(
                f"非法条件边：{contract.node.value} -> {target}"
            )

    def _retrieval(self, state: WorkflowState) -> NodeResult:
        metric_filter = RetrievalFilter(
            document_types=frozenset({"metric"})
        )
        schema_filter = RetrievalFilter(
            document_types=frozenset({"schema"})
        )
        metric_payload = retrieve_payload(
            self.services.retriever,
            state.question,
            top_k=self.services.metric_top_k,
            filters=metric_filter,
        )
        schema_payload = retrieve_payload(
            self.services.retriever,
            state.question,
            top_k=self.services.schema_top_k,
            filters=schema_filter,
        )
        hits = tuple(
            self.services.retriever.search(
                state.question,
                top_k=self.services.metric_top_k,
                filters=metric_filter,
            )
        ) + tuple(
            self.services.retriever.search(
                state.question,
                top_k=self.services.schema_top_k,
                filters=schema_filter,
            )
        )
        return NodeResult(
            updates={
                "retrieval_payload": {
                    "metric": metric_payload,
                    "schema": schema_payload,
                },
                "retrieval_hits": hits,
            },
            next_node=WorkflowNode.PLANNING,
            detail={"hit_count": len(hits)},
        )

    def _planning(self, state: WorkflowState) -> NodeResult:
        planning = self.services.planner.create_plan(
            state.question,
            state.retrieval_hits,
        )
        updates: dict[str, Any] = {"planning_result": planning}
        validation = planning.validation
        if not planning.is_success:
            message = (
                validation.error_message
                if validation is not None
                else planning.client_error_message
            ) or "分析计划生成失败"
            error_type = (
                validation.error_type.value
                if validation is not None
                and validation.error_type is not None
                else (
                    planning.client_error_type.value
                    if planning.client_error_type is not None
                    else "planning_failed"
                )
            )
            updates.update(
                self._terminal_updates(
                    WorkflowStatus.PLANNING_FAILED,
                    error_type,
                    error_type,
                    message,
                )
            )
            return NodeResult(
                updates,
                WorkflowNode.FINALIZATION,
                NodeOutcome.BRANCHED,
            )
        assert validation is not None
        if validation.needs_clarification:
            updates.update(
                {
                    "clarification_question": (
                        validation.clarification_question
                    ),
                    "pending_status": WorkflowStatus.NEEDS_CLARIFICATION,
                    "pending_stop_reason": "clarification_required",
                }
            )
            return NodeResult(
                updates,
                WorkflowNode.FINALIZATION,
                NodeOutcome.BRANCHED,
            )
        assert validation.plan is not None
        updates.update(
            {
                "analysis_plan": validation.plan,
                "evidence_document_ids": validation.evidence_document_ids,
            }
        )
        return NodeResult(updates, WorkflowNode.SQL_GENERATION)

    def _sql_generation(self, state: WorkflowState) -> NodeResult:
        assert state.analysis_plan is not None
        context = build_sql_generation_context(
            self.services.root,
            state.question,
            state.analysis_plan,
            state.retrieval_hits,
            self.services.documents,
        )
        generation = self.services.sql_generator.generate(context)
        updates: dict[str, Any] = {
            "generation_context": context,
            "generation_result": generation,
            "generated_query": generation.query,
            "sql_safety_trace": generation.safety_trace,
        }
        if generation.query is not None:
            # Unsafe candidates are routed to the explicit safety node so the
            # rejection remains distinct from model/structure failures.
            return NodeResult(updates, WorkflowNode.SQL_SAFETY)
        updates.update(
            self._terminal_updates(
                WorkflowStatus.SQL_GENERATION_FAILED,
                generation.error_type.value
                if generation.error_type is not None
                else "sql_generation_failed",
                "sql_generation",
                generation.error_message or "SQL 生成失败",
            )
        )
        return NodeResult(
            updates,
            WorkflowNode.FINALIZATION,
            NodeOutcome.BRANCHED,
        )

    def _sql_safety(self, state: WorkflowState) -> NodeResult:
        assert state.generation_context is not None
        assert state.generated_query is not None
        safety = validate_sql_safety(
            state.generated_query.sql,
            state.generation_context.safety_policy,
        )
        updates: dict[str, Any] = {"sql_safety_trace": safety.trace}
        generation_error = (
            state.generation_result.error_type
            if state.generation_result is not None
            else None
        )
        if not safety.is_safe:
            updates.update(
                self._terminal_updates(
                    WorkflowStatus.SAFETY_REJECTED,
                    safety.trace.error_code.value
                    if safety.trace.error_code is not None
                    else "sql_safety_rejected",
                    "sql_safety",
                    safety.trace.error_message or "SQL 安全拒绝",
                )
            )
            return NodeResult(
                updates,
                WorkflowNode.FINALIZATION,
                NodeOutcome.BRANCHED,
            )
        if generation_error is not None:
            updates.update(
                self._terminal_updates(
                    WorkflowStatus.SQL_GENERATION_FAILED,
                    generation_error.value,
                    "sql_generation",
                    state.generation_result.error_message
                    or "SQL 生成合同失败",
                )
            )
            return NodeResult(
                updates,
                WorkflowNode.FINALIZATION,
                NodeOutcome.BRANCHED,
            )
        return NodeResult(updates, WorkflowNode.EXECUTION)

    def _execution(self, state: WorkflowState) -> NodeResult:
        assert state.generated_query is not None
        assert state.generation_context is not None
        result = self.services.repair_workflow.run(
            state.generated_query,
            state.generation_context,
            run_id=state.run_id,
        )
        updates: dict[str, Any] = {
            "repair_result": result,
            "execution_result": result.execution,
            "sql_attempt_trace": result.trace,
        }
        if len(result.trace.attempts) > 1:
            return NodeResult(
                updates,
                WorkflowNode.BOUNDED_REPAIR,
                detail={"sql_attempts": len(result.trace.attempts)},
            )
        if result.is_success:
            return NodeResult(
                updates,
                WorkflowNode.DETERMINISTIC_ANALYSIS,
                detail={"sql_attempts": 1},
            )
        updates.update(self._repair_terminal_updates(result.trace.stop_reason))
        return NodeResult(
            updates,
            WorkflowNode.FINALIZATION,
            NodeOutcome.BRANCHED,
        )

    def _bounded_repair(self, state: WorkflowState) -> NodeResult:
        assert state.repair_result is not None
        if state.repair_result.is_success:
            return NodeResult(
                {},
                WorkflowNode.DETERMINISTIC_ANALYSIS,
                detail={
                    "stop_reason": state.repair_result.trace.stop_reason,
                    "repair_succeeded": True,
                },
            )
        updates = self._repair_terminal_updates(
            state.repair_result.trace.stop_reason
        )
        return NodeResult(
            updates,
            WorkflowNode.FINALIZATION,
            NodeOutcome.BRANCHED,
            {"repair_succeeded": False},
        )

    def _deterministic_analysis(
        self,
        state: WorkflowState,
    ) -> NodeResult:
        assert state.execution_result is not None
        assert state.execution_result.is_success
        assert state.sql_attempt_trace is not None
        result = self.services.analyzer(state)
        trace = build_calculation_trace(
            calculation_id=f"{state.run_id}-calculation",
            step="deterministic_analysis",
            result=result,
        )
        if trace.parent_run_id != state.run_id:
            raise WorkflowInvariantError(
                "calculation trace 的 parent_run_id 必须等于顶层 run_id"
            )
        successful_attempt = state.sql_attempt_trace.attempts[-1].sql_attempt
        if trace.source_sql_attempt != successful_attempt:
            raise WorkflowInvariantError(
                "calculation trace 必须引用最终成功 SQL attempt"
            )
        return NodeResult(
            {
                "analysis_result": result,
                "calculation_trace": trace,
            },
            WorkflowNode.PRESENTATION,
            detail={"analysis_type": trace.analysis_type},
        )

    def _presentation(self, state: WorkflowState) -> NodeResult:
        assert state.analysis_result is not None
        presentation = self.services.presenter(state.analysis_result)
        if presentation.lineage.parent_run_id != state.run_id:
            raise WorkflowInvariantError(
                "展示结果必须保留顶层 run_id lineage"
            )
        return NodeResult(
            {"presentation": presentation},
            WorkflowNode.FINALIZATION,
        )

    @staticmethod
    def _finalization(state: WorkflowState) -> NodeResult:
        if state.pending_status is not None:
            status = state.pending_status
            reason = state.pending_stop_reason or "controlled_failure"
            outcome = NodeOutcome.BRANCHED
        else:
            if state.presentation is None:
                raise WorkflowInvariantError(
                    "成功结束前必须存在确定性展示结果"
                )
            status = WorkflowStatus.SUCCEEDED
            reason = "completed"
            outcome = NodeOutcome.SUCCEEDED
        return NodeResult(
            {"status": status, "stop_reason": reason},
            None,
            outcome,
        )

    @staticmethod
    def _terminal_updates(
        status: WorkflowStatus,
        stop_reason: str,
        error_category: str,
        error_message: str,
    ) -> dict[str, Any]:
        return {
            "pending_status": status,
            "pending_stop_reason": stop_reason,
            "error_category": error_category,
            "error_message": error_message,
        }

    def _repair_terminal_updates(self, stop_reason: str) -> dict[str, Any]:
        if stop_reason in {
            "safety_failure",
            "repair_candidate_safety_rejected",
        }:
            status = WorkflowStatus.SAFETY_REJECTED
        elif stop_reason == "resource_failure":
            status = WorkflowStatus.RESOURCE_FAILED
        elif stop_reason == "environment_error":
            status = WorkflowStatus.ENVIRONMENT_FAILED
        elif stop_reason == "repair_limit_reached":
            status = WorkflowStatus.REPAIR_LIMIT_REACHED
        elif stop_reason in {
            "duplicate_candidate",
            "repair_parameter_mismatch",
            "transient_model_client_error",
            "permanent_model_client_error",
            "non_json",
            "invalid_structure",
        }:
            status = WorkflowStatus.REPAIR_FAILED
        else:
            status = WorkflowStatus.EXECUTION_FAILED
        return self._terminal_updates(
            status,
            stop_reason,
            "query_execution_or_repair",
            f"SQL repair 执行/修复流程停止：{stop_reason}",
        )

    def _exception_result(
        self,
        node: WorkflowNode,
        error: Exception,
    ) -> NodeResult:
        if node is WorkflowNode.FINALIZATION:
            return NodeResult(
                {
                    "status": WorkflowStatus.INTERNAL_FAILED,
                    "stop_reason": "finalization_invariant_failed",
                },
                None,
                NodeOutcome.FAILED,
                {"exception_type": type(error).__name__},
            )
        status_by_node = {
            WorkflowNode.RETRIEVAL: WorkflowStatus.RETRIEVAL_FAILED,
            WorkflowNode.PLANNING: WorkflowStatus.PLANNING_FAILED,
            WorkflowNode.SQL_GENERATION: (
                WorkflowStatus.SQL_GENERATION_FAILED
            ),
            WorkflowNode.SQL_SAFETY: WorkflowStatus.SAFETY_REJECTED,
            WorkflowNode.EXECUTION: WorkflowStatus.EXECUTION_FAILED,
            WorkflowNode.BOUNDED_REPAIR: WorkflowStatus.REPAIR_FAILED,
            WorkflowNode.DETERMINISTIC_ANALYSIS: (
                WorkflowStatus.CALCULATION_FAILED
            ),
            WorkflowNode.PRESENTATION: (
                WorkflowStatus.PRESENTATION_FAILED
            ),
        }
        status = status_by_node.get(node, WorkflowStatus.INTERNAL_FAILED)
        updates = self._terminal_updates(
            status,
            f"{node.value}_exception",
            node.value,
            str(error) or type(error).__name__,
        )
        return NodeResult(
            updates,
            WorkflowNode.FINALIZATION,
            NodeOutcome.FAILED,
            {"exception_type": type(error).__name__},
        )
