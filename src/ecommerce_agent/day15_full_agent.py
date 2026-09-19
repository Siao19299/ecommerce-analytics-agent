"""Projection adapter from the existing Day 11 workflow into Day 15 records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Any, ClassVar, Protocol

from pydantic import Field, model_validator

from src.ecommerce_agent.day11_state import Day11WorkflowState, WorkflowStatus
from src.ecommerce_agent.day14_schema import StrictEvaluationModel
from src.ecommerce_agent.day15_reproducibility import PublicCase
from src.ecommerce_agent.day15_protocol import CandidateVersion


class AgentSafetyOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NOT_EVALUATED = "not_evaluated"


class FullAgentAdapterFailure(StrEnum):
    RUNNER_EXCEPTION = "runner_exception"
    NON_TERMINAL_STATE = "non_terminal_state"


class WorkflowRunner(Protocol):
    def run(self, question: str, *, run_id: str | None = None) -> Day11WorkflowState: ...


class FullAgentAdapterRecord(StrictEvaluationModel):
    case_id: str
    adapter_version: str = "full_agent_v1"
    run_id: str
    workflow_status: str | None
    stop_reason: str | None
    adapter_failure: FullAgentAdapterFailure | None
    initial_generated_sql: str | None
    final_sql: str | None
    final_named_parameters: dict[str, str | int | float | bool | None]
    safety_outcome: AgentSafetyOutcome
    execution_started: bool
    execution_succeeded: bool
    source_result_columns: tuple[str, ...]
    source_result_rows: tuple[dict[str, Any], ...]
    result_columns: tuple[str, ...]
    result_rows: tuple[dict[str, Any], ...]
    rows_truncated: bool
    calculation_status: str | None
    calculation_parent_run_id: str | None
    calculation_source_sql_attempt: int | None = Field(default=None, ge=1)
    sql_attempt_count: int = Field(ge=0)
    repair_attempt_count: int = Field(ge=0)
    generation_model_call_count: int = Field(ge=0)
    repair_model_call_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    generation_transport_attempt_count: int | None = Field(default=None, ge=0)
    repair_transport_attempt_count: int | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    model_latency_ms: float | None = Field(default=None, ge=0)
    end_to_end_latency_ms: float = Field(ge=0)
    cost: float | None = Field(default=None, ge=0)
    cost_currency: str | None = None
    model_names: tuple[str, ...]
    generation_model_response_sha256s: tuple[str, ...]
    retrieved_document_ids: tuple[str, ...]
    node_sequence: tuple[str, ...]
    final_answer: str | None
    workflow_snapshot: dict[str, Any] | None
    workflow_snapshot_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_attempt_accounting(self):
        if self.model_call_count != (
            self.generation_model_call_count + self.repair_model_call_count
        ):
            raise ValueError("model call count must equal generation plus repair calls")
        if self.adapter_failure is None and self.workflow_status is None:
            raise ValueError("successful adapter projection requires a workflow status")
        if self.execution_succeeded and not self.execution_started:
            raise ValueError("successful execution requires SQLite entry")
        return self


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _response_attempts(response) -> int:
    return response.transport_attempts or 1


def _sum_or_none(values: list[int | float | None]) -> int | float | None:
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


@dataclass
class FullAgentAdapter:
    candidate_version: ClassVar[CandidateVersion] = CandidateVersion.FULL_AGENT
    runner: WorkflowRunner

    def run_case(self, case: PublicCase, *, run_id: str) -> FullAgentAdapterRecord:
        started = perf_counter()
        try:
            state = self.runner.run(case.question, run_id=run_id)
        except Exception:
            return self._runner_failure(
                case,
                run_id,
                (perf_counter() - started) * 1000,
                FullAgentAdapterFailure.RUNNER_EXCEPTION,
            )
        elapsed = (perf_counter() - started) * 1000
        if not state.status.is_terminal:
            return self._runner_failure(
                case,
                run_id,
                elapsed,
                FullAgentAdapterFailure.NON_TERMINAL_STATE,
            )
        return self._project(case, state, elapsed)

    def _project(
        self,
        case: PublicCase,
        state: Day11WorkflowState,
        elapsed_ms: float,
    ) -> FullAgentAdapterRecord:
        trace = state.sql_attempt_trace
        attempts = trace.attempts if trace is not None else ()
        repair_events = trace.repair_model_events if trace is not None else ()
        initial_sql = state.generated_query.sql if state.generated_query is not None else None
        if attempts:
            final_sql = attempts[-1].candidate_sql
            parameters = attempts[-1].parameters
            execution_started = any(item.execution_started for item in attempts)
        else:
            final_sql = initial_sql
            parameters = (
                state.generated_query.parameters if state.generated_query is not None else {}
            )
            execution_started = (
                state.execution_result.execution_started
                if state.execution_result is not None
                else False
            )

        execution = state.execution_result
        calculation_status = None
        if state.analysis_result is not None:
            raw_status = getattr(state.analysis_result, "calculation_status", None)
            calculation_status = getattr(raw_status, "value", raw_status)

        safety = AgentSafetyOutcome.NOT_EVALUATED
        if state.sql_safety_trace is not None:
            safety = (
                AgentSafetyOutcome.ACCEPTED
                if state.sql_safety_trace.accepted
                else AgentSafetyOutcome.REJECTED
            )

        planning_responses = (
            list(state.planning_result.model_responses)
            if state.planning_result is not None
            else []
        )
        generation_responses = (
            [state.generation_result.response]
            if state.generation_result is not None
            and state.generation_result.response is not None
            else []
        )
        generation_response_objects = planning_responses + generation_responses
        repair_response_traces = list(repair_events)
        generation_calls = self._generation_call_count(state)
        repair_calls = len(repair_events)

        generation_transport = self._generation_transport_attempts(
            state, generation_response_objects
        )
        repair_transport_values = [event.http_attempts or 1 for event in repair_events]
        repair_transport = _sum_or_none(repair_transport_values)
        if not repair_events:
            repair_transport = 0

        prompt_tokens = _sum_or_none(
            [response.prompt_tokens for response in generation_response_objects]
            + [event.prompt_tokens for event in repair_response_traces]
        )
        completion_tokens = _sum_or_none(
            [response.completion_tokens for response in generation_response_objects]
            + [event.completion_tokens for event in repair_response_traces]
        )
        cost = _sum_or_none(
            [response.cost for response in generation_response_objects]
            + [event.cost for event in repair_response_traces]
        )
        cost_currencies = {
            value
            for value in (
                [response.cost_currency for response in generation_response_objects]
                + [event.cost_currency for event in repair_response_traces]
            )
            if value is not None
        }
        model_latency = _sum_or_none(
            [response.latency_ms for response in generation_response_objects]
            + [event.latency_ms for event in repair_response_traces]
        )
        model_names = tuple(
            response.model_name for response in generation_response_objects
        ) + tuple(event.model_name for event in repair_response_traces)
        response_hashes = tuple(
            _sha256_text(response.content) for response in generation_response_objects
        )

        snapshot = state.to_dict()
        serialized_snapshot = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        presentation = state.presentation
        if presentation is not None and calculation_status is not None:
            final_columns = presentation.table.columns
            final_rows = presentation.table.rows
        else:
            final_columns = execution.columns if execution is not None else ()
            final_rows = execution.rows if execution is not None else ()
        return FullAgentAdapterRecord(
            case_id=case.case_id,
            run_id=state.run_id,
            workflow_status=state.status.value,
            stop_reason=state.stop_reason,
            adapter_failure=None,
            initial_generated_sql=initial_sql,
            final_sql=final_sql,
            final_named_parameters=parameters,
            safety_outcome=safety,
            execution_started=execution_started,
            execution_succeeded=execution.is_success if execution is not None else False,
            source_result_columns=execution.columns if execution is not None else (),
            source_result_rows=execution.rows if execution is not None else (),
            result_columns=final_columns,
            result_rows=final_rows,
            rows_truncated=execution.rows_truncated if execution is not None else False,
            calculation_status=calculation_status,
            calculation_parent_run_id=(
                state.calculation_trace.parent_run_id
                if state.calculation_trace is not None
                else None
            ),
            calculation_source_sql_attempt=(
                state.calculation_trace.source_sql_attempt
                if state.calculation_trace is not None
                else None
            ),
            sql_attempt_count=len(attempts) or (1 if initial_sql is not None else 0),
            repair_attempt_count=len(repair_events),
            generation_model_call_count=generation_calls,
            repair_model_call_count=repair_calls,
            model_call_count=generation_calls + repair_calls,
            generation_transport_attempt_count=generation_transport,
            repair_transport_attempt_count=repair_transport,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model_latency_ms=model_latency,
            end_to_end_latency_ms=elapsed_ms,
            cost=float(cost) if cost is not None else None,
            cost_currency=(
                next(iter(cost_currencies)) if len(cost_currencies) == 1 else None
            ),
            model_names=model_names,
            generation_model_response_sha256s=response_hashes,
            retrieved_document_ids=tuple(
                hit.document.document_id for hit in state.retrieval_hits
            ),
            node_sequence=tuple(item.node.value for item in state.node_trace),
            final_answer=(presentation.conclusion if presentation is not None else None),
            workflow_snapshot=snapshot,
            workflow_snapshot_sha256=_sha256_text(serialized_snapshot),
        )

    @staticmethod
    def _generation_call_count(state: Day11WorkflowState) -> int:
        planning_calls = (
            len(state.planning_result.log_records)
            if state.planning_result is not None
            else 0
        )
        sql_calls = 1 if state.generation_result is not None else 0
        return planning_calls + sql_calls

    @staticmethod
    def _generation_transport_attempts(
        state: Day11WorkflowState,
        responses: list,
    ) -> int | None:
        expected_calls = FullAgentAdapter._generation_call_count(state)
        if expected_calls == 0:
            return 0
        if len(responses) != expected_calls:
            return None
        return sum(_response_attempts(response) for response in responses)

    @staticmethod
    def _runner_failure(
        case: PublicCase,
        run_id: str,
        elapsed_ms: float,
        failure: FullAgentAdapterFailure,
    ) -> FullAgentAdapterRecord:
        return FullAgentAdapterRecord(
            case_id=case.case_id,
            run_id=run_id,
            workflow_status=None,
            stop_reason=None,
            adapter_failure=failure,
            initial_generated_sql=None,
            final_sql=None,
            final_named_parameters={},
            safety_outcome=AgentSafetyOutcome.NOT_EVALUATED,
            execution_started=False,
            execution_succeeded=False,
            source_result_columns=(),
            source_result_rows=(),
            result_columns=(),
            result_rows=(),
            rows_truncated=False,
            calculation_status=None,
            calculation_parent_run_id=None,
            calculation_source_sql_attempt=None,
            sql_attempt_count=0,
            repair_attempt_count=0,
            generation_model_call_count=0,
            repair_model_call_count=0,
            model_call_count=0,
            generation_transport_attempt_count=None,
            repair_transport_attempt_count=None,
            prompt_tokens=None,
            completion_tokens=None,
            model_latency_ms=None,
            end_to_end_latency_ms=elapsed_ms,
            cost=None,
            cost_currency=None,
            model_names=(),
            generation_model_response_sha256s=(),
            retrieved_document_ids=(),
            node_sequence=(),
            final_answer=None,
            workflow_snapshot=None,
            workflow_snapshot_sha256=None,
        )
