"""HTTP tests for sanitized validation and unhandled exception responses."""

from fastapi.testclient import TestClient

from src.ecommerce_agent.workflow_state import WorkflowState, WorkflowStatus
from src.ecommerce_agent.api import create_app


class ExplodingService:
    def analyze(self, question: str, *, run_id: str | None = None):
        raise RuntimeError(
            "C:\\Users\\private\\olist.sqlite3 SECRET_PROMPT API_KEY_VALUE"
        )


class InconsistentRunIdService:
    def analyze(self, question: str, *, run_id: str | None = None):
        return WorkflowState(
            run_id="different-run-id",
            question=question,
            status=WorkflowStatus.SAFETY_REJECTED,
            stop_reason="safety_failure",
        )


class IncompleteSuccessService:
    def analyze(self, question: str, *, run_id: str | None = None):
        return WorkflowState(
            run_id=run_id or "missing-run-id",
            question=question,
            status=WorkflowStatus.SUCCEEDED,
            stop_reason="completed",
        )


def test_unhandled_exception_is_stable_and_does_not_leak_raw_details():
    client = TestClient(
        create_app(ExplodingService()),
        raise_server_exceptions=False,
    )

    response = client.post("/analyze", json={"question": "分析订单量。"})
    payload = response.json()

    assert response.status_code == 500
    assert payload["status"] == "internal_failed"
    assert payload["stop_reason"] == "unhandled_exception"
    assert payload["error"] == {
        "code": "internal_failed",
        "message": "分析服务发生内部错误。",
        "retryable": False,
    }
    assert len(payload["run_id"]) == 32
    assert payload["metadata"]["execution_started"] is False
    assert "C:\\Users" not in response.text
    assert "SECRET_PROMPT" not in response.text
    assert "API_KEY_VALUE" not in response.text
    assert "RuntimeError" not in response.text
    assert "traceback" not in response.text.lower()


def test_run_id_or_success_contract_violation_becomes_sanitized_500():
    for service in (InconsistentRunIdService(), IncompleteSuccessService()):
        response = TestClient(
            create_app(service),
            raise_server_exceptions=False,
        ).post("/analyze", json={"question": "分析订单量。"})

        assert response.status_code == 500
        assert response.json()["status"] == "internal_failed"
        assert response.json()["error"]["code"] == "internal_failed"
        assert "run_id" not in response.json()["error"]["message"]
        assert "合同" not in response.json()["error"]["message"]


def test_validation_errors_do_not_echo_input_or_raw_json():
    client = TestClient(create_app(ExplodingService()))
    cases = (
        ({"question": "   "}, None),
        (
            {
                "question": "分析订单量。",
                "skip_safety": "TOP_SECRET_VALUE",
            },
            None,
        ),
        ({"question": "问" * 2001}, None),
    )
    for body, _ in cases:
        response = client.post("/analyze", json=body)
        payload = response.json()

        assert response.status_code == 422
        assert payload["status"] == "invalid_request"
        assert payload["error"]["code"] == "invalid_request"
        assert len(payload["run_id"]) == 32
        assert "TOP_SECRET_VALUE" not in response.text
        assert "问" * 20 not in response.text
        assert all(set(issue) == {"location", "error_type"} for issue in payload["issues"])

    malformed = client.post(
        "/analyze",
        content=b'{"question":"RAW_BROKEN_JSON_SECRET"',
        headers={"content-type": "application/json"},
    )
    assert malformed.status_code == 422
    assert malformed.json()["status"] == "invalid_request"
    assert "RAW_BROKEN_JSON_SECRET" not in malformed.text
