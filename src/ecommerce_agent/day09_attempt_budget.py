"""Separate Day 9 SQL repair, SQL candidate, and HTTP attempt counts."""

from __future__ import annotations

from dataclasses import dataclass


class RepairLimitReached(RuntimeError):
    """Raised before starting a repair beyond the configured hard limit."""


@dataclass(frozen=True)
class RepairLimits:
    """Hard SQL repair limit; initial SQL is not itself a repair."""

    max_repair_attempts: int = 2

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_repair_attempts, bool)
            or not isinstance(self.max_repair_attempts, int)
            or self.max_repair_attempts < 0
        ):
            raise ValueError("max_repair_attempts 必须是非负整数")

    @property
    def max_total_sql_attempts(self) -> int:
        """Initial SQL plus at most one candidate per repair round."""
        return 1 + self.max_repair_attempts


@dataclass(frozen=True)
class AttemptSnapshot:
    repair_attempts: int
    sql_attempts: int
    model_http_attempts: int
    sqlite_entries: int
    repair_candidate_pending: bool


@dataclass
class AttemptCounter:
    """Count logical repair rounds independently from transport attempts.

    A returned repair candidate counts as a SQL attempt even when the Day 8
    safety gate rejects it before SQLite.  HTTP attempts never increment SQL or
    repair counts by themselves.
    """

    limits: RepairLimits
    _repair_attempts: int = 0
    _sql_attempts: int = 0
    _model_http_attempts: int = 0
    _sqlite_entries: int = 0
    _repair_candidate_pending: bool = False

    def record_initial_sql(self, *, entered_sqlite: bool) -> int:
        if self._sql_attempts != 0 or self._repair_attempts != 0:
            raise RuntimeError("首次 SQL 只能记录一次且必须先记录")
        self._sql_attempts = 1
        if entered_sqlite:
            self._sqlite_entries += 1
        return self._sql_attempts

    def begin_repair(self) -> int:
        if self._sql_attempts == 0:
            raise RuntimeError("开始修复前必须先记录首次 SQL")
        if self._repair_candidate_pending:
            raise RuntimeError("当前修复轮次尚未记录候选 SQL")
        if self._repair_attempts >= self.limits.max_repair_attempts:
            raise RepairLimitReached("已达到最大 SQL 修复次数")
        self._repair_attempts += 1
        self._repair_candidate_pending = True
        return self._repair_attempts

    def record_model_http_attempt(self) -> int:
        if not self._repair_candidate_pending:
            raise RuntimeError("模型 HTTP 尝试必须属于一个修复轮次")
        self._model_http_attempts += 1
        return self._model_http_attempts

    def record_repair_candidate(self, *, entered_sqlite: bool) -> int:
        if not self._repair_candidate_pending:
            raise RuntimeError("没有等待记录的修复候选")
        self._sql_attempts += 1
        self._repair_candidate_pending = False
        if entered_sqlite:
            self._sqlite_entries += 1
        return self._sql_attempts

    def end_repair_without_candidate(self) -> None:
        """Close a repair round whose transport failed without a response."""
        if not self._repair_candidate_pending:
            raise RuntimeError("没有进行中的修复轮次")
        self._repair_candidate_pending = False

    def record_sqlite_entry(self) -> int:
        """Record that an already counted SQL candidate entered SQLite."""
        if self._sql_attempts == 0:
            raise RuntimeError("进入 SQLite 前必须先记录 SQL candidate")
        self._sqlite_entries += 1
        return self._sqlite_entries

    def snapshot(self) -> AttemptSnapshot:
        return AttemptSnapshot(
            repair_attempts=self._repair_attempts,
            sql_attempts=self._sql_attempts,
            model_http_attempts=self._model_http_attempts,
            sqlite_entries=self._sqlite_entries,
            repair_candidate_pending=self._repair_candidate_pending,
        )
