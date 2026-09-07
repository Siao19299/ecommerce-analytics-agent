import json
from datetime import datetime, timezone

from src.ecommerce_agent.analysis_planner import PlanningLogRecord
from src.ecommerce_agent.planning_log import append_planning_logs


def test_planning_logs_are_appended_as_sanitized_json_lines(tmp_path):
    log_path = tmp_path / "planning.jsonl"
    record = PlanningLogRecord(
        run_id="synthetic-run-id",
        created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        model_name="fake-test-model",
        output_attempt=1,
        status="failed",
        latency_ms=None,
        prompt_tokens=None,
        completion_tokens=None,
        error_type="non_json",
    )

    append_planning_logs(log_path, [record])
    append_planning_logs(log_path, [record])

    lines = log_path.read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines]

    assert len(payloads) == 2
    assert payloads[0]["run_id"] == "synthetic-run-id"
    assert payloads[0]["created_at"] == "2026-09-07T00:00:00+00:00"
    assert payloads[0]["error_type"] == "non_json"
    assert "question" not in payloads[0]
    assert "content" not in payloads[0]
    assert "api_key" not in payloads[0]
