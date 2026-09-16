"""Controlled Day 9 SQL repair context and one-candidate generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import ValidationError

from src.ecommerce_agent.day09_sql_identity import SqlIdentity
from src.ecommerce_agent.day09_trace import sanitize_error_message
from src.ecommerce_agent.model_client import (
    MessageRole,
    ModelClient,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)
from src.ecommerce_agent.sql_generation import (
    GeneratedQuery,
    ParameterValue,
    SqlGenerationContext,
)
from src.ecommerce_agent.sql_safety import (
    SqlSafetyTrace,
    validate_sql_safety,
)


REPAIR_CONTEXT_SOURCE = (
    "validated_analysis_plan_context_and_sanitized_sqlite_error"
)


class SqlRepairErrorType(str, Enum):
    NON_JSON = "non_json"
    INVALID_STRUCTURE = "invalid_structure"
    UNSAFE_CANDIDATE = "unsafe_candidate"
    PARAMETER_MISMATCH = "parameter_mismatch"
    TRANSIENT_MODEL_CLIENT_ERROR = "transient_model_client_error"
    PERMANENT_MODEL_CLIENT_ERROR = "permanent_model_client_error"


@dataclass(frozen=True)
class SqlRepairContext:
    source_sql_attempt: int
    original_sql: str
    original_identity: SqlIdentity
    database_error_rule: str
    sanitized_database_error: str
    attempted_sql_hashes: tuple[str, ...]
    generation_context: SqlGenerationContext
    context_source: str = REPAIR_CONTEXT_SOURCE

    def __post_init__(self) -> None:
        if self.source_sql_attempt < 1:
            raise ValueError("source_sql_attempt 必须是正整数")
        sanitized = sanitize_error_message(self.sanitized_database_error)
        object.__setattr__(self, "sanitized_database_error", sanitized or "")
        if not self.sanitized_database_error:
            raise ValueError("数据库错误不能为空")
        if self.original_identity.sha256 not in self.attempted_sql_hashes:
            raise ValueError("已尝试哈希必须包含原 SQL")

    def to_prompt_payload(self) -> dict[str, Any]:
        """Expose only the validated plan scope needed for local repair."""
        context = self.generation_context
        return {
            "context_source": self.context_source,
            "source_sql_attempt": self.source_sql_attempt,
            "analysis_plan": context.analysis_plan,
            "metric_definitions": context.metric_definitions,
            "dimension_definitions": context.dimension_definitions,
            "schema_fields": context.schema_fields,
            "table_context": context.table_context,
            "field_scope": context.field_scope,
            "parameter_contract": context.parameter_contract,
            "original_sql": self.original_sql,
            "original_normalized_sql": (
                self.original_identity.normalized_sql
            ),
            "original_sql_hash": self.original_identity.sha256,
            "database_error_rule": self.database_error_rule,
            "database_error_message": self.sanitized_database_error,
            "attempted_sql_hashes": self.attempted_sql_hashes,
        }


@dataclass(frozen=True)
class SqlRepairResult:
    query: GeneratedQuery | None
    response: ModelResponse | None
    error_type: SqlRepairErrorType | None = None
    error_message: str | None = None
    safety_trace: SqlSafetyTrace | None = None
    transport_attempts: int | None = None

    @property
    def is_success(self) -> bool:
        return self.query is not None and self.error_type is None


@dataclass
class SqlRepairer:
    client: ModelClient
    config: ModelConfig

    def repair(self, context: SqlRepairContext) -> SqlRepairResult:
        messages = self._build_messages(context)
        try:
            response = self.client.generate(messages, self.config)
        except TransientModelError as error:
            return SqlRepairResult(
                query=None,
                response=None,
                error_type=SqlRepairErrorType.TRANSIENT_MODEL_CLIENT_ERROR,
                error_message="SQL 修复模型服务在有限传输重试后仍不可用",
                transport_attempts=getattr(error, "transport_attempts", 1),
            )
        except PermanentModelError as error:
            return SqlRepairResult(
                query=None,
                response=None,
                error_type=SqlRepairErrorType.PERMANENT_MODEL_CLIENT_ERROR,
                error_message="SQL 修复模型凭据或配置无效",
                transport_attempts=getattr(error, "transport_attempts", 1),
            )

        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError:
            return SqlRepairResult(
                query=None,
                response=response,
                error_type=SqlRepairErrorType.NON_JSON,
                error_message="SQL 修复响应不是有效 JSON",
                transport_attempts=response.transport_attempts,
            )
        try:
            query = GeneratedQuery.model_validate(payload)
        except ValidationError as error:
            first = error.errors(
                include_url=False,
                include_input=False,
            )[0]
            return SqlRepairResult(
                query=None,
                response=response,
                error_type=SqlRepairErrorType.INVALID_STRUCTURE,
                error_message=first["msg"],
                transport_attempts=response.transport_attempts,
            )

        safety = validate_sql_safety(
            query.sql,
            context.generation_context.safety_policy,
        )
        if not safety.is_safe:
            return SqlRepairResult(
                query=query,
                response=response,
                error_type=SqlRepairErrorType.UNSAFE_CANDIDATE,
                error_message=safety.trace.error_message,
                safety_trace=safety.trace,
                transport_attempts=response.transport_attempts,
            )

        placeholders = set(safety.trace.referenced_parameters)
        expected_parameters = context.generation_context.parameter_contract
        if (
            placeholders != set(expected_parameters)
            or query.parameters != expected_parameters
        ):
            return SqlRepairResult(
                query=query,
                response=response,
                error_type=SqlRepairErrorType.PARAMETER_MISMATCH,
                error_message=(
                    "修复 SQL 的命名占位符和参数必须与原计划合同完全一致"
                ),
                safety_trace=safety.trace,
                transport_attempts=response.transport_attempts,
            )
        return SqlRepairResult(
            query=query,
            response=response,
            safety_trace=safety.trace,
            transport_attempts=response.transport_attempts,
        )

    @staticmethod
    def _build_messages(
        context: SqlRepairContext,
    ) -> tuple[ModelMessage, ...]:
        payload = json.dumps(
            context.to_prompt_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system = (
            "你是受限的 SQLite SQL 修复器。只返回一个 JSON 对象，"
            "字段严格为 sql 和 parameters。只修复给定的 SQLite 执行错误。"
            "不得增加或替换指标、维度、筛选、时间口径、表、字段或 JOIN 范围；"
            "不得改变指标公式和表粒度；明细与支付仍须分别按 order_id 预聚合。"
            "必须原样保留 parameter_contract，不得改写或新增参数。"
            "不得返回 attempted_sql_hashes 中已经尝试过的 SQL。"
            "输出仍会重新经过 AST、全局与计划允许列表及参数合同校验。"
            f"受控修复上下文：{payload}"
        )
        return (
            ModelMessage(MessageRole.SYSTEM, system),
            ModelMessage(
                MessageRole.USER,
                "修复该 SQL 的局部执行错误，只返回规定 JSON。",
            ),
        )
