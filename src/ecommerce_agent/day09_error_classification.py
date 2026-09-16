"""Day 9 repair eligibility for errors returned by guarded SQLite execution.

This module does not repair SQL.  It only decides whether an execution failure
may enter a later, bounded repair workflow.  Day 8 safety decisions remain the
authority: safety, timeout, contract, environment, and ambiguous database
failures are never promoted to model repair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from src.ecommerce_agent.sql_generation import (
    QueryExecutionErrorType,
    QueryExecutionResult,
)


class RepairEligibilityCategory(str, Enum):
    """Stable categories used before any SQL repair model call."""

    NOT_APPLICABLE = "not_applicable"
    SAFETY_FAILURE = "safety_failure"
    RESOURCE_FAILURE = "resource_failure"
    CONTRACT_FAILURE = "contract_failure"
    REPAIRABLE_SQL_ERROR = "repairable_sql_error"
    ENVIRONMENT_ERROR = "environment_error"
    UNCLASSIFIED_DATABASE_ERROR = "unclassified_database_error"


@dataclass(frozen=True)
class RepairEligibilityDecision:
    category: RepairEligibilityCategory
    eligible: bool
    reason: str
    rule_id: str


# These messages were verified against the real project SQLite database after
# the query passed the Day 8 safety gate.  Matching remains deliberately narrow:
# an unfamiliar SQLite error must not be sent to a model by default.
_REPAIRABLE_SQL_ERROR_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "sqlite_missing_scalar_function",
        re.compile(r"^no such function: [A-Za-z_][A-Za-z0-9_]*$", re.I),
    ),
    (
        "sqlite_function_argument_count",
        re.compile(
            r"^wrong number of arguments to function "
            r"[A-Za-z_][A-Za-z0-9_]*\(\)$",
            re.I,
        ),
    ),
    (
        "sqlite_compound_select_column_count",
        re.compile(
            r"^SELECTs to the left and right of (?:UNION|UNION ALL|"
            r"INTERSECT|EXCEPT) do not have the same number of result columns$",
            re.I,
        ),
    ),
    (
        "sqlite_cte_column_count",
        re.compile(r"^table .+ has \d+ values for \d+ columns$", re.I),
    ),
    (
        "sqlite_escape_width",
        re.compile(
            r"^ESCAPE expression must be a single character$",
            re.I,
        ),
    ),
    (
        "sqlite_window_argument",
        re.compile(
            r"^second argument to nth_value must be a positive integer$",
            re.I,
        ),
    ),
)


def classify_repair_eligibility(
    result: QueryExecutionResult,
) -> RepairEligibilityDecision:
    """Classify one guarded execution result using default-deny rules."""
    if result.is_success:
        return RepairEligibilityDecision(
            category=RepairEligibilityCategory.NOT_APPLICABLE,
            eligible=False,
            reason="查询已成功，不存在数据库错误修复资格",
            rule_id="execution_succeeded",
        )

    error_type = result.error_type
    if error_type is QueryExecutionErrorType.SAFETY:
        return RepairEligibilityDecision(
            category=RepairEligibilityCategory.SAFETY_FAILURE,
            eligible=False,
            reason="Day 8 安全失败必须直接返回",
            rule_id="day8_safety_failure",
        )
    if error_type is QueryExecutionErrorType.TIMEOUT:
        return RepairEligibilityDecision(
            category=RepairEligibilityCategory.RESOURCE_FAILURE,
            eligible=False,
            reason="查询超时属于资源失败，不允许模型反复试探",
            rule_id="sqlite_timeout",
        )
    if error_type is QueryExecutionErrorType.BINDING:
        return RepairEligibilityDecision(
            category=RepairEligibilityCategory.CONTRACT_FAILURE,
            eligible=False,
            reason="参数绑定失败表示参数合同或程序不变量被破坏",
            rule_id="parameter_binding_failure",
        )

    # SQL_SYNTAX and FIELD are eligible only if SQLite actually started.  In
    # normal Day 8 flow most such errors are rejected before execution.
    if error_type in {
        QueryExecutionErrorType.SQL_SYNTAX,
        QueryExecutionErrorType.FIELD,
    }:
        if result.execution_started:
            return RepairEligibilityDecision(
                category=RepairEligibilityCategory.REPAIRABLE_SQL_ERROR,
                eligible=True,
                reason="错误由 SQLite 在安全查询执行阶段返回",
                rule_id=f"sqlite_{error_type.value}_after_safety",
            )
        return RepairEligibilityDecision(
            category=RepairEligibilityCategory.SAFETY_FAILURE,
            eligible=False,
            reason="错误未进入 SQLite，不得绕过执行前安全门进行修复",
            rule_id=f"pre_execution_{error_type.value}",
        )

    message = (result.error_message or "").strip()
    if error_type is QueryExecutionErrorType.DATABASE:
        if not result.execution_started:
            return RepairEligibilityDecision(
                category=RepairEligibilityCategory.ENVIRONMENT_ERROR,
                eligible=False,
                reason="数据库在 SQL 执行前不可用",
                rule_id="database_unavailable_before_execution",
            )
        for rule_id, pattern in _REPAIRABLE_SQL_ERROR_RULES:
            if pattern.fullmatch(message):
                return RepairEligibilityDecision(
                    category=(
                        RepairEligibilityCategory.REPAIRABLE_SQL_ERROR
                    ),
                    eligible=True,
                    reason=(
                        "错误属于已验证可达的 SQLite SQL 结构错误，"
                        "后续修复仍须保持原计划范围"
                    ),
                    rule_id=rule_id,
                )
        return RepairEligibilityDecision(
            category=(
                RepairEligibilityCategory.UNCLASSIFIED_DATABASE_ERROR
            ),
            eligible=False,
            reason="数据库错误未命中窄白名单，默认不得调用修复模型",
            rule_id="database_error_default_deny",
        )

    return RepairEligibilityDecision(
        category=RepairEligibilityCategory.UNCLASSIFIED_DATABASE_ERROR,
        eligible=False,
        reason="未知执行错误默认不得调用修复模型",
        rule_id="unknown_execution_error_default_deny",
    )
