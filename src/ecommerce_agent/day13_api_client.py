"""Replaceable, sanitized client boundary for the Day 13 UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeAlias

import httpx
from pydantic import TypeAdapter, ValidationError

from src.ecommerce_agent.day12_api_models import (
    AnalyzeResponse,
    RequestValidationErrorResponse,
)


ApiPayload: TypeAlias = AnalyzeResponse | RequestValidationErrorResponse
_PAYLOAD_ADAPTER = TypeAdapter(ApiPayload)


class ClientFailureKind(str, Enum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    NON_JSON = "non_json_response"
    CONTRACT = "contract_mismatch"


@dataclass(frozen=True)
class ApiResponse:
    http_status: int
    payload: ApiPayload


@dataclass(frozen=True)
class ApiClientFailure:
    kind: ClientFailureKind
    public_message: str
    retryable: bool


ApiCallResult: TypeAlias = ApiResponse | ApiClientFailure


class AnalysisApiClient(Protocol):
    def analyze(self, question: str) -> ApiCallResult: ...


class HttpTransport(Protocol):
    def post(self, url: str, **kwargs: object): ...


_FAILURES = {
    ClientFailureKind.TIMEOUT: ApiClientFailure(
        kind=ClientFailureKind.TIMEOUT,
        public_message="分析请求超时，请稍后重试。",
        retryable=True,
    ),
    ClientFailureKind.NETWORK: ApiClientFailure(
        kind=ClientFailureKind.NETWORK,
        public_message="暂时无法连接分析服务，请确认服务可用后重试。",
        retryable=True,
    ),
    ClientFailureKind.NON_JSON: ApiClientFailure(
        kind=ClientFailureKind.NON_JSON,
        public_message="分析服务返回了无法识别的响应。",
        retryable=False,
    ),
    ClientFailureKind.CONTRACT: ApiClientFailure(
        kind=ClientFailureKind.CONTRACT,
        public_message="分析服务响应与当前页面版本不兼容。",
        retryable=False,
    ),
}


@dataclass(frozen=True)
class Day13ApiClient:
    transport: HttpTransport
    timeout_seconds: float = 90.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")

    def analyze(self, question: str) -> ApiCallResult:
        try:
            response = self.transport.post(
                "/analyze",
                json={"question": question},
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException:
            return _FAILURES[ClientFailureKind.TIMEOUT]
        except httpx.RequestError:
            return _FAILURES[ClientFailureKind.NETWORK]
        except Exception:
            return _FAILURES[ClientFailureKind.NETWORK]

        try:
            raw_payload = response.json()
        except Exception:
            return _FAILURES[ClientFailureKind.NON_JSON]
        try:
            payload = _PAYLOAD_ADAPTER.validate_python(raw_payload)
        except (ValidationError, TypeError, ValueError):
            return _FAILURES[ClientFailureKind.CONTRACT]
        return ApiResponse(http_status=response.status_code, payload=payload)


def create_http_api_client(
    base_url: str = "http://127.0.0.1:8000",
    *,
    timeout_seconds: float = 90.0,
) -> Day13ApiClient:
    """Create the production HTTP client without probing the API."""
    transport = httpx.Client(base_url=base_url)
    return Day13ApiClient(
        transport=transport,
        timeout_seconds=timeout_seconds,
    )
