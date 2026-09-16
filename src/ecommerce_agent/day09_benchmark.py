"""Offline Day 9 benchmark with fake responses and real SQLite execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.ecommerce_agent.day09_attempt_budget import RepairLimits
from src.ecommerce_agent.day09_pipeline import Day09RepairWorkflow
from src.ecommerce_agent.day09_repair import SqlRepairer
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
    RetryPolicy,
    RetryingModelClient,
    TransientModelError,
)
from src.ecommerce_agent.sql_generation import (
    GeneratedQuery,
    SqlGenerationContext,
)
from src.ecommerce_agent.sql_safety import (
    SqlSafetyPolicy,
    load_global_schema,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cases(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return (
        payload["case_source"],
        payload["response_source"],
        payload["cases"],
    )


def _context(
    root: Path,
    *,
    timeout_seconds: float = 10.0,
) -> SqlGenerationContext:
    global_schema = load_global_schema(root)
    plan_fields = global_schema["fact_orders"]
    return SqlGenerationContext(
        question="公开 Olist 数据中的订单数机械测试",
        analysis_plan={
            "metrics": ["order_count"],
            "dimensions": [],
            "filters": [],
            "time_range": {"mode": "all_data"},
        },
        retrieved_document_ids=("metric:order_count",),
        metric_definitions=(
            {
                "metric_id": "order_count",
                "formula": "COUNT(DISTINCT fact_orders.order_id)",
                "grain": "order",
            },
        ),
        dimension_definitions=(),
        schema_fields=tuple(
            {"qualified_name": f"fact_orders.{field}"}
            for field in sorted(plan_fields)
        ),
        table_context=(
            {"table": "fact_orders", "grain": "one order"},
        ),
        parameter_contract={},
        safety_policy=SqlSafetyPolicy(
            global_schema=global_schema,
            plan_schema={"fact_orders": plan_fields},
            timeout_seconds=timeout_seconds,
            progress_handler_steps=1,
        ),
    )


def _response(sql: str) -> ModelResponse:
    return ModelResponse(
        content=json.dumps({"sql": sql, "parameters": {}}),
        model_name="fake-day09-repair",
        prompt_tokens=None,
        completion_tokens=None,
        latency_ms=None,
        finish_reason=None,
        transport_attempts=None,
    )


def summarize_cases(results: list[dict[str, Any]]) -> dict[str, Any]:
    entered_repair = sum(case["entered_repair"] for case in results)
    repair_successes = sum(
        case["observed_stop_reason"] == "repair_succeeded"
        for case in results
    )
    return {
        "total_requests": len(results),
        "first_attempt_successes": sum(
            case["observed_stop_reason"] == "first_attempt_succeeded"
            for case in results
        ),
        "eligible_cases_entering_repair": entered_repair,
        "repair_successes": repair_successes,
        "repair_limit_reached": sum(
            case["observed_stop_reason"] == "repair_limit_reached"
            for case in results
        ),
        "duplicate_candidates": sum(
            case["observed_stop_reason"] == "duplicate_candidate"
            for case in results
        ),
        "safety_rejections": sum(
            case["observed_stop_reason"]
            in {"safety_failure", "repair_candidate_safety_rejected"}
            for case in results
        ),
        "environment_errors": sum(
            case["observed_stop_reason"] == "environment_error"
            for case in results
        ),
        "resource_failures": sum(
            case["observed_stop_reason"] == "resource_failure"
            for case in results
        ),
        "repair_success_rate": {
            "numerator": repair_successes,
            "denominator": entered_repair,
            "value": (
                repair_successes / entered_repair
                if entered_repair
                else None
            ),
            "formula": (
                "repair_successes / eligible_cases_entering_repair"
            ),
        },
    }


def run_benchmark(root: Path) -> dict[str, Any]:
    fixture_path = root / "tests/fixtures/day09/repair_cases.json"
    case_source, response_source, cases = load_cases(fixture_path)
    database_path = root / "data/processed/olist.sqlite3"
    database_hash_before = _sha256(database_path)
    results = []

    for case in cases:
        responses = [_response(sql) for sql in case["repair_sqls"]]
        fallback = responses[-1] if responses else _response("SELECT 1")
        transient_count = int(case.get("simulated_transient_errors", 0))
        fake_client = FakeModelClient(
            response=fallback,
            errors_before_response=[
                TransientModelError("synthetic transient model error")
                for _ in range(transient_count)
            ],
            scripted_responses=list(responses),
        )
        model_client = fake_client
        case_response_source = response_source
        if transient_count:
            model_client = RetryingModelClient(
                fake_client,
                RetryPolicy(
                    max_attempts=transient_count + 1,
                    initial_backoff_seconds=0,
                ),
            )
            case_response_source = (
                "fake_model_response_with_simulated_transient_errors"
            )
        selected_database = (
            root / "data/processed/day09/missing.sqlite3"
            if case.get("database_mode") == "missing"
            else database_path
        )
        workflow = Day09RepairWorkflow(
            database_path=selected_database,
            repairer=SqlRepairer(
                model_client,
                ModelConfig(model_name="fake-day09-repair"),
            ),
            limits=RepairLimits(
                max_repair_attempts=int(
                    case.get("max_repair_attempts", 2)
                )
            ),
            response_source=case_response_source,
        )
        result = workflow.run(
            GeneratedQuery(sql=case["initial_sql"], parameters={}),
            _context(
                root,
                timeout_seconds=float(case.get("timeout_seconds", 10.0)),
            ),
            run_id=f"day09-{case['case_id'].lower()}",
        )
        observed = result.trace.stop_reason
        results.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "expected_stop_reason": case["expected_stop_reason"],
                "observed_stop_reason": observed,
                "expectation_met": observed == case["expected_stop_reason"],
                "entered_repair": len(fake_client.requests) > 0,
                "fake_model_generate_calls": len(fake_client.requests),
                "external_api_calls": 0,
                "repair_succeeded": result.repair_succeeded,
                "business_validation_status": (
                    result.business_validation_status.value
                ),
                "trace": result.trace.to_dict(),
            }
        )

    database_hash_after = _sha256(database_path)
    return {
        "measurement_scope": (
            "offline_fake_model_responses_with_real_local_sqlite_execution; "
            "not_real_model_repair_accuracy"
        ),
        "case_source": case_source,
        "response_source": response_source,
        "fixture_sha256": _sha256(fixture_path),
        "database_path": str(database_path),
        "database_sha256_before": database_hash_before,
        "database_sha256_after": database_hash_after,
        "database_unchanged": database_hash_before == database_hash_after,
        "external_api_calls": 0,
        "cost": None,
        "cost_note": (
            "No external model call or verified provider price was used."
        ),
        "case_count": len(results),
        "expectation_met_count": sum(
            result["expectation_met"] for result in results
        ),
        "summary": summarize_cases(results),
        "cases": results,
    }


def main() -> None:
    root = Path(__file__).parents[2]
    report = run_benchmark(root)
    processed = root / "data/processed/day09/offline_benchmark.json"
    compact = root / "docs/DAY09_REPAIR_RESULTS.json"
    processed.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    processed.write_text(payload, encoding="utf-8")
    compact.write_text(payload, encoding="utf-8")
    print(
        f"Day 9 offline benchmark: "
        f"{report['expectation_met_count']}/{report['case_count']} expected"
    )


if __name__ == "__main__":
    main()
