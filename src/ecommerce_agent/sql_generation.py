"""Day 7 SQL context, generation contract, and minimal read-only execution.

This module deliberately stops short of Day 8 AST validation and Day 9 repair.
It still enforces the minimum boundary needed to execute generated SQL safely:
one SELECT/WITH statement, named value binding, and a read-only SQLite handle.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import Enum
from pathlib import Path
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
    field_scope: str = (
        "canonical columns of required metric and dimension tables; "
        "not arbitrary join permission"
    )

    def to_prompt_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SqlGenerationResult:
    query: GeneratedQuery | None
    response: ModelResponse | None
    error_type: SqlGenerationErrorType | None = None
    error_message: str | None = None

    @property
    def is_success(self) -> bool:
        return self.query is not None and self.error_type is None


@dataclass(frozen=True)
class QueryExecutionResult:
    columns: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    error_type: QueryExecutionErrorType | None = None
    error_message: str | None = None

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

        try:
            validate_single_read_only_statement(query.sql)
        except ValueError as error:
            return SqlGenerationResult(
                query=None,
                response=response,
                error_type=SqlGenerationErrorType.UNSAFE_STATEMENT,
                error_message=str(error),
            )
        placeholders = set(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", query.sql))
        expected = set(context.parameter_contract)
        if placeholders != expected or query.parameters != context.parameter_contract:
            return SqlGenerationResult(
                query=None,
                response=response,
                error_type=SqlGenerationErrorType.PARAMETER_MISMATCH,
                error_message=(
                    "SQL 命名占位符和参数必须与计划生成的参数合同完全一致"
                ),
            )
        return SqlGenerationResult(query=query, response=response)

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
    """Lexical Day 7 guard; full SQL AST enforcement belongs to Day 8."""
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
) -> QueryExecutionResult:
    try:
        validate_single_read_only_statement(sql)
    except ValueError as error:
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.SAFETY,
            error_message=str(error),
        )

    path = Path(database_path).resolve()
    if not path.is_file():
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.DATABASE,
            error_message=f"数据库不存在：{path}",
        )
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        cursor = connection.execute(sql, parameters or {})
        columns = tuple(column[0] for column in cursor.description or ())
        return QueryExecutionResult(
            columns=columns,
            rows=tuple(rows_to_dicts(cursor)),
        )
    except sqlite3.ProgrammingError as error:
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.BINDING,
            error_message=str(error),
        )
    except sqlite3.OperationalError as error:
        message = str(error)
        lowered = message.lower()
        if "syntax error" in lowered or "incomplete input" in lowered:
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
        )
    except sqlite3.DatabaseError as error:
        return QueryExecutionResult(
            error_type=QueryExecutionErrorType.DATABASE,
            error_message=str(error),
        )
    finally:
        connection.close()


def _validation_message(error: ValidationError) -> str:
    detail = error.errors(include_url=False, include_input=False)[0]
    location = ".".join(str(part) for part in detail["loc"])
    return f"{location}: {detail['msg']}" if location else detail["msg"]
