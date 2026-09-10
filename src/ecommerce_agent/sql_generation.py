"""SQL context, generation contract, and guarded read-only execution.

Day 8 adds SQLGlot AST validation, plan-scoped allowlists, row caps, and a
SQLite progress-handler deadline. Day 9 model-driven repair remains out of
scope: every safety rejection is returned directly without another model call.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.ecommerce_agent.analysis_plan import (
    AnalysisPlan,
    FilterOperator,
    TimeRangeMode,
)
from src.ecommerce_agent.model_client import (
    MessageRole,
    ModelClient,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)
from src.ecommerce_agent.retrieval import RetrievalDocument, RetrievalHit
from src.ecommerce_agent.sql_safety import (
    SqlSafetyErrorCode,
    SqlSafetyPolicy,
    SqlSafetyTrace,
    validate_sql_safety,
)


ParameterValue = str | int | float | bool | None


class GeneratedQuery(BaseModel):
    """Untrusted model output after structural validation."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1)
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)


class SqlGenerationErrorType(str, Enum):
    NON_JSON = "non_json"
    INVALID_STRUCTURE = "invalid_structure"
    UNSAFE_STATEMENT = "unsafe_statement"
    PARAMETER_MISMATCH = "parameter_mismatch"
    TRANSIENT_MODEL_CLIENT_ERROR = "transient_model_client_error"
    PERMANENT_MODEL_CLIENT_ERROR = "permanent_model_client_error"


class QueryExecutionErrorType(str, Enum):
    SQL_SYNTAX = "sql_syntax"
    FIELD = "field"
    BINDING = "binding"
    SAFETY = "safety"
    TIMEOUT = "timeout"
    DATABASE = "database"


@dataclass(frozen=True)
class SqlGenerationContext:
    """Canonical, plan-specific context sent to the SQL generator."""

    question: str
    analysis_plan: dict[str, Any]
    retrieved_document_ids: tuple[str, ...]
    metric_definitions: tuple[dict[str, Any], ...]
    dimension_definitions: tuple[dict[str, Any], ...]
    schema_fields: tuple[dict[str, Any], ...]
    table_context: tuple[dict[str, Any], ...]
    parameter_contract: dict[str, ParameterValue]
    safety_policy: SqlSafetyPolicy
    field_scope: str = (
        "canonical columns of required metric and dimension tables; "
        "not arbitrary join permission"
    )

    def to_prompt_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("safety_policy")
        return payload


@dataclass(frozen=True)
class SqlGenerationResult:
    query: GeneratedQuery | None
    response: ModelResponse | None
    error_type: SqlGenerationErrorType | None = None
    error_message: str | None = None
    safety_trace: SqlSafetyTrace | None = None

    @property
    def is_success(self) -> bool:
        return self.query is not None and self.error_type is None


@dataclass(frozen=True)
class QueryExecutionResult:
    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    error_type: QueryExecutionErrorType | None = None
    error_message: str | None = None
    safety_trace: SqlSafetyTrace | None = None
    execution_started: bool = False
    rows_truncated: bool = False
    row_limit: int | None = None
    timeout_seconds: float | None = None

    @property
    def is_success(self) -> bool:
        return self.error_type is None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build_sql_generation_context(
    root: Path,
    question: str,
    plan: AnalysisPlan,
    retrieval_hits: Sequence[RetrievalHit],
    documents: Sequence[RetrievalDocument],
) -> SqlGenerationContext:
    """Hydrate retrieved metric IDs with canonical definitions and schema.

    Retrieval establishes candidate relevance. After the plan passes Day 5
    semantic validation, canonical dictionaries supply complete SQL context so
    RetrievalDocument.fields is never mistaken for a minimal dependency list.
    """
    document_by_id = {document.document_id: document for document in documents}
    retrieved_ids = tuple(hit.document.document_id for hit in retrieval_hits)
    missing_metrics = [
        metric_id
        for metric_id in plan.metrics
        if f"metric:{metric_id}" not in retrieved_ids
    ]
    if missing_metrics:
        raise ValueError(
            "分析计划指标未被本次检索召回："
            + ", ".join(sorted(missing_metrics))
        )

    dimension_rows = _read_csv(
        root / "data" / "metadata" / "dimension_dictionary.csv"
    )
    dimension_by_id = {
        row["dimension_id"]: row for row in dimension_rows
    }
    schema_rows = _read_csv(
        root / "data" / "metadata" / "database_data_dictionary.csv"
    )
    requested_dimensions = set(plan.dimensions)
    requested_dimensions.update(item.field for item in plan.filters)

    metric_documents = [
        document_by_id[f"metric:{metric_id}"]
        for metric_id in plan.metrics
    ]
    metric_definitions = tuple(
        {
            "metric_id": document.identifier,
            "chinese_name": document.chinese_name,
            "definition": document.description,
            "formula": document.formula,
            "base_grain": document.grain,
            "source_tables": document.tables,
            "default_time_field": document.default_time_field,
            "available_dimensions": document.available_dimensions,
            "constraints": document.constraints,
            "source": "data/metadata/metric_dictionary.csv",
        }
        for document in metric_documents
    )
    dimension_definitions = tuple(
        {
            **dimension_by_id[dimension_id],
            "source": "data/metadata/dimension_dictionary.csv",
        }
        for dimension_id in sorted(requested_dimensions)
    )

    required_tables = {
        table
        for document in metric_documents
        for table in document.tables
    }
    required_tables.update(
        dimension_by_id[dimension_id]["source_table"]
        for dimension_id in requested_dimensions
    )
    required_tables.add("fact_orders")
    schema_fields = tuple(
        {
            "qualified_name": (
                f"{row['table_name']}.{row['column_name']}"
            ),
            **row,
            "source": "data/metadata/database_data_dictionary.csv",
        }
        for row in schema_rows
        if row["table_name"] in required_tables
    )
    global_schema: dict[str, set[str]] = {}
    plan_schema: dict[str, set[str]] = {}
    for row in schema_rows:
        global_schema.setdefault(row["table_name"], set()).add(
            row["column_name"]
        )
        if row["table_name"] in required_tables:
            plan_schema.setdefault(row["table_name"], set()).add(
                row["column_name"]
            )

    table_context = []
    for table in sorted(required_tables):
        matching_document = next(
            document
            for document in documents
            if document.document_type == "schema"
            and document.tables == (table,)
        )
        table_context.append(
            {
                "table": table,
                "grain": matching_document.grain,
                "ddl": matching_document.metadata["ddl"],
                "source": "sql/schema.sql",
            }
        )

    return SqlGenerationContext(
        question=question,
        analysis_plan=plan.model_dump(mode="json"),
        retrieved_document_ids=retrieved_ids,
        metric_definitions=metric_definitions,
        dimension_definitions=dimension_definitions,
        schema_fields=schema_fields,
        table_context=tuple(table_context),
        parameter_contract=build_parameter_contract(plan),
        safety_policy=SqlSafetyPolicy(
            global_schema=global_schema,
            plan_schema=plan_schema,
        ),
    )


def build_parameter_contract(
    plan: AnalysisPlan,
) -> dict[str, ParameterValue]:
    parameters: dict[str, ParameterValue] = {}
    if plan.time_range.mode == TimeRangeMode.BOUNDED:
        assert plan.time_range.start_date is not None
        assert plan.time_range.end_date is not None
        parameters["start_date"] = plan.time_range.start_date.isoformat()
        parameters["end_date_exclusive"] = (
            plan.time_range.end_date + timedelta(days=1)
        ).isoformat()

    for index, condition in enumerate(plan.filters):
        if condition.operator == FilterOperator.EQ:
            parameters[f"filter_{index}"] = condition.value
            continue
        assert isinstance(condition.value, list)
        for value_index, value in enumerate(condition.value):
            parameters[f"filter_{index}_{value_index}"] = value
    return parameters


@dataclass
class SqlGenerator:
    client: ModelClient
    config: ModelConfig

    def generate(
        self,
        context: SqlGenerationContext,
    ) -> SqlGenerationResult:
        messages = self._build_messages(context)
        try:
            response = self.client.generate(messages, self.config)
        except TransientModelError:
            return SqlGenerationResult(
                query=None,
                response=None,
                error_type=(
                    SqlGenerationErrorType.TRANSIENT_MODEL_CLIENT_ERROR
                ),
                error_message="SQL 生成模型服务不可用",
            )
        except PermanentModelError:
            return SqlGenerationResult(
                query=None,
                response=None,
                error_type=(
                    SqlGenerationErrorType.PERMANENT_MODEL_CLIENT_ERROR
                ),
                error_message="SQL 生成模型凭据或配置无效",
            )

        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError:
            return SqlGenerationResult(
                query=None,
                response=response,
                error_type=SqlGenerationErrorType.NON_JSON,
                error_message="SQL 生成响应不是有效 JSON",
            )
        try:
            query = GeneratedQuery.model_validate(payload)
        except ValidationError as error:
            return SqlGenerationResult(
                query=None,
                response=response,
                error_type=SqlGenerationErrorType.INVALID_STRUCTURE,
                error_message=_validation_message(error),
            )

        safety = validate_sql_safety(query.sql, context.safety_policy)
        if not safety.is_safe:
            return SqlGenerationResult(
                query=query,
                response=response,
                error_type=SqlGenerationErrorType.UNSAFE_STATEMENT,
                error_message=safety.trace.error_message,
                safety_trace=safety.trace,
            )
        placeholders = set(safety.trace.referenced_parameters)
        expected = set(context.parameter_contract)
        if placeholders != expected or query.parameters != context.parameter_contract:
            return SqlGenerationResult(
                query=None,
                response=response,
                error_type=SqlGenerationErrorType.PARAMETER_MISMATCH,
                error_message=(
                    "SQL 命名占位符和参数必须与计划生成的参数合同完全一致"
                ),
                safety_trace=safety.trace,
            )
        return SqlGenerationResult(
            query=query,
            response=response,
            safety_trace=safety.trace,
        )

    def _build_messages(
        self,
        context: SqlGenerationContext,
    ) -> tuple[ModelMessage, ...]:
        payload = json.dumps(
            context.to_prompt_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system = (
            "你是 SQLite 查询生成器。只返回一个 JSON 对象，字段严格为 "
            "sql 和 parameters。sql 只能是一条 SELECT 或 WITH 查询。"
            "用户日期和筛选值必须使用 parameter_contract 中的命名占位符，"
            "parameters 必须原样返回该合同；不得拼接值。"
            "严格遵守指标定义、时间字段、允许维度、表粒度和 JOIN 限制。"
            "检索字段是保守上下文，不是任意 JOIN 许可。"
            "明细和支付同时使用时必须分别按 order_id 预聚合。"
            f"本次完整上下文：{payload}"
        )
        return (
            ModelMessage(MessageRole.SYSTEM, system),
            ModelMessage(MessageRole.USER, context.question),
        )


def validate_single_read_only_statement(sql: str) -> None:
    """Legacy lexical helper retained for compatibility with Day 7 tests.

    Production generation and execution use ``validate_sql_safety`` instead.
    """
    normalized = _remove_sql_comments_and_literals(sql).strip()
    if not normalized:
        raise ValueError("SQL 不能为空")
    semicolons = [index for index, char in enumerate(normalized) if char == ";"]
    if semicolons:
        if len(semicolons) != 1 or normalized[semicolons[0] + 1 :].strip():
            raise ValueError("只允许执行一条 SQL")
        normalized = normalized[: semicolons[0]].strip()
    first_keyword = re.match(r"[A-Za-z]+", normalized)
    if first_keyword is None or first_keyword.group(0).upper() not in {
        "SELECT",
        "WITH",
    }:
        raise ValueError("只允许 SELECT 或 WITH 查询")


def _remove_sql_comments_and_literals(sql: str) -> str:
    """Mask strings/comments so their semicolons do not look like statements."""
    output: list[str] = []
    index = 0
    state = "normal"
    while index < len(sql):
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if state == "normal":
            if char == "'":
                state = "single_quote"
                output.append(" ")
            elif char == '"':
                state = "double_quote"
                output.append(char)
            elif char == "-" and following == "-":
                state = "line_comment"
                output.extend("  ")
                index += 1
            elif char == "/" and following == "*":
                state = "block_comment"
                output.extend("  ")
                index += 1
            else:
                output.append(char)
        elif state == "single_quote":
            output.append(" ")
            if char == "'" and following == "'":
                output.append(" ")
                index += 1
            elif char == "'":
                state = "normal"
        elif state == "double_quote":
            output.append(char)
            if char == '"' and following == '"':
                output.append(following)
                index += 1
            elif char == '"':
                state = "normal"
        elif state == "line_comment":
            output.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "normal"
        else:
            output.append(" ")
            if char == "*" and following == "/":
                output.append(" ")
                index += 1
                state = "normal"
        index += 1
    if state in {"single_quote", "double_quote", "block_comment"}:
        raise ValueError("SQL 包含未闭合的字符串、标识符或注释")
    return "".join(output)


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """Convert DB-API cursor rows into a stable list of dictionaries."""
    if cursor.description is None:
        return []
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def execute_read_only_query(
    database_path: str | Path,
    sql: str,
    parameters: dict[str, ParameterValue] | None = None,
    *,
    safety_policy: SqlSafetyPolicy,
) -> QueryExecutionResult:
    safety = validate_sql_safety(sql, safety_policy)
    if not safety.is_safe:
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.SAFETY,
            error_message=safety.trace.error_message,
            safety_trace=safety.trace,
            row_limit=safety_policy.max_rows,
            timeout_seconds=safety_policy.timeout_seconds,
        )

    path = Path(database_path).resolve()
    if not path.is_file():
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.DATABASE,
            error_message=f"数据库不存在：{path}",
            safety_trace=safety.trace,
            row_limit=safety_policy.max_rows,
            timeout_seconds=safety_policy.timeout_seconds,
        )
    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro",
            uri=True,
        )
    except sqlite3.Error as error:
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.DATABASE,
            error_message=str(error),
            safety_trace=safety.trace,
            row_limit=safety_policy.max_rows,
            timeout_seconds=safety_policy.timeout_seconds,
        )
    deadline = monotonic() + safety_policy.timeout_seconds
    timed_out = False

    def stop_after_deadline() -> int:
        nonlocal timed_out
        if monotonic() >= deadline:
            timed_out = True
            return 1
        return 0

    try:
        connection.execute("PRAGMA query_only = ON")
        for table in safety.trace.referenced_tables:
            actual_columns = {
                row[1]
                for row in connection.execute(
                    f'PRAGMA table_info("{table}")'
                )
            }
            expected_columns = set(safety_policy.global_schema[table])
            if actual_columns != expected_columns:
                mismatch_trace = replace(
                    safety.trace,
                    accepted=False,
                    error_code=(
                        SqlSafetyErrorCode.DATABASE_SCHEMA_MISMATCH
                    ),
                    error_message=(
                        f"数据库实际字段与全局允许列表不一致：{table}"
                    ),
                )
                return QueryExecutionResult(
                    error_type=QueryExecutionErrorType.SAFETY,
                    error_message=mismatch_trace.error_message,
                    safety_trace=mismatch_trace,
                    row_limit=safety_policy.max_rows,
                    timeout_seconds=safety_policy.timeout_seconds,
                )
        connection.set_progress_handler(
            stop_after_deadline,
            safety_policy.progress_handler_steps,
        )
        cursor = connection.execute(sql, parameters or {})
        columns = tuple(column[0] for column in cursor.description or ())
        fetched = cursor.fetchmany(safety_policy.max_rows + 1)
        rows_truncated = len(fetched) > safety_policy.max_rows
        returned = fetched[: safety_policy.max_rows]
        return QueryExecutionResult(
            columns=columns,
            rows=tuple(
                dict(zip(columns, row)) for row in returned
            ),
            safety_trace=safety.trace,
            execution_started=True,
            rows_truncated=rows_truncated,
            row_limit=safety_policy.max_rows,
            timeout_seconds=safety_policy.timeout_seconds,
        )
    except sqlite3.ProgrammingError as error:
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.BINDING,
            error_message=str(error),
            safety_trace=safety.trace,
            execution_started=True,
            row_limit=safety_policy.max_rows,
            timeout_seconds=safety_policy.timeout_seconds,
        )
    except sqlite3.OperationalError as error:
        message = str(error)
        lowered = message.lower()
        if timed_out and "interrupted" in lowered:
            error_type = QueryExecutionErrorType.TIMEOUT
        elif "syntax error" in lowered or "incomplete input" in lowered:
            error_type = QueryExecutionErrorType.SQL_SYNTAX
        elif "no such column" in lowered or "ambiguous column" in lowered:
            error_type = QueryExecutionErrorType.FIELD
        elif "readonly" in lowered or "not authorized" in lowered:
            error_type = QueryExecutionErrorType.SAFETY
        else:
            error_type = QueryExecutionErrorType.DATABASE
        return QueryExecutionResult(
            error_type=error_type,
            error_message=message,
            safety_trace=safety.trace,
            execution_started=True,
            row_limit=safety_policy.max_rows,
            timeout_seconds=safety_policy.timeout_seconds,
        )
    except sqlite3.DatabaseError as error:
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.DATABASE,
            error_message=str(error),
            safety_trace=safety.trace,
            execution_started=True,
            row_limit=safety_policy.max_rows,
            timeout_seconds=safety_policy.timeout_seconds,
        )
    finally:
        connection.set_progress_handler(None, 0)
        connection.close()


def _validation_message(error: ValidationError) -> str:
    detail = error.errors(include_url=False, include_input=False)[0]
    location = ".".join(str(part) for part in detail["loc"])
    return f"{location}: {detail['msg']}" if location else detail["msg"]
