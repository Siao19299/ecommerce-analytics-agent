"""Tests for the replaceable and sanitized Day 13 API client."""

import httpx
import pytest

from src.ecommerce_agent.day12_api_models import (
    AnalyzeClarificationResponse,
    AnalyzeErrorResponse,
    PublicFailureStatus,
)
from src.ecommerce_agent.day13_api_client import (
    ApiClientFailure,
    ApiResponse,
    ClientFailureKind,
    Day13ApiClient,
)


class ResponseStub:
    def __init__(self, status_code=200, payload=None, error=None):
        self.status_code = status_code
        self.payload = payload
        self.error = error

    def json(self):
        if self.error is not None:
            raise self.error
        return self.payload


class TransportStub:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def _metadata():
    return {
        "created_at": "2026-09-17T00:00:00Z",
        "workflow_duration_ms": 1,
        "node_count": 2,
        "sql_attempt_count": 0,
        "repair_attempt_count": 0,
        "execution_started": False,
        "rows_truncated": False,
    }


def test_client_calls_analyze_once_and_validates_clarification_contract():
    transport = TransportStub(
        ResponseStub(
            payload={
                "status": "needs_clarification",
                "run_id": "run-clarify",
                "clarification_question": "销售额具体指哪一种口径？",
                "sql": None,
                "stop_reason": "clarification_required",
                "metadata": _metadata(),
            }
        )
    )

    result = Day13ApiClient(transport, timeout_seconds=7).analyze("分析销售额。")

    assert isinstance(result, ApiResponse)
    assert isinstance(result.payload, AnalyzeClarificationResponse)
    assert transport.calls == [
        (
            "/analyze",
            {"json": {"question": "分析销售额。"}, "timeout": 7},
        )
    ]


def test_valid_http_failure_body_remains_a_typed_workflow_failure():
    transport = TransportStub(
        ResponseStub(
            status_code=503,
            payload={
                "status": "resource_failed",
                "run_id": "run-resource",
                "stop_reason": "resource_failure",
                "error": {
                    "code": "resource_failed",
                    "message": "分析受到当前资源限制，未完成执行。",
                    "retryable": True,
                },
                "metadata": _metadata(),
            },
        )
    )

    result = Day13ApiClient(transport).analyze("分析订单量。")

    assert isinstance(result, ApiResponse)
    assert result.http_status == 503
    assert isinstance(result.payload, AnalyzeErrorResponse)
    assert result.payload.status is PublicFailureStatus.RESOURCE_FAILED


@pytest.mark.parametrize(
    "error,expected_kind",
    [
        (
            httpx.ReadTimeout(
                "C:\\Users\\private SECRET_KEY",
                request=httpx.Request("POST", "http://test/analyze"),
            ),
            ClientFailureKind.TIMEOUT,
        ),
        (
            httpx.ConnectError(
                "C:\\Users\\private SECRET_KEY",
                request=httpx.Request("POST", "http://test/analyze"),
            ),
            ClientFailureKind.NETWORK,
        ),
    ],
)
def test_transport_failures_are_stable_and_do_not_expose_exception(error, expected_kind):
    result = Day13ApiClient(TransportStub(error=error)).analyze("问题")

    assert isinstance(result, ApiClientFailure)
    assert result.kind is expected_kind
    assert "C:\\Users" not in result.public_message
    assert "SECRET_KEY" not in result.public_message


def test_non_json_and_contract_mismatch_have_distinct_sanitized_results():
    non_json = Day13ApiClient(
        TransportStub(ResponseStub(error=ValueError("RAW_MODEL_RESPONSE")))
    ).analyze("问题")
    mismatch = Day13ApiClient(
        TransportStub(
            ResponseStub(payload={"status": "succeeded", "prompt": "SECRET"})
        )
    ).analyze("问题")

    assert isinstance(non_json, ApiClientFailure)
    assert non_json.kind is ClientFailureKind.NON_JSON
    assert isinstance(mismatch, ApiClientFailure)
    assert mismatch.kind is ClientFailureKind.CONTRACT
    assert "RAW_MODEL_RESPONSE" not in non_json.public_message
    assert "SECRET" not in mismatch.public_message


def test_timeout_must_be_positive():
    with pytest.raises(ValueError, match="timeout_seconds"):
        Day13ApiClient(TransportStub(), timeout_seconds=0)
