"""Assistant-authored mechanical tests for SQL repair structured traces."""

import json

import pytest

from src.ecommerce_agent.query_trace import (
    ModelCallTrace,
    RepairModelEvent,
    RunFinalStatus,
    RunTraceRecorder,
    SqlAttemptTrace,
    TraceLogLevel,
    write_run_trace,
)


def _attempt(
    number: int,
    *,
    repair_attempt: int | None = None,
    source_sql_attempt: int | None = None,
    model: ModelCallTrace | None = None,
    entered: bool = True,
) -> SqlAttemptTrace:
    return SqlAttemptTrace(
        sql_attempt=number,
        repair_attempt=repair_attempt,
        source_sql_attempt=source_sql_attempt,
        trigger="initial_generation" if number == 1 else "repairable_sql_error",
        candidate_sql="SELECT order_id FROM fact_orders",
        normalized_sql="SELECT order_id FROM fact_orders",
        sql_hash="a" * 64,
        parameters={},
        repair_context_source=(
            None if number == 1 else "sanitized_sqlite_error"
        ),
        safety_trace={"accepted": True},
        execution_started=entered,
        execution_latency_ms=2.5 if entered else None,
        execution_status="succeeded" if entered else "safety_rejected",
        database_error_category=None,
        database_error_message=None,
        model=model,
        result_summary={"row_count": 1} if entered else None,
        log_level=TraceLogLevel.INFO,
    )


def test_same_run_keeps_continuous_attempts_and_null_fake_usage(tmp_path):
    recorder = RunTraceRecorder(
        response_source="fake_model_response",
        run_id="R1",
    )
    recorder.add_attempt(_attempt(1))
    recorder.add_repair_model_event(
        RepairModelEvent(
            repair_attempt=1,
            response_source="fake_model_response",
            model_name="fake-repair-repair",
            status="response_received",
            error_type=None,
            http_attempts=3,
        )
    )
    recorder.add_attempt(
        _attempt(
            2,
            repair_attempt=1,
            source_sql_attempt=1,
            model=ModelCallTrace(
                response_source="fake_model_response",
                model_name="fake-repair-repair",
                http_attempts=3,
                prompt_tokens=None,
                completion_tokens=None,
                latency_ms=None,
                finish_reason=None,
            ),
        )
    )
    trace = recorder.finish(
        final_status=RunFinalStatus.SUCCEEDED,
        stop_reason="repair_succeeded",
    )
    path = tmp_path / "trace.json"

    write_run_trace(path, trace)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["run_id"] == "R1"
    assert [row["sql_attempt"] for row in payload["attempts"]] == [1, 2]
    assert payload["attempts"][1]["repair_attempt"] == 1
    assert payload["attempts"][1]["model"]["http_attempts"] == 3
    assert payload["attempts"][1]["model"]["prompt_tokens"] is None
    assert payload["attempts"][1]["model"]["latency_ms"] is None
    assert payload["repair_model_events"][0]["repair_attempt"] == 1
    assert payload["repair_model_events"][0]["http_attempts"] == 3
    assert payload["stop_reason"] == "repair_succeeded"


def test_database_error_message_removes_local_path_and_newlines():
    attempt = SqlAttemptTrace(
        sql_attempt=1,
        repair_attempt=None,
        source_sql_attempt=None,
        trigger="initial_generation",
        candidate_sql="SELECT 1",
        normalized_sql="SELECT 1",
        sql_hash="b" * 64,
        parameters={},
        repair_context_source=None,
        safety_trace={"accepted": True},
        execution_started=False,
        execution_latency_ms=None,
        execution_status="failed",
        database_error_category="environment_error",
        database_error_message=(
            "unable to open C:\\Users\\person\\private\\olist.sqlite3\nfailed"
        ),
        model=None,
        result_summary=None,
        log_level=TraceLogLevel.ERROR,
    )

    assert "C:\\Users" not in attempt.database_error_message
    assert "<database-path>" in attempt.database_error_message
    assert "\n" not in attempt.database_error_message


def test_recorder_rejects_reset_or_skipped_attempt_numbers():
    recorder = RunTraceRecorder(response_source="fake", run_id="R1")
    recorder.add_attempt(_attempt(1))

    with pytest.raises(ValueError, match="当前应为 2"):
        recorder.add_attempt(_attempt(1))


def test_repair_attempt_must_reference_an_earlier_sql_attempt():
    with pytest.raises(ValueError, match="引用更早"):
        _attempt(
            2,
            repair_attempt=1,
            source_sql_attempt=2,
        )


def test_finished_trace_cannot_be_mutated():
    recorder = RunTraceRecorder(response_source="fake", run_id="R1")
    recorder.add_attempt(_attempt(1))
    recorder.finish(
        final_status=RunFinalStatus.SUCCEEDED,
        stop_reason="first_attempt_succeeded",
    )

    with pytest.raises(RuntimeError, match="已结束"):
        recorder.add_attempt(
            _attempt(2, repair_attempt=1, source_sql_attempt=1)
        )


def test_model_failure_without_sql_candidate_is_still_traceable():
    recorder = RunTraceRecorder(response_source="fake", run_id="R1")
    recorder.add_attempt(_attempt(1))
    recorder.add_repair_model_event(
        RepairModelEvent(
            repair_attempt=1,
            response_source="fake",
            model_name="fake-repair-repair",
            status="failed",
            error_type="transient_model_client_error",
            http_attempts=3,
        )
    )

    trace = recorder.finish(
        final_status=RunFinalStatus.FAILED,
        stop_reason="transient_model_client_error",
    )

    assert len(trace.attempts) == 1
    assert trace.repair_model_events[0].http_attempts == 3
