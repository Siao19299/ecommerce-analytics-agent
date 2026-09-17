"""Fully offline Day 13 UI/API acceptance with honest evidence scopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from src.ecommerce_agent.day11_benchmark import (
    MONTHLY_SQL,
    QUESTION,
    _july_only_sql,
    _machine,
)
from src.ecommerce_agent.day12_api import create_app
from src.ecommerce_agent.day12_api_models import (
    AnalyzeErrorResponse,
    ApiErrorDetail,
    PublicFailureStatus,
    RunMetadata,
)
from src.ecommerce_agent.day12_service import Day11AgentService
from src.ecommerce_agent.day13_api_client import (
    ApiCallResult,
    ApiClientFailure,
    ApiResponse,
    ClientFailureKind,
    Day13ApiClient,
)
from src.ecommerce_agent.day13_streamlit import API_CLIENT_KEY, PAGE_STATE_KEY


ZERO_BASELINE_SQL = MONTHLY_SQL.replace(
    "SUM(i.price) AS monthly_gmv",
    "SUM(CASE WHEN substr(o.order_purchase_timestamp, 1, 7) = "
    "'2018-06' THEN 0 ELSE i.price END) AS monthly_gmv",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raw_hashes(root: Path) -> dict[str, str]:
    return {
        path.name: _sha256(path)
        for path in sorted((root / "data/raw").iterdir())
        if path.is_file() and path.name != ".gitkeep"
    }


class LocalTestTransport:
    """Adapt TestClient without pretending its in-process call has a timeout."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def post(self, url: str, **kwargs: Any):
        kwargs.pop("timeout", None)
        return self.client.post(url, **kwargs)


@dataclass
class FixedClient:
    result: ApiCallResult
    call_count: int = 0

    def analyze(self, question: str) -> ApiCallResult:
        self.call_count += 1
        return self.result


def _visible_text(app: AppTest) -> str:
    collections = (
        app.markdown,
        app.caption,
        app.warning,
        app.error,
        app.info,
        app.success,
        app.code,
        app.metric,
    )
    values = []
    for collection in collections:
        for item in collection:
            value = getattr(item, "value", "")
            values.append(str(value))
    return " ".join(values)


def _render_case(
    root: Path,
    *,
    case_id: str,
    client: Any,
    question: str,
    expected_status: str,
    evidence_scope: str,
) -> dict[str, Any]:
    app = AppTest.from_file(root / "streamlit_app.py", default_timeout=30)
    app.session_state[API_CLIENT_KEY] = client
    app.run()
    app.text_area[0].input(question)
    app.button[0].click().run()

    page_state = app.session_state[PAGE_STATE_KEY]
    result = page_state.result
    if isinstance(result, ApiResponse):
        observed_status = result.payload.status.value if hasattr(
            result.payload.status, "value"
        ) else result.payload.status
        run_id = result.payload.run_id
        calculation_status = getattr(
            result.payload, "calculation_status", None
        )
    else:
        observed_status = result.kind.value
        run_id = None
        calculation_status = None
    visible = _visible_text(app)
    sensitive_absent = all(
        marker.lower() not in visible.lower()
        for marker in (
            "C:\\Users\\private",
            "TOP_SECRET",
            "RAW_PROMPT",
            "RAW_MODEL_RESPONSE",
            "Traceback",
        )
    )
    no_component_exception = len(app.exception) == 0
    return {
        "case_id": case_id,
        "evidence_scope": evidence_scope,
        "expected_status": expected_status,
        "observed_status": observed_status,
        "run_id": run_id,
        "calculation_status": calculation_status,
        "success_component_count": len(app.success),
        "info_component_count": len(app.info),
        "warning_component_count": len(app.warning),
        "error_component_count": len(app.error),
        "sql_component_count": len(app.code),
        "table_component_count": len(app.dataframe),
        "chart_component_count": len(app.get("arrow_vega_lite_chart")),
        "sensitive_markers_absent": sensitive_absent,
        "component_exception_absent": no_component_exception,
        "expectation_met": (
            observed_status == expected_status
            and sensitive_absent
            and no_component_exception
        ),
        "business_reference_status": "not_independently_evaluated",
    }


def _api_client(machine) -> Day13ApiClient:
    app = create_app(Day11AgentService(machine))
    return Day13ApiClient(LocalTestTransport(TestClient(app)))


def _metadata() -> RunMetadata:
    return RunMetadata.model_validate(
        {
            "created_at": "2026-09-17T00:00:00Z",
            "workflow_duration_ms": 0,
            "node_count": 0,
            "sql_attempt_count": 0,
            "repair_attempt_count": 0,
            "execution_started": False,
            "rows_truncated": False,
        }
    )


def _failure(status: PublicFailureStatus) -> ApiResponse:
    return ApiResponse(
        http_status=500,
        payload=AnalyzeErrorResponse(
            status=status,
            run_id=f"scripted-{status.value}",
            stop_reason=f"scripted-{status.value}",
            error=ApiErrorDetail(
                code=status.value,
                message=(
                    "C:\\Users\\private RAW_PROMPT RAW_MODEL_RESPONSE "
                    "API_KEY=TOP_SECRET Traceback"
                ),
                retryable=status in {
                    PublicFailureStatus.RESOURCE_FAILED,
                    PublicFailureStatus.ENVIRONMENT_FAILED,
                },
            ),
            metadata=_metadata(),
        ),
    )


def run_benchmark(root: Path) -> dict[str, Any]:
    database = root / "data/processed/olist.sqlite3"
    database_before = _sha256(database)
    raw_before = _raw_hashes(root)
    results = []

    workflow_cases = (
        (
            "D13_REAL_SQLITE_SUCCESS",
            _machine(root),
            QUESTION,
            "succeeded",
            "full_day11_fake_model_state_machine_via_day12_api_real_sqlite",
        ),
        (
            "D13_CLARIFICATION",
            _machine(
                root,
                planning_payload={
                    "status": "needs_clarification",
                    "clarification_question": "销售额具体指哪一种口径？",
                },
            ),
            "分析销售额。",
            "needs_clarification",
            "full_day11_fake_model_state_machine_stopped_before_sqlite",
        ),
        (
            "D13_SAFETY_REJECTION",
            _machine(root, sql="DELETE FROM fact_orders"),
            QUESTION,
            "safety_rejected",
            "full_day11_fake_model_state_machine_stopped_before_sqlite",
        ),
        (
            "D13_MISSING_COMPARISON",
            _machine(root, sql=_july_only_sql()),
            QUESTION,
            "succeeded",
            "full_day11_fake_model_state_machine_via_day12_api_real_sqlite",
        ),
        (
            "D13_ZERO_BASELINE",
            _machine(root, sql=ZERO_BASELINE_SQL),
            QUESTION,
            "succeeded",
            "full_day11_fake_model_state_machine_via_day12_api_real_sqlite",
        ),
    )
    for case_id, machine, question, status, scope in workflow_cases:
        results.append(
            _render_case(
                root,
                case_id=case_id,
                client=_api_client(machine),
                question=question,
                expected_status=status,
                evidence_scope=scope,
            )
        )

    for status in (
        PublicFailureStatus.RESOURCE_FAILED,
        PublicFailureStatus.ENVIRONMENT_FAILED,
        PublicFailureStatus.REPAIR_LIMIT_REACHED,
        PublicFailureStatus.CALCULATION_FAILED,
        PublicFailureStatus.INTERNAL_FAILED,
    ):
        results.append(
            _render_case(
                root,
                case_id=f"D13_{status.value.upper()}",
                client=FixedClient(_failure(status)),
                question="分析经营数据。",
                expected_status=status.value,
                evidence_scope="scripted_public_api_response_fake_client",
            )
        )

    for kind, message, retryable in (
        (ClientFailureKind.TIMEOUT, "分析请求超时，请稍后重试。", True),
        (ClientFailureKind.NETWORK, "暂时无法连接分析服务。", True),
        (ClientFailureKind.NON_JSON, "分析服务返回了无法识别的响应。", False),
        (ClientFailureKind.CONTRACT, "分析服务响应与页面不兼容。", False),
    ):
        results.append(
            _render_case(
                root,
                case_id=f"D13_CLIENT_{kind.value.upper()}",
                client=FixedClient(
                    ApiClientFailure(
                        kind=kind,
                        public_message=message,
                        retryable=retryable,
                    )
                ),
                question="分析经营数据。",
                expected_status=kind.value,
                evidence_scope="scripted_sanitized_client_failure",
            )
        )

    database_after = _sha256(database)
    raw_after = _raw_hashes(root)
    return {
        "measurement_scope": (
            "assistant_authored_mechanical_streamlit_apptest_cases; "
            "in_process_day12_fastapi_transport; fake_model_responses; "
            "real_local_sqlite_where_stated; no_independent_business_accuracy"
        ),
        "external_api_calls": 0,
        "model_generated_numeric_results": 0,
        "case_count": len(results),
        "expectation_met_count": sum(
            item["expectation_met"] for item in results
        ),
        "streamlit_apptest_case_count": len(results),
        "day12_api_integration_case_count": len(workflow_cases),
        "full_state_machine_case_count": len(workflow_cases),
        "real_sqlite_execution_case_count": sum(
            "real_sqlite" in item["evidence_scope"] for item in results
        ),
        "scripted_public_response_case_count": 5,
        "scripted_client_failure_case_count": 4,
        "independent_user_completion": False,
        "independent_business_reference": False,
        "database_sha256_before": database_before,
        "database_sha256_after": database_after,
        "database_unchanged": database_before == database_after,
        "raw_file_hashes_before": raw_before,
        "raw_file_hashes_after": raw_after,
        "raw_files_unchanged": raw_before == raw_after,
        "cases": results,
    }


def main() -> None:
    root = Path(__file__).parents[2]
    report = run_benchmark(root)
    for destination in (
        root / "data/processed/day13/offline_ui_benchmark.json",
        root / "docs/DAY13_RESULTS.json",
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
