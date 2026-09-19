"""Bounded offline-first Day 9 SQL repair workflow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any

from src.ecommerce_agent.day09_attempt_budget import (
    AttemptCounter,
    RepairLimits,
)
from src.ecommerce_agent.day09_error_classification import (
    RepairEligibilityCategory,
    RepairEligibilityDecision,
    classify_repair_eligibility,
)
from src.ecommerce_agent.day09_repair import (
    SqlRepairContext,
    SqlRepairErrorType,
    SqlRepairer,
)
from src.ecommerce_agent.day09_sql_identity import (
    SqlCandidateRegistry,
    SqlIdentity,
    SqlNormalizationError,
    identify_sql,
)
from src.ecommerce_agent.day09_trace import (
    ModelCallTrace,
    RepairModelEvent,
    RepairRunTrace,
    RunFinalStatus,
    RunTraceRecorder,
    SqlAttemptTrace,
    TraceLogLevel,
)
from src.ecommerce_agent.sql_generation import (
    GeneratedQuery,
    QueryExecutionResult,
    SqlGenerationContext,
    execute_read_only_query,
)


class BusinessValidationStatus(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    CORRECT = "correct"
    INCORRECT = "incorrect"


@dataclass(frozen=True)
class Day09WorkflowResult:
    trace: RepairRunTrace
    execution: QueryExecutionResult
    repair_succeeded: bool
    business_validation_status: BusinessValidationStatus

    @property
    def is_success(self) -> bool:
        return self.execution.is_success


@dataclass
class Day09RepairWorkflow:
    database_path: Path
    repairer: SqlRepairer
    limits: RepairLimits = RepairLimits()
    response_source: str = "unspecified_model_response"

    def run(
        self,
        initial_query: GeneratedQuery,
        generation_context: SqlGenerationContext,
        *,
        run_id: str | None = None,
    ) -> Day09WorkflowResult:
        recorder = RunTraceRecorder(
            response_source=self.response_source,
            **({"run_id": run_id} if run_id is not None else {}),
        )
        counter = AttemptCounter(self.limits)
        registry = SqlCandidateRegistry()
        attempted_hashes: list[str] = []

        current_query = initial_query
        current_identity = identify_sql(current_query.sql)
        registration = registry.register(current_query.sql, sql_attempt=1)
        attempted_hashes.append(registration.identity.sha256)
        execution, latency_ms = self._execute(
            current_query,
            generation_context,
        )
        counter.record_initial_sql(
            entered_sqlite=execution.execution_started
        )
        decision = classify_repair_eligibility(execution)
        recorder.add_attempt(
            self._attempt_trace(
                sql_attempt=1,
                repair_attempt=None,
                source_sql_attempt=None,
                trigger="initial_generation",
                query=current_query,
                identity=current_identity,
                execution=execution,
                execution_latency_ms=latency_ms,
                decision=decision,
                model=None,
            )
        )

        if execution.is_success:
            return self._finish(
                recorder,
                execution,
                repair_succeeded=False,
                final_status=RunFinalStatus.SUCCEEDED,
                stop_reason="first_attempt_succeeded",
            )
        if not decision.eligible:
            return self._finish(
                recorder,
                execution,
                repair_succeeded=False,
                final_status=RunFinalStatus.FAILED,
                stop_reason=decision.category.value,
            )

        source_sql_attempt = 1
        while counter.snapshot().repair_attempts < self.limits.max_repair_attempts:
            repair_attempt = counter.begin_repair()
            repair_context = SqlRepairContext(
                source_sql_attempt=source_sql_attempt,
                original_sql=current_query.sql,
                original_identity=current_identity,
                database_error_rule=decision.rule_id,
                sanitized_database_error=execution.error_message or "",
                attempted_sql_hashes=tuple(attempted_hashes),
                generation_context=generation_context,
            )
            repair = self.repairer.repair(repair_context)
            transport_attempts = (
                repair.transport_attempts
            )
            if transport_attempts is not None:
                for _ in range(transport_attempts):
                    counter.record_model_http_attempt()
            recorder.add_repair_model_event(
                self._repair_model_event(
                    repair_attempt,
                    repair,
                )
            )

            if repair.query is None:
                counter.end_repair_without_candidate()
                return self._finish(
                    recorder,
                    execution,
                    repair_succeeded=False,
                    final_status=RunFinalStatus.FAILED,
                    stop_reason=(
                        repair.error_type.value
                        if repair.error_type is not None
                        else "repair_model_error"
                    ),
                )

            sql_attempt = counter.record_repair_candidate(
                entered_sqlite=False
            )
            identity = self._optional_identity(repair.query.sql)
            model_trace = self._model_trace(repair.response)

            if repair.error_type is not None:
                # The repair candidate itself is still a SQL attempt, but a
                # safety or parameter-contract rejection never enters SQLite.
                if identity is not None:
                    registration = registry.register(
                        repair.query.sql,
                        sql_attempt=sql_attempt,
                    )
                    attempted_hashes.append(registration.identity.sha256)
                recorder.add_attempt(
                    SqlAttemptTrace(
                        sql_attempt=sql_attempt,
                        repair_attempt=repair_attempt,
                        source_sql_attempt=source_sql_attempt,
                        trigger=decision.rule_id,
                        candidate_sql=repair.query.sql,
                        normalized_sql=(
                            identity.normalized_sql if identity else None
                        ),
                        sql_hash=identity.sha256 if identity else None,
                        parameters=repair.query.parameters,
                        repair_context_source=repair_context.context_source,
                        safety_trace=(
                            repair.safety_trace.to_dict()
                            if repair.safety_trace is not None
                            else None
                        ),
                        execution_started=False,
                        execution_latency_ms=None,
                        execution_status=repair.error_type.value,
                        database_error_category=None,
                        database_error_message=repair.error_message,
                        model=model_trace,
                        result_summary=None,
                        log_level=TraceLogLevel.WARNING,
                    )
                )
                stop_reason = {
                    SqlRepairErrorType.UNSAFE_CANDIDATE: (
                        "repair_candidate_safety_rejected"
                    ),
                    SqlRepairErrorType.PARAMETER_MISMATCH: (
                        "repair_parameter_mismatch"
                    ),
                }.get(repair.error_type, repair.error_type.value)
                return self._finish(
                    recorder,
                    execution,
                    repair_succeeded=False,
                    final_status=RunFinalStatus.FAILED,
                    stop_reason=stop_reason,
                )

            assert identity is not None
            registration = registry.register(
                repair.query.sql,
                sql_attempt=sql_attempt,
            )
            attempted_hashes.append(registration.identity.sha256)
            if registration.is_duplicate:
                recorder.add_attempt(
                    SqlAttemptTrace(
                        sql_attempt=sql_attempt,
                        repair_attempt=repair_attempt,
                        source_sql_attempt=source_sql_attempt,
                        trigger=decision.rule_id,
                        candidate_sql=repair.query.sql,
                        normalized_sql=identity.normalized_sql,
                        sql_hash=identity.sha256,
                        parameters=repair.query.parameters,
                        repair_context_source=repair_context.context_source,
                        safety_trace=repair.safety_trace.to_dict(),
                        execution_started=False,
                        execution_latency_ms=None,
                        execution_status="duplicate_candidate",
                        database_error_category=None,
                        database_error_message=None,
                        model=model_trace,
                        result_summary={
                            "first_seen_sql_attempt": (
                                registration.first_seen_sql_attempt
                            )
                        },
                        log_level=TraceLogLevel.WARNING,
                    )
                )
                return self._finish(
                    recorder,
                    execution,
                    repair_succeeded=False,
                    final_status=RunFinalStatus.FAILED,
                    stop_reason="duplicate_candidate",
                )

            execution, latency_ms = self._execute(
                repair.query,
                generation_context,
            )
            # The candidate was provisionally counted before safety handling;
            # update only the SQLite-entry total when execution really started.
            if execution.execution_started:
                counter.record_sqlite_entry()
            decision = classify_repair_eligibility(execution)
            recorder.add_attempt(
                self._attempt_trace(
                    sql_attempt=sql_attempt,
                    repair_attempt=repair_attempt,
                    source_sql_attempt=source_sql_attempt,
                    trigger=repair_context.database_error_rule,
                    query=repair.query,
                    identity=identity,
                    execution=execution,
                    execution_latency_ms=latency_ms,
                    decision=decision,
                    model=model_trace,
                    repair_context_source=repair_context.context_source,
                )
            )
            if execution.is_success:
                return self._finish(
                    recorder,
                    execution,
                    repair_succeeded=True,
                    final_status=RunFinalStatus.SUCCEEDED,
                    stop_reason="repair_succeeded",
                )
            if not decision.eligible:
                return self._finish(
                    recorder,
                    execution,
                    repair_succeeded=False,
                    final_status=RunFinalStatus.FAILED,
                    stop_reason=decision.category.value,
                )
            current_query = repair.query
            current_identity = identity
            source_sql_attempt = sql_attempt

        return self._finish(
            recorder,
            execution,
            repair_succeeded=False,
            final_status=RunFinalStatus.FAILED,
            stop_reason="repair_limit_reached",
        )

    def _execute(
        self,
        query: GeneratedQuery,
        context: SqlGenerationContext,
    ) -> tuple[QueryExecutionResult, float]:
        started = perf_counter()
        result = execute_read_only_query(
            self.database_path,
            query.sql,
            query.parameters,
            safety_policy=context.safety_policy,
        )
        return result, (perf_counter() - started) * 1000

    @staticmethod
    def _optional_identity(sql: str) -> SqlIdentity | None:
        try:
            return identify_sql(sql)
        except SqlNormalizationError:
            return None

    def _model_trace(self, response) -> ModelCallTrace | None:
        if response is None:
            return None
        return ModelCallTrace(
            response_source=self.response_source,
            model_name=response.model_name,
            http_attempts=response.transport_attempts,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=response.latency_ms,
            finish_reason=response.finish_reason,
            cost=response.cost,
            cost_currency=response.cost_currency,
        )

    def _repair_model_event(
        self,
        repair_attempt: int,
        repair,
    ) -> RepairModelEvent:
        response = repair.response
        return RepairModelEvent(
            repair_attempt=repair_attempt,
            response_source=self.response_source,
            model_name=(
                response.model_name
                if response is not None
                else self.repairer.config.model_name
            ),
            status="response_received" if response is not None else "failed",
            error_type=(
                repair.error_type.value
                if repair.error_type is not None
                else None
            ),
            http_attempts=repair.transport_attempts,
            prompt_tokens=(
                response.prompt_tokens if response is not None else None
            ),
            completion_tokens=(
                response.completion_tokens if response is not None else None
            ),
            latency_ms=response.latency_ms if response is not None else None,
                finish_reason=(
                    response.finish_reason if response is not None else None
                ),
                cost=response.cost if response is not None else None,
                cost_currency=(
                    response.cost_currency if response is not None else None
                ),
        )

    @staticmethod
    def _attempt_trace(
        *,
        sql_attempt: int,
        repair_attempt: int | None,
        source_sql_attempt: int | None,
        trigger: str,
        query: GeneratedQuery,
        identity: SqlIdentity,
        execution: QueryExecutionResult,
        execution_latency_ms: float,
        decision: RepairEligibilityDecision,
        model: ModelCallTrace | None,
        repair_context_source: str | None = None,
    ) -> SqlAttemptTrace:
        if execution.is_success:
            level = TraceLogLevel.INFO
            status = "succeeded"
            result_summary: dict[str, Any] | None = {
                "columns": list(execution.columns),
                "rows": list(execution.rows),
                "row_count": len(execution.rows),
                "rows_truncated": execution.rows_truncated,
            }
        else:
            level = (
                TraceLogLevel.ERROR
                if decision.category
                in {
                    RepairEligibilityCategory.ENVIRONMENT_ERROR,
                    RepairEligibilityCategory.UNCLASSIFIED_DATABASE_ERROR,
                }
                else TraceLogLevel.WARNING
            )
            status = "failed"
            result_summary = None
        return SqlAttemptTrace(
            sql_attempt=sql_attempt,
            repair_attempt=repair_attempt,
            source_sql_attempt=source_sql_attempt,
            trigger=trigger,
            candidate_sql=query.sql,
            normalized_sql=identity.normalized_sql,
            sql_hash=identity.sha256,
            parameters=query.parameters,
            repair_context_source=repair_context_source,
            safety_trace=(
                execution.safety_trace.to_dict()
                if execution.safety_trace is not None
                else None
            ),
            execution_started=execution.execution_started,
            execution_latency_ms=execution_latency_ms,
            execution_status=status,
            database_error_category=(
                decision.category.value if not execution.is_success else None
            ),
            database_error_message=execution.error_message,
            model=model,
            result_summary=result_summary,
            log_level=level,
        )

    @staticmethod
    def _finish(
        recorder: RunTraceRecorder,
        execution: QueryExecutionResult,
        *,
        repair_succeeded: bool,
        final_status: RunFinalStatus,
        stop_reason: str,
    ) -> Day09WorkflowResult:
        return Day09WorkflowResult(
            trace=recorder.finish(
                final_status=final_status,
                stop_reason=stop_reason,
            ),
            execution=execution,
            repair_succeeded=repair_succeeded,
            business_validation_status=(
                BusinessValidationStatus.NOT_EVALUATED
            ),
        )
