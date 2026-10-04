"""Offline API service HTTP acceptance with explicitly separated evidence scopes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from src.ecommerce_agent.workflow_benchmark import (
    MONTHLY_SQL,
    QUESTION,
    REPAIRABLE_SQL,
    _controlled_failure_analyzer,
    _july_only_sql,
    _machine,
)
from src.ecommerce_agent.workflow_state import WorkflowState, WorkflowStatus
from src.ecommerce_agent.workflow import AgentStateMachine
from src.ecommerce_agent.api import create_app
from src.ecommerce_agent.analysis_service import AgentService


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


class StaticStateService:
    def __init__(self, status: WorkflowStatus, stop_reason: str) -> None:
        self.status = status
        self.stop_reason = stop_reason

    def analyze(self, question: str, *, run_id: str | None = None):
        return WorkflowState(
            run_id=run_id or "benchmark-run",
            question=question,
            status=self.status,
            stop_reason=self.stop_reason,
        )


class ExplodingService:
    def analyze(self, question: str, *, run_id: str | None = None):
        raise RuntimeError(
            "C:\\Users\\private\\olist.sqlite3 SECRET_PROMPT API_KEY_VALUE"
        )


def _environment_machine(root: Path) -> AgentStateMachine:
    machine = _machine(root)
    missing = root / "data/processed/api/missing.sqlite3"
    repair_workflow = replace(
        machine.services.repair_workflow,
        database_path=missing,
    )
    return AgentStateMachine(
        replace(
            machine.services,
            database_path=missing,
            repair_workflow=repair_workflow,
        ),
        max_node_steps=machine.max_node_steps,
    )


def _http_case(
    *,
    case_id: str,
    client: TestClient,
    request_kwargs: dict[str, Any],
    expected_http: int,
    expected_status: str,
    evidence_scope: str,
    business_reference_status: str,
    expected_calculation_status: str | None = None,
    expected_sql_attempts: int | None = None,
) -> dict[str, Any]:
    response = client.post("/analyze", **request_kwargs)
    payload = response.json()
    expectation_met = (
        response.status_code == expected_http
        and payload.get("status") == expected_status
    )
    if expected_calculation_status is not None:
        expectation_met = expectation_met and (
            payload.get("calculation_status")
            == expected_calculation_status
        )
    if expected_sql_attempts is not None:
        expectation_met = expectation_met and (
            payload.get("metadata", {}).get("sql_attempt_count")
            == expected_sql_attempts
        )
    serialized = response.text
    sensitive_absent = all(
        marker not in serialized
        for marker in (
            "C:\\Users",
            "SECRET_PROMPT",
            "API_KEY_VALUE",
            "sql_generation_context",
            "normalized_sql",
            "model_response",
        )
    )
    return {
        "case_id": case_id,
        "evidence_scope": evidence_scope,
        "http_transport": "fastapi_testclient_in_process_asgi",
        "expected_http_status": expected_http,
        "observed_http_status": response.status_code,
        "expected_workflow_status": expected_status,
        "observed_workflow_status": payload.get("status"),
        "calculation_status": payload.get("calculation_status"),
        "run_id": payload.get("run_id"),
        "sql_attempt_count": payload.get("metadata", {}).get(
            "sql_attempt_count"
        ),
        "repair_attempt_count": payload.get("metadata", {}).get(
            "repair_attempt_count"
        ),
        "execution_started": payload.get("metadata", {}).get(
            "execution_started"
        ),
        "sensitive_markers_absent": sensitive_absent,
        "expectation_met": expectation_met and sensitive_absent,
        "business_reference_status": business_reference_status,
        "response": payload,
    }


def run_benchmark(root: Path) -> dict[str, Any]:
    database = root / "data/processed/olist.sqlite3"
    database_before = _sha256(database)
    raw_before = _raw_hashes(root)
    repeated_error = REPAIRABLE_SQL.replace(
        "o.customer_id",
        "o.order_status",
    )
    full_workflow_cases = (
        (
            "D12_NORMAL_SUCCESS",
            _machine(root),
            QUESTION,
            200,
            "succeeded",
            "computed",
            1,
        ),
        (
            "D12_CLARIFICATION",
            _machine(
                root,
                planning_payload={
                    "status": "needs_clarification",
                    "clarification_question": "销售额具体指哪一种口径？",
                },
            ),
            "分析销售额。",
            200,
            "needs_clarification",
            None,
            0,
        ),
        (
            "D12_SAFETY_REJECTION",
            _machine(root, sql="DELETE FROM fact_orders"),
            QUESTION,
            422,
            "safety_rejected",
            None,
            0,
        ),
        (
            "D12_REPAIR_SUCCESS",
            _machine(
                root,
                sql=REPAIRABLE_SQL,
                repair_sqls=(MONTHLY_SQL,),
            ),
            QUESTION,
            200,
            "succeeded",
            "computed",
            2,
        ),
        (
            "D12_REPAIR_LIMIT",
            _machine(
                root,
                sql=REPAIRABLE_SQL,
                repair_sqls=(repeated_error,),
                max_repairs=1,
            ),
            QUESTION,
            500,
            "repair_limit_reached",
            None,
            2,
        ),
        (
            "D12_MISSING_COMPARISON",
            _machine(root, sql=_july_only_sql()),
            QUESTION,
            200,
            "succeeded",
            "missing_comparison_period",
            1,
        ),
        (
            "D12_ZERO_BASELINE",
            _machine(root, sql=ZERO_BASELINE_SQL),
            QUESTION,
            200,
            "succeeded",
            "zero_baseline",
            1,
        ),
        (
            "D12_CALCULATION_FAILURE",
            _machine(root, analyzer=_controlled_failure_analyzer),
            QUESTION,
            500,
            "calculation_failed",
            None,
            1,
        ),
        (
            "D12_ENVIRONMENT_FAILURE",
            _environment_machine(root),
            QUESTION,
            503,
            "environment_failed",
            None,
            1,
        ),
    )
    results = []
    for (
        case_id,
        machine,
        question,
        http_status,
        workflow_status,
        calculation_status,
        sql_attempts,
    ) in full_workflow_cases:
        if case_id in {"D12_CLARIFICATION", "D12_SAFETY_REJECTION"}:
            evidence_scope = (
                "full_day11_fake_model_responses_stopped_before_sqlite"
            )
        elif case_id == "D12_ENVIRONMENT_FAILURE":
            evidence_scope = (
                "full_day11_fake_model_responses_missing_database_environment"
            )
        else:
            evidence_scope = (
                "full_day11_fake_model_responses_real_local_sqlite_execution"
            )
        results.append(
            _http_case(
                case_id=case_id,
                client=TestClient(
                    create_app(AgentService(machine)),
                    raise_server_exceptions=False,
                ),
                request_kwargs={"json": {"question": question}},
                expected_http=http_status,
                expected_status=workflow_status,
                expected_calculation_status=calculation_status,
                expected_sql_attempts=sql_attempts,
                evidence_scope=evidence_scope,
                business_reference_status=(
                    "not_independently_evaluated"
                ),
            )
        )

    for case_id, status, reason, http_status in (
        (
            "D12_RESOURCE_FAILURE_MAPPING",
            WorkflowStatus.RESOURCE_FAILED,
            "resource_failure",
            503,
        ),
        (
            "D12_UNKNOWN_EXECUTION_FAILURE_MAPPING",
            WorkflowStatus.EXECUTION_FAILED,
            "unclassified_database_error",
            500,
        ),
    ):
        results.append(
            _http_case(
                case_id=case_id,
                client=TestClient(
                    create_app(StaticStateService(status, reason))
                ),
                request_kwargs={"json": {"question": QUESTION}},
                expected_http=http_status,
                expected_status=status.value,
                evidence_scope="scripted_terminal_workflow_state_http_mapping",
                business_reference_status="not_applicable_api_contract",
            )
        )

    results.append(
        _http_case(
            case_id="D12_UNHANDLED_EXCEPTION",
            client=TestClient(
                create_app(ExplodingService()),
                raise_server_exceptions=False,
            ),
            request_kwargs={"json": {"question": QUESTION}},
            expected_http=500,
            expected_status="internal_failed",
            evidence_scope="scripted_unhandled_exception_http_boundary",
            business_reference_status="not_applicable_api_contract",
        )
    )

    validation_client = TestClient(create_app(ExplodingService()))
    for case_id, request_kwargs in (
        ("D12_BLANK_QUESTION", {"json": {"question": "   "}}),
        (
            "D12_EXTRA_FIELD",
            {"json": {"question": QUESTION, "skip_safety": True}},
        ),
        (
            "D12_MALFORMED_JSON",
            {
                "content": b'{"question":',
                "headers": {"content-type": "application/json"},
            },
        ),
    ):
        results.append(
            _http_case(
                case_id=case_id,
                client=validation_client,
                request_kwargs=request_kwargs,
                expected_http=422,
                expected_status="invalid_request",
                evidence_scope="fastapi_pydantic_request_validation",
                business_reference_status="not_applicable_api_contract",
            )
        )

    database_after = _sha256(database)
    raw_after = _raw_hashes(root)
    return {
        "measurement_scope": (
            "assistant_authored_mechanical_day12_http_cases; "
            "fastapi_testclient_in_process_asgi; fake_model_responses; "
            "real_local_sqlite_where_execution_started; "
            "no_independent_business_accuracy"
        ),
        "external_api_calls": 0,
        "model_generated_numeric_results": 0,
        "case_count": len(results),
        "expectation_met_count": sum(
            item["expectation_met"] for item in results
        ),
        "full_state_machine_case_count": len(full_workflow_cases),
        "real_sqlite_execution_case_count": sum(
            item["evidence_scope"]
            == "full_day11_fake_model_responses_real_local_sqlite_execution"
            for item in results
        ),
        "scripted_state_case_count": 2,
        "unhandled_exception_case_count": 1,
        "request_validation_case_count": 3,
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
        root / "data/processed/api/offline_api_benchmark.json",
        root / "docs/reports/api.json",
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
