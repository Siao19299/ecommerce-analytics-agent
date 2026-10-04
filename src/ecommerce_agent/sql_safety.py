"""SQL security SQL AST validation and plan-scoped read-only policy.

SQL text is untrusted.  SQLGlot parses SQLite syntax into an AST, then this
module applies a default-deny query/type check and resolves tables and columns
through aliases, CTEs, subqueries, and stars before SQLite can execute it.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope


SQLITE_DIALECT = "sqlite"
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class SqlSafetyErrorCode(str, Enum):
    EMPTY_SQL = "empty_sql"
    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NON_QUERY = "non_query"
    FORBIDDEN_OPERATION = "forbidden_operation"
    FORBIDDEN_FUNCTION = "forbidden_function"
    PARAMETER_STYLE_DENIED = "parameter_style_denied"
    UNSUPPORTED_SOURCE = "unsupported_source"
    GLOBAL_TABLE_DENIED = "global_table_denied"
    PLAN_TABLE_DENIED = "plan_table_denied"
    COLUMN_RESOLUTION_FAILED = "column_resolution_failed"
    GLOBAL_COLUMN_DENIED = "global_column_denied"
    PLAN_COLUMN_DENIED = "plan_column_denied"
    DATABASE_SCHEMA_MISMATCH = "database_schema_mismatch"


# A query root is not sufficient on its own: SQLGlot can represent a mutation
# nested inside a WITH expression whose outer root is SELECT.
FORBIDDEN_NODE_TYPES = tuple(
    node_type
    for node_type in (
        getattr(exp, name, None)
        for name in (
            "Insert",
            "Update",
            "Delete",
            "Merge",
            "Copy",
            "Create",
            "Drop",
            "Alter",
            "TruncateTable",
            "Command",
            "Transaction",
            "Commit",
            "Rollback",
            "Use",
            "Set",
            "Pragma",
            "Attach",
            "Detach",
            "Grant",
            "Revoke",
            "Analyze",
            "Into",
        )
    )
    if node_type is not None
)

# These SQLite/CLI functions can load code, read or write files, expose parser
# internals, or bypass the ordinary six-table data boundary when extensions are
# available. Unknown scalar functions are left to SQLite execution errors;
# table-valued functions are denied separately as unsupported sources.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "edit",
        "fts3_tokenizer",
        "load_extension",
        "randomblob",
        "readfile",
        "writefile",
        "zeroblob",
    }
)


def _normalize_schema(
    schema: Mapping[str, object],
) -> dict[str, frozenset[str]]:
    normalized: dict[str, frozenset[str]] = {}
    for table, columns in schema.items():
        table_name = str(table).strip().lower()
        if not IDENTIFIER_PATTERN.fullmatch(table_name):
            raise ValueError("安全策略中的表名必须是普通 SQL 标识符")
        if isinstance(columns, Mapping):
            column_names = columns.keys()
        else:
            if isinstance(columns, (str, bytes)):
                raise ValueError(
                    f"表 {table_name} 的允许字段必须是字段集合"
                )
            column_names = columns  # type: ignore[assignment]
        normalized[table_name] = frozenset(
            str(column).strip().lower() for column in column_names
        )
        if not normalized[table_name] or any(
            not IDENTIFIER_PATTERN.fullmatch(column)
            for column in normalized[table_name]
        ):
            raise ValueError(
                f"表 {table_name} 的允许字段必须是普通 SQL 标识符"
            )
    if not normalized:
        raise ValueError("全局 SQL 允许列表不能为空")
    return normalized


@dataclass(frozen=True)
class SqlSafetyPolicy:
    """Global data boundary intersected with one validated analysis scope."""

    global_schema: Mapping[str, object]
    plan_schema: Mapping[str, object]
    max_rows: int = 1000
    timeout_seconds: float = 10.0
    progress_handler_steps: int = 1000

    def __post_init__(self) -> None:
        global_schema = _normalize_schema(self.global_schema)
        plan_schema = _normalize_schema(self.plan_schema)
        object.__setattr__(self, "global_schema", global_schema)
        object.__setattr__(self, "plan_schema", plan_schema)
        unknown_tables = set(plan_schema) - set(global_schema)
        if unknown_tables:
            raise ValueError(
                "本次计划包含全局范围外的表："
                + ", ".join(sorted(unknown_tables))
            )
        for table, columns in plan_schema.items():
            unknown_columns = columns - global_schema[table]
            if unknown_columns:
                raise ValueError(
                    f"本次计划包含 {table} 的全局范围外字段："
                    + ", ".join(sorted(unknown_columns))
                )
        if (
            isinstance(self.max_rows, bool)
            or not isinstance(self.max_rows, int)
            or self.max_rows < 1
        ):
            raise ValueError("max_rows 必须是正整数")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds 必须大于 0")
        if (
            isinstance(self.progress_handler_steps, bool)
            or not isinstance(self.progress_handler_steps, int)
            or self.progress_handler_steps < 1
        ):
            raise ValueError("progress_handler_steps 必须是正整数")

    @property
    def effective_schema(self) -> dict[str, frozenset[str]]:
        return {
            table: frozenset(columns & self.global_schema[table])
            for table, columns in self.plan_schema.items()
        }

    def trace_payload(self) -> dict[str, object]:
        def serializable(schema: Mapping[str, frozenset[str]]):
            return {
                table: sorted(columns)
                for table, columns in sorted(schema.items())
            }

        return {
            "scope_rule": "global_core_schema_intersection_analysis_plan_schema",
            "global_schema": serializable(self.global_schema),
            "plan_schema": serializable(self.plan_schema),
            "effective_schema": serializable(self.effective_schema),
            "max_rows": self.max_rows,
            "timeout_seconds": self.timeout_seconds,
            "progress_handler_steps": self.progress_handler_steps,
        }


def load_global_schema(root: Path) -> dict[str, frozenset[str]]:
    path = root / "data" / "metadata" / "database_data_dictionary.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    schema: dict[str, set[str]] = {}
    for row in rows:
        schema.setdefault(row["table_name"], set()).add(row["column_name"])
    return {table: frozenset(columns) for table, columns in schema.items()}


def build_global_sql_policy(
    root: Path,
    *,
    max_rows: int = 1000,
    timeout_seconds: float = 10.0,
    progress_handler_steps: int = 1000,
) -> SqlSafetyPolicy:
    schema = load_global_schema(root)
    return SqlSafetyPolicy(
        global_schema=schema,
        plan_schema=schema,
        max_rows=max_rows,
        timeout_seconds=timeout_seconds,
        progress_handler_steps=progress_handler_steps,
    )


@dataclass(frozen=True)
class SqlSafetyTrace:
    accepted: bool
    dialect: str = SQLITE_DIALECT
    statement_count: int = 0
    root_expression: str | None = None
    referenced_tables: tuple[str, ...] = ()
    referenced_columns: tuple[str, ...] = ()
    referenced_parameters: tuple[str, ...] = ()
    error_code: SqlSafetyErrorCode | None = None
    error_message: str | None = None
    policy: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["error_code"] = (
            self.error_code.value if self.error_code is not None else None
        )
        return payload


@dataclass(frozen=True)
class SqlSafetyResult:
    trace: SqlSafetyTrace

    @property
    def is_safe(self) -> bool:
        return self.trace.accepted


def _rejected(
    policy: SqlSafetyPolicy,
    code: SqlSafetyErrorCode,
    message: str,
    *,
    statement_count: int = 0,
    root_expression: str | None = None,
    tables: set[str] | None = None,
    columns: set[str] | None = None,
    parameters: set[str] | None = None,
) -> SqlSafetyResult:
    return SqlSafetyResult(
        SqlSafetyTrace(
            accepted=False,
            statement_count=statement_count,
            root_expression=root_expression,
            referenced_tables=tuple(sorted(tables or set())),
            referenced_columns=tuple(sorted(columns or set())),
            referenced_parameters=tuple(sorted(parameters or set())),
            error_code=code,
            error_message=message,
            policy=policy.trace_payload(),
        )
    )


def _function_name(function: exp.Func) -> str:
    return (function.name or function.sql_name()).lower()


def _physical_sources(
    expression: exp.Expression,
) -> tuple[set[str], exp.Table | None]:
    tables: set[str] = set()
    for scope in traverse_scope(expression):
        for _alias, (_node, source) in scope.selected_sources.items():
            if isinstance(source, Scope):
                continue
            if not isinstance(source, exp.Table):
                continue
            if not isinstance(source.this, exp.Identifier):
                return tables, source
            if source.catalog:
                return tables, source
            database = source.db.lower()
            if database not in {"", "main"}:
                return tables, source
            tables.add(source.name.lower())
    return tables, None


def _resolve_column_source(
    scope: Scope,
    table_alias: str,
) -> exp.Table | Scope | None:
    current: Scope | None = scope
    while current is not None:
        source = current.sources.get(table_alias)
        if source is not None:
            return source
        current = current.parent
    return None


def _physical_columns(expression: exp.Expression) -> set[str]:
    columns: set[str] = set()
    for scope in traverse_scope(expression):
        for column in scope.columns:
            if not column.table:
                continue
            source = _resolve_column_source(scope, column.table)
            if isinstance(source, exp.Table) and isinstance(
                source.this, exp.Identifier
            ):
                columns.add(f"{source.name.lower()}.{column.name.lower()}")
    return columns


def validate_sql_safety(
    sql: str,
    policy: SqlSafetyPolicy,
) -> SqlSafetyResult:
    """Parse and validate one SQLite query against global and plan scopes."""
    if not sql.strip():
        return _rejected(
            policy,
            SqlSafetyErrorCode.EMPTY_SQL,
            "SQL 不能为空",
        )
    try:
        parsed = sqlglot.parse(
            sql,
            read=SQLITE_DIALECT,
            error_level=sqlglot.ErrorLevel.RAISE,
        )
    except (SqlglotError, ValueError, RecursionError) as error:
        return _rejected(
            policy,
            SqlSafetyErrorCode.PARSE_ERROR,
            f"SQLite SQL 解析失败：{error}",
        )
    if len(parsed) != 1 or parsed[0] is None:
        nonempty_count = sum(statement is not None for statement in parsed)
        if nonempty_count == 0:
            return _rejected(
                policy,
                SqlSafetyErrorCode.EMPTY_SQL,
                "SQL 不能为空",
                statement_count=len(parsed),
            )
        return _rejected(
            policy,
            SqlSafetyErrorCode.MULTIPLE_STATEMENTS,
            "只允许执行一条 SQL 查询",
            statement_count=len(parsed),
        )

    expression = parsed[0]
    assert expression is not None
    root_name = type(expression).__name__
    if not isinstance(expression, exp.Query):
        return _rejected(
            policy,
            SqlSafetyErrorCode.NON_QUERY,
            f"只允许只读查询，当前根节点为 {root_name}",
            statement_count=1,
            root_expression=root_name,
        )
    if next(expression.find_all(exp.Select), None) is None:
        return _rejected(
            policy,
            SqlSafetyErrorCode.NON_QUERY,
            "只允许 SELECT 或包含 SELECT 的只读集合查询",
            statement_count=1,
            root_expression=root_name,
        )
    parameters: set[str] = set()
    unsupported_placeholders = []
    for placeholder in expression.find_all(exp.Placeholder):
        if not isinstance(placeholder.this, str) or not placeholder.this:
            unsupported_placeholders.append(placeholder.sql(dialect=SQLITE_DIALECT))
        else:
            parameters.add(placeholder.this)
    if unsupported_placeholders or next(
        expression.find_all(exp.Parameter), None
    ) is not None:
        return _rejected(
            policy,
            SqlSafetyErrorCode.PARAMETER_STYLE_DENIED,
            "只允许 :name 形式的 SQLite 命名参数，不允许位置参数或其他参数语法",
            statement_count=1,
            root_expression=root_name,
            parameters=parameters,
        )

    # SQLGlot intentionally accepts some tolerant forms that SQLite itself
    # rejects (for example ``SELECT FROM table`` or ``FROM FROM table``).
    # Reject those malformed query shapes before allowlist resolution.
    malformed_select = next(
        (
            select
            for select in expression.find_all(exp.Select)
            if not select.expressions
            or (
                isinstance(select.args.get("from_"), exp.From)
                and isinstance(select.args["from_"].this, exp.Query)
            )
        ),
        None,
    )
    if malformed_select is not None:
        return _rejected(
            policy,
            SqlSafetyErrorCode.PARSE_ERROR,
            "SQLite SQL 解析后仍包含不完整或非法的 SELECT 结构",
            statement_count=1,
            root_expression=root_name,
        )

    forbidden = next(expression.find_all(*FORBIDDEN_NODE_TYPES), None)
    if forbidden is not None:
        return _rejected(
            policy,
            SqlSafetyErrorCode.FORBIDDEN_OPERATION,
            f"查询包含不允许的操作：{type(forbidden).__name__}",
            statement_count=1,
            root_expression=root_name,
        )
    for function in expression.find_all(exp.Func):
        function_name = _function_name(function)
        if function_name in FORBIDDEN_FUNCTIONS:
            return _rejected(
                policy,
                SqlSafetyErrorCode.FORBIDDEN_FUNCTION,
                f"查询包含不允许的 SQLite 函数：{function_name}",
                statement_count=1,
                root_expression=root_name,
            )

    tables, unsupported_source = _physical_sources(expression)
    if unsupported_source is not None:
        return _rejected(
            policy,
            SqlSafetyErrorCode.UNSUPPORTED_SOURCE,
            "不允许表值函数、外部数据源或非 main 数据库限定符",
            statement_count=1,
            root_expression=root_name,
            tables=tables,
        )
    globally_denied = tables - set(policy.global_schema)
    if globally_denied:
        return _rejected(
            policy,
            SqlSafetyErrorCode.GLOBAL_TABLE_DENIED,
            "查询引用全局六表允许列表之外的表："
            + ", ".join(sorted(globally_denied)),
            statement_count=1,
            root_expression=root_name,
            tables=tables,
        )
    plan_denied = tables - set(policy.effective_schema)
    if plan_denied:
        return _rejected(
            policy,
            SqlSafetyErrorCode.PLAN_TABLE_DENIED,
            "查询引用本次 AnalysisPlan 范围之外的表："
            + ", ".join(sorted(plan_denied)),
            statement_count=1,
            root_expression=root_name,
            tables=tables,
        )

    qualifier_schema = {
        table: {column: "UNKNOWN" for column in columns}
        for table, columns in policy.global_schema.items()
    }
    try:
        qualified = qualify(
            expression.copy(),
            dialect=SQLITE_DIALECT,
            schema=qualifier_schema,
            expand_stars=True,
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
    except (SqlglotError, ValueError, RecursionError) as error:
        return _rejected(
            policy,
            SqlSafetyErrorCode.COLUMN_RESOLUTION_FAILED,
            f"字段、别名、CTE 或通配符无法安全解析：{error}",
            statement_count=1,
            root_expression=root_name,
            tables=tables,
        )

    columns = _physical_columns(qualified)
    globally_denied_columns = {
        qualified_name
        for qualified_name in columns
        if qualified_name.split(".", 1)[1]
        not in policy.global_schema[qualified_name.split(".", 1)[0]]
    }
    if globally_denied_columns:
        return _rejected(
            policy,
            SqlSafetyErrorCode.GLOBAL_COLUMN_DENIED,
            "查询引用全局字段允许列表之外的字段："
            + ", ".join(sorted(globally_denied_columns)),
            statement_count=1,
            root_expression=root_name,
            tables=tables,
            columns=columns,
        )
    plan_denied_columns = {
        qualified_name
        for qualified_name in columns
        if qualified_name.split(".", 1)[1]
        not in policy.effective_schema[qualified_name.split(".", 1)[0]]
    }
    if plan_denied_columns:
        return _rejected(
            policy,
            SqlSafetyErrorCode.PLAN_COLUMN_DENIED,
            "查询引用本次 AnalysisPlan 范围之外的字段："
            + ", ".join(sorted(plan_denied_columns)),
            statement_count=1,
            root_expression=root_name,
            tables=tables,
            columns=columns,
        )

    return SqlSafetyResult(
        SqlSafetyTrace(
            accepted=True,
            statement_count=1,
            root_expression=root_name,
            referenced_tables=tuple(sorted(tables)),
            referenced_columns=tuple(sorted(columns)),
            policy=policy.trace_payload(),
            referenced_parameters=tuple(sorted(parameters)),
        )
    )
