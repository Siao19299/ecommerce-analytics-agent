"""Structured, sanitized SQL repair run and SQL-attempt traces."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


class TraceLogLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RunFinalStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_WINDOWS_PATH = re.compile(
    r"(?i)(?:[a-z]:\\|[a-z]:/)[^\s,;]+"
)
_FILE_URI = re.compile(r"(?i)file:[^\s,;]+")


def sanitize_error_message(message: str | None) -> str | None:
    """Remove local absolute paths and bound one-line error text."""
    if message is None:
        return None
    sanitized = " ".join(str(message).split())
    sanitized = _FILE_URI.sub("<database-path>", sanitized)
    sanitized = _WINDOWS_PATH.sub("<database-path>", sanitized)
    return sanitized[:500]


@dataclass(frozen=True)
class ModelCallTrace:
    response_source: str
    model_name: str
    http_attempts: int | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float | None = None
    finish_reason: str | None = None
    cost: float | None = None
    cost_currency: str | None = None

    def __post_init__(self) -> None:
        if self.http_attempts is not None and self.http_attempts < 1:
            raise ValueError("http_attempts 必须至少为 1")


@dataclass(frozen=True)
class RepairModelEvent:
    repair_attempt: int
    response_source: str
    model_name: str
    status: str
    error_type: str | None
    http_attempts: int | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float | None = None
    finish_reason: str | None = None
    cost: float | None = None
    cost_currency: str | None = None

    def __post_init__(self) -> None:
        if self.repair_attempt < 1:
            raise ValueError("repair_attempt 必须是正整数")
        if self.http_attempts is not None and self.http_attempts < 1:
            raise ValueError("http_attempts 必须至少为 1")


@dataclass(frozen=True)
class SqlAttemptTrace:
    sql_attempt: int
    repair_attempt: int | None
    trigger: str
    candidate_sql: str
    normalized_sql: str | None
    sql_hash: str | None
    parameters: dict[str, Any]
    repair_context_source: str | None
    safety_trace: dict[str, Any] | None
    execution_started: bool
    execution_latency_ms: float | None
    execution_status: str
    database_error_category: str | None
    database_error_message: str | None
    model: ModelCallTrace | None
    result_summary: dict[str, Any] | None
    log_level: TraceLogLevel
    source_sql_attempt: int | None = None

    def __post_init__(self) -> None:
        if self.sql_attempt < 1:
            raise ValueError("sql_attempt 必须是正整数")
        if self.sql_attempt == 1:
            if self.repair_attempt is not None:
                raise ValueError("首次 SQL 不得带 repair_attempt")
            if self.source_sql_attempt is not None:
                raise ValueError("首次 SQL 不得引用来源 SQL")
        else:
            if self.repair_attempt is None or self.repair_attempt < 1:
                raise ValueError("修复候选必须带正整数 repair_attempt")
            if (
                self.source_sql_attempt is None
                or self.source_sql_attempt < 1
                or self.source_sql_attempt >= self.sql_attempt
            ):
                raise ValueError("修复候选必须引用更早的 SQL attempt")
        object.__setattr__(
            self,
            "database_error_message",
            sanitize_error_message(self.database_error_message),
        )


@dataclass(frozen=True)
class RepairRunTrace:
    run_id: str
    created_at: datetime
    response_source: str
    attempts: tuple[SqlAttemptTrace, ...]
    repair_model_events: tuple[RepairModelEvent, ...]
    final_status: RunFinalStatus
    stop_reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["final_status"] = self.final_status.value
        for attempt in payload["attempts"]:
            attempt["log_level"] = attempt["log_level"].value
        return payload


@dataclass
class RunTraceRecorder:
    """Build one immutable trace while enforcing one run and ordered attempts."""

    response_source: str
    run_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    _attempts: list[SqlAttemptTrace] = field(default_factory=list)
    _repair_model_events: list[RepairModelEvent] = field(
        default_factory=list
    )
    _finished: bool = False

    def add_attempt(self, attempt: SqlAttemptTrace) -> None:
        if self._finished:
            raise RuntimeError("run trace 已结束")
        expected = len(self._attempts) + 1
        if attempt.sql_attempt != expected:
            raise ValueError(
                f"sql_attempt 必须连续递增，当前应为 {expected}"
            )
        self._attempts.append(attempt)

    def add_repair_model_event(self, event: RepairModelEvent) -> None:
        if self._finished:
            raise RuntimeError("run trace 已结束")
        expected = len(self._repair_model_events) + 1
        if event.repair_attempt != expected:
            raise ValueError(
                f"repair_attempt 必须连续递增，当前应为 {expected}"
            )
        self._repair_model_events.append(event)

    def finish(
        self,
        *,
        final_status: RunFinalStatus,
        stop_reason: str,
    ) -> RepairRunTrace:
        if self._finished:
            raise RuntimeError("run trace 已结束")
        if not self._attempts:
            raise RuntimeError("run trace 至少需要一条 SQL attempt")
        if not stop_reason.strip():
            raise ValueError("stop_reason 不能为空")
        self._finished = True
        return RepairRunTrace(
            run_id=self.run_id,
            created_at=self.created_at,
            response_source=self.response_source,
            attempts=tuple(self._attempts),
            repair_model_events=tuple(self._repair_model_events),
            final_status=final_status,
            stop_reason=stop_reason,
        )


def write_run_trace(path: str | Path, trace: RepairRunTrace) -> None:
    """Persist one complete run as readable UTF-8 JSON."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(trace.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
