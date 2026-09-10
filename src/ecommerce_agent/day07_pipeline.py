"""Day 7 minimal Text-to-SQL workflow with reproducible intermediate state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any, Sequence

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.retrieval import (
    RetrievalDocument,
    RetrievalFilter,
    Retriever,
    retrieve_payload,
)
from src.ecommerce_agent.sql_generation import (
    SqlGenerator,
    build_sql_generation_context,
    execute_read_only_query,
)


class Day07Status(str, Enum):
    SUCCEEDED = "succeeded"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"


class Day07ErrorCategory(str, Enum):
    RETRIEVAL = "retrieval"
    PLAN_GROUNDING = "plan_grounding"
    SQL_SYNTAX = "sql_syntax"
    FIELD = "field"
    BUSINESS_SEMANTICS = "business_semantics"
    SQL_SAFETY = "sql_safety"
    MODEL_OUTPUT = "model_output"
    DATABASE = "database"


@dataclass
class Day07Agent:
    root: Path
    database_path: Path
    documents: Sequence[RetrievalDocument]
    retriever: Retriever
    planner: AnalysisPlanner
    sql_generator: SqlGenerator
    metric_top_k: int = 5
    schema_top_k: int = 8
    planning_response_source: str = "unspecified_model_response"
    sql_response_source: str = "unspecified_model_response"

    def run(self, question: str) -> dict[str, Any]:
        started_at = perf_counter()
        created_at = datetime.now(timezone.utc)
        metric_retrieval = retrieve_payload(
            self.retriever,
            question,
            top_k=self.metric_top_k,
            filters=RetrievalFilter(
                document_types=frozenset({"metric"})
            ),
        )
        schema_retrieval = retrieve_payload(
            self.retriever,
            question,
            top_k=self.schema_top_k,
            filters=RetrievalFilter(
                document_types=frozenset({"schema"})
            ),
        )
        hits = tuple(
            self.retriever.search(
                question,
                top_k=self.metric_top_k,
                filters=RetrievalFilter(
                    document_types=frozenset({"metric"})
                ),
            )
        ) + tuple(
            self.retriever.search(
                question,
                top_k=self.schema_top_k,
                filters=RetrievalFilter(
                    document_types=frozenset({"schema"})
                ),
            )
        )
        record: dict[str, Any] = {
            "question": question,
            "retrieval": {
                "metric": metric_retrieval,
                "schema": schema_retrieval,
            },
            "analysis_plan": None,
            "analysis_plan_evidence_document_ids": None,
            "sql_generation_context": None,
            "sql": None,
            "parameters": None,
            "sql_safety": None,
            "query_result": None,
            "error": None,
            "metadata": {
                "created_at": created_at.isoformat(),
                "retriever_version": getattr(
                    self.retriever,
                    "version",
                    type(self.retriever).__name__,
                ),
                "metric_top_k": self.metric_top_k,
                "schema_top_k": self.schema_top_k,
                "planning_response_source": (
                    self.planning_response_source
                ),
                "sql_response_source": self.sql_response_source,
                "database_path": str(self.database_path),
                "database_mode": "sqlite_uri_mode_ro_and_query_only",
                "sql_safety_scope": (
                    "day8_sqlglot_sqlite_ast_global_and_plan_allowlists_"
                    "plus_row_and_timeout_limits"
                ),
            },
        }

        planning = self.planner.create_plan(question, hits)
        record["metadata"]["planning_run_id"] = planning.run_id
        record["metadata"]["planning_attempts"] = len(
            planning.model_responses
        )
        record["metadata"]["planning_model"] = (
            planning.model_response.model_name
            if planning.model_response is not None
            else self.planner.config.model_name
        )
        record["metadata"]["planning_usage"] = [
            {
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "latency_ms": response.latency_ms,
                "finish_reason": response.finish_reason,
            }
            for response in planning.model_responses
        ]

        if not planning.is_success:
            validation = planning.validation
            if validation is not None and validation.plan is not None:
                record["analysis_plan"] = validation.plan.model_dump(
                    mode="json"
                )
                record["analysis_plan_evidence_document_ids"] = list(
                    validation.evidence_document_ids
                )
            message = (
                validation.error_message
                if validation is not None
                else planning.client_error_message
            )
            if (
                validation is not None
                and validation.error_type is not None
                and validation.error_type.value == "invalid_semantics"
            ):
                category = Day07ErrorCategory.BUSINESS_SEMANTICS
            elif (
                validation is not None
                and validation.error_type is not None
                and validation.error_type.value == "ungrounded"
            ):
                category = Day07ErrorCategory.PLAN_GROUNDING
            else:
                category = Day07ErrorCategory.MODEL_OUTPUT
            return self._finish(
                record,
                started_at,
                Day07Status.FAILED,
                category,
                message or "分析计划生成失败",
            )

        assert planning.validation is not None
        if planning.validation.needs_clarification:
            record["clarification_question"] = (
                planning.validation.clarification_question
            )
            return self._finish(
                record,
                started_at,
                Day07Status.NEEDS_CLARIFICATION,
            )

        plan = planning.validation.plan
        assert plan is not None
        record["analysis_plan"] = plan.model_dump(mode="json")
        record["analysis_plan_evidence_document_ids"] = list(
            planning.validation.evidence_document_ids
        )
        try:
            context = build_sql_generation_context(
                self.root,
                question,
                plan,
                hits,
                self.documents,
            )
        except ValueError as error:
            return self._finish(
                record,
                started_at,
                Day07Status.FAILED,
                Day07ErrorCategory.RETRIEVAL,
                str(error),
            )
        record["sql_generation_context"] = context.to_prompt_payload()

        generation = self.sql_generator.generate(context)
        if generation.query is not None:
            record["sql"] = generation.query.sql
            record["parameters"] = generation.query.parameters
        if generation.safety_trace is not None:
            record["sql_safety"] = generation.safety_trace.to_dict()
        if not generation.is_success:
            return self._finish(
                record,
                started_at,
                Day07Status.FAILED,
                Day07ErrorCategory.SQL_SAFETY
                if generation.error_type is not None
                and generation.error_type.value == "unsafe_statement"
                else Day07ErrorCategory.MODEL_OUTPUT,
                generation.error_message or "SQL 生成失败",
            )
        assert generation.query is not None
        if generation.response is not None:
            record["metadata"]["sql_model"] = (
                generation.response.model_name
            )
            record["metadata"]["sql_usage"] = {
                "prompt_tokens": generation.response.prompt_tokens,
                "completion_tokens": generation.response.completion_tokens,
                "latency_ms": generation.response.latency_ms,
                "finish_reason": generation.response.finish_reason,
            }

        execution = execute_read_only_query(
            self.database_path,
            generation.query.sql,
            generation.query.parameters,
            safety_policy=context.safety_policy,
        )
        record["sql_safety"] = (
            execution.safety_trace.to_dict()
            if execution.safety_trace is not None
            else record["sql_safety"]
        )
        record["query_result"] = {
            "columns": list(execution.columns),
            "rows": list(execution.rows),
            "rows_truncated": execution.rows_truncated,
            "row_limit": execution.row_limit,
            "timeout_seconds": execution.timeout_seconds,
            "execution_started": execution.execution_started,
        }
        if not execution.is_success:
            category_by_execution = {
                "sql_syntax": Day07ErrorCategory.SQL_SYNTAX,
                "field": Day07ErrorCategory.FIELD,
                "safety": Day07ErrorCategory.SQL_SAFETY,
                "binding": Day07ErrorCategory.MODEL_OUTPUT,
                "timeout": Day07ErrorCategory.SQL_SAFETY,
                "database": Day07ErrorCategory.DATABASE,
            }
            assert execution.error_type is not None
            return self._finish(
                record,
                started_at,
                Day07Status.FAILED,
                category_by_execution[execution.error_type.value],
                execution.error_message or "数据库执行失败",
            )
        return self._finish(
            record,
            started_at,
            Day07Status.SUCCEEDED,
        )

    @staticmethod
    def _finish(
        record: dict[str, Any],
        started_at: float,
        status: Day07Status,
        category: Day07ErrorCategory | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        record["status"] = status.value
        if category is not None:
            record["error"] = {
                "category": category.value,
                "message": message,
            }
        record["metadata"]["workflow_latency_ms"] = (
            perf_counter() - started_at
        ) * 1000
        return record
