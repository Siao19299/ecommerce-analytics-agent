"""Thin LangGraph mapping over the tested Agent workflow Python node contracts."""

from __future__ import annotations

from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from src.ecommerce_agent.workflow_state import (
    WorkflowState,
    WorkflowNode,
)
from src.ecommerce_agent.workflow import AgentStateMachine


class LangGraphEnvelope(TypedDict):
    """Keep the canonical Python state as the only graph state value."""

    workflow_state: WorkflowState


class LangGraphRunner:
    """Compile and invoke a graph without duplicating any business node."""

    def __init__(self, machine: AgentStateMachine):
        self.machine = machine
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(LangGraphEnvelope)
        for node in WorkflowNode:
            builder.add_node(node.value, self._node_adapter(node))
        builder.add_edge(START, WorkflowNode.RETRIEVAL.value)
        destinations = {
            node.value: node.value for node in WorkflowNode
        }
        destinations["__end__"] = END
        for node in WorkflowNode:
            builder.add_conditional_edges(
                node.value,
                self._route,
                destinations,
            )
        return builder.compile()

    def _node_adapter(self, expected: WorkflowNode):
        def execute(envelope: LangGraphEnvelope) -> LangGraphEnvelope:
            state = envelope["workflow_state"]
            if state.current_node is not expected:
                raise RuntimeError(
                    "LangGraph 节点与普通 Python 状态不一致："
                    f"expected={expected.value}, "
                    f"actual={state.current_node.value}"
                )
            self.machine.step(state)
            return {"workflow_state": state}

        execute.__name__ = f"workflow_{expected.value}"
        return execute

    @staticmethod
    def _route(envelope: LangGraphEnvelope) -> str:
        state = envelope["workflow_state"]
        if state.status.is_terminal:
            return "__end__"
        return state.current_node.value

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
        result = self.graph.invoke(
            {"workflow_state": state},
            {"recursion_limit": self.machine.max_node_steps + 2},
        )
        return result["workflow_state"]
