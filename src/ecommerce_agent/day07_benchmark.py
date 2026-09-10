"""Run ten public/synthetic Day 7 cases with explicitly fake clients."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.day07_pipeline import Day07Agent
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
)
from src.ecommerce_agent.retrieval import KeywordRetriever, build_documents
from src.ecommerce_agent.sql_generation import (
    SqlGenerator,
    execute_read_only_query,
)
from src.ecommerce_agent.sql_safety import build_global_sql_policy


EXPECTED_OUTCOMES = {
    "succeeded",
    "needs_clarification",
    "retrieval_error",
    "sql_syntax_error",
    "field_error",
    "business_semantics_error",
}


def validate_cases(cases: list[dict[str, Any]]) -> None:
    if len(cases) != 10:
        raise ValueError("Day 7 离线批次必须恰好包含 10 题")
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Day 7 case_id 必须唯一")
    for case in cases:
        if case["expected_outcome"] not in EXPECTED_OUTCOMES:
            raise ValueError("未知 expected_outcome")
        if case["question_source"] != "synthetic_for_public_olist":
            raise ValueError("Day 7 批次只能使用公开或合成问题")
        if case["planning_response_source"] != "fake_model_response":
            raise ValueError("规划响应必须明确标为 fake_model_response")
        if case["sql_response_source"] != (
            "fake_model_response_with_preset_sql_fixture"
        ):
            raise ValueError("SQL 响应必须明确标记假响应和预设 SQL")
        if case.get("reference_sql") and case.get(
            "reference_source"
        ) != "assistant_reviewed_reference_query":
            raise ValueError("参考查询来源必须明确")


def observed_outcome(record: dict[str, Any]) -> str:
    if record["status"] == "succeeded":
        return "succeeded"
    if record["status"] == "needs_clarification":
        return "needs_clarification"
    category = (record.get("error") or {}).get("category")
    if category == "sql_safety":
        safety_code = (record.get("sql_safety") or {}).get("error_code")
        if safety_code == "parse_error":
            return "sql_syntax_error"
        if safety_code == "column_resolution_failed":
            return "field_error"
        return "sql_safety_error"
    return {
        "retrieval": "retrieval_error",
        "sql_syntax": "sql_syntax_error",
        "field": "field_error",
        "business_semantics": "business_semantics_error",
        "plan_grounding": "plan_grounding_error",
    }.get(category, f"unexpected_{category}")


def run_case(
    root: Path,
    database_path: Path,
    documents,
    retriever,
    catalog: MetricCatalog,
    case: dict[str, Any],
) -> dict[str, Any]:
    planning_client = FakeModelClient(
        ModelResponse(
            content=json.dumps(
                case["planning_response"],
                ensure_ascii=False,
            ),
            model_name="fake-day07-planner",
        )
    )
    sql_client = FakeModelClient(
        ModelResponse(
            content=json.dumps(
                case["sql_response"],
                ensure_ascii=False,
            ),
            model_name="fake-day07-sql-generator",
        )
    )
    agent = Day07Agent(
        root=root,
        database_path=database_path,
        documents=documents,
        retriever=retriever,
        planner=AnalysisPlanner(
            client=planning_client,
            config=ModelConfig(
                model_name="fake-day07-planner",
                temperature=0,
                max_tokens=1024,
            ),
            metric_catalog=catalog,
            max_output_corrections=0,
            require_retrieval_grounding=True,
        ),
        sql_generator=SqlGenerator(
            client=sql_client,
            config=ModelConfig(
                model_name="fake-day07-sql-generator",
                temperature=0,
                max_tokens=2048,
            ),
        ),
        planning_response_source=case["planning_response_source"],
        sql_response_source=case["sql_response_source"],
    )
    record = agent.run(case["question"])
    record["case_id"] = case["case_id"]
    record["question_source"] = case["question_source"]
    record["expected_outcome"] = case["expected_outcome"]
    record["observed_outcome_before_reference_check"] = observed_outcome(
        record
    )
    record["fake_client_call_counts"] = {
        "planning": len(planning_client.requests),
        "sql_generation": len(sql_client.requests),
    }
    record["model_requests"] = {
        "planning": [
            {
                "messages": [
                    {
                        "role": message.role.value,
                        "content": message.content,
                    }
                    for message in messages
                ],
                "config": asdict(config),
            }
            for messages, config in planning_client.requests
        ],
        "sql_generation": [
            {
                "messages": [
                    {
                        "role": message.role.value,
                        "content": message.content,
                    }
                    for message in messages
                ],
                "config": asdict(config),
            }
            for messages, config in sql_client.requests
        ],
    }

    reference_sql = case.get("reference_sql")
    if reference_sql:
        reference = execute_read_only_query(
            database_path,
            reference_sql,
            case.get("reference_parameters", {}),
            safety_policy=build_global_sql_policy(root),
        )
        if not reference.is_success:
            raise RuntimeError(
                f"参考查询执行失败 {case['case_id']}: "
                f"{reference.error_message}"
            )
        actual_rows = (record.get("query_result") or {}).get("rows")
        reference_rows = list(reference.rows)
        reference_match = (
            actual_rows == reference_rows
            if record["status"] == "succeeded"
            else None
        )
        record["reference"] = {
            "source": case["reference_source"],
            "sql": reference_sql,
            "parameters": case.get("reference_parameters", {}),
            "columns": list(reference.columns),
            "rows": reference_rows,
            "candidate_result_match": reference_match,
        }
        if record["status"] == "succeeded" and not reference_match:
            record["status"] = "failed"
            record["error"] = {
                "category": "business_semantics",
                "message": (
                    "SQL 可执行，但结果与独立参考查询不一致"
                ),
            }

    runtime_outcome = observed_outcome(record)
    record["runtime_outcome"] = runtime_outcome
    reference_metric_ids = set(case.get("reference_metric_ids", []))
    retrieved_metric_ids = {
        hit["document"]["identifier"]
        for hit in record["retrieval"]["metric"]["hits"]
    }
    missing_reference_metrics = sorted(
        reference_metric_ids - retrieved_metric_ids
    )
    if (
        runtime_outcome == "plan_grounding_error"
        and missing_reference_metrics
    ):
        record["evaluation_error"] = {
            "category": "retrieval",
            "message": (
                "带参考标签的离线评测确认规范指标未进入检索候选："
                + ", ".join(missing_reference_metrics)
            ),
        }
        record["observed_outcome"] = "retrieval_error"
    else:
        record["evaluation_error"] = None
        record["observed_outcome"] = runtime_outcome
    record["expectation_met"] = (
        record["observed_outcome"] == case["expected_outcome"]
    )
    return record


def compact_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "measurement_scope": report["measurement_scope"],
        "input_sha256": report["input_sha256"],
        "database_sha256": report["database_sha256"],
        "case_count": len(report["cases"]),
        "expectation_met_count": sum(
            case["expectation_met"] for case in report["cases"]
        ),
        "observed_outcome_counts": report["observed_outcome_counts"],
        "error_category_counts": report["error_category_counts"],
        "generation_disclosure": report["generation_disclosure"],
        "cases": [
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "expected_outcome": case["expected_outcome"],
                "observed_outcome": case["observed_outcome"],
                "expectation_met": case["expectation_met"],
                "error_category": (
                    case.get("error") or {}
                ).get("category"),
                "evaluation_error_category": (
                    case.get("evaluation_error") or {}
                ).get("category"),
                "runtime_outcome": case["runtime_outcome"],
                "planning_response_source": case["metadata"][
                    "planning_response_source"
                ],
                "sql_response_source": case["metadata"][
                    "sql_response_source"
                ],
                "reference_source": (
                    case.get("reference") or {}
                ).get("source"),
                "reference_match": (
                    case.get("reference") or {}
                ).get("candidate_result_match"),
                "candidate_rows_preview": (
                    (case.get("query_result") or {}).get("rows") or []
                )[:5],
                "reference_rows_preview": (
                    (case.get("reference") or {}).get("rows") or []
                )[:5],
            }
            for case in report["cases"]
        ],
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite path; defaults to data/processed/olist.sqlite3",
    )
    args = parser.parse_args()
    root = Path(__file__).parents[2]
    fixture_path = root / "tests" / "fixtures" / "day07" / "cases.json"
    database_path = args.database or (
        root / "data" / "processed" / "olist.sqlite3"
    )
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))["cases"]
    validate_cases(cases)
    documents = build_documents(root)
    retriever = KeywordRetriever(documents)
    catalog = MetricCatalog.from_csv(
        root / "data" / "metadata" / "metric_dictionary.csv",
        root / "data" / "metadata" / "dimension_dictionary.csv",
    )
    results = [
        run_case(
            root,
            database_path,
            documents,
            retriever,
            catalog,
            case,
        )
        for case in cases
    ]
    outcome_counts = {
        outcome: sum(
            result["observed_outcome"] == outcome for result in results
        )
        for outcome in sorted(EXPECTED_OUTCOMES)
    }
    error_categories = {
        category: sum(
            (
                (result.get("evaluation_error") or {}).get("category")
                or (result.get("error") or {}).get("category")
            ) == category
            for result in results
        )
        for category in (
            "retrieval",
            "sql_syntax",
            "field",
            "business_semantics",
        )
    }
    report = {
        "measurement_scope": (
            "offline_integration_with_fake_model_responses_and_preset_sql; "
            "not_real_model_generation_accuracy"
        ),
        "generation_disclosure": {
            "planning": "fake_model_response",
            "sql": "fake_model_response_with_preset_sql_fixture",
            "reference": "assistant_reviewed_reference_query",
            "external_api_calls": 0,
        },
        "input_sha256": file_sha256(fixture_path),
        "database_sha256": file_sha256(database_path),
        "retriever_version": retriever.version,
        "observed_outcome_counts": outcome_counts,
        "error_category_counts": error_categories,
        "cases": results,
    }
    if not all(result["expectation_met"] for result in results):
        failures = [
            result["case_id"]
            for result in results
            if not result["expectation_met"]
        ]
        raise RuntimeError(f"离线批次结果不符合预期：{failures}")

    output_dir = root / "data" / "processed" / "day07"
    output_dir.mkdir(parents=True, exist_ok=True)
    full_path = output_dir / "offline_benchmark.json"
    full_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    compact_path = root / "docs" / "DAY07_BENCHMARK_RESULTS.json"
    compact_path.write_text(
        json.dumps(compact_report(report), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Day 7 cases: {len(results)}; expectations met: "
        f"{sum(result['expectation_met'] for result in results)}"
    )
    print(f"Outcomes: {outcome_counts}")
    print(f"Errors: {error_categories}")
    print(f"Full report: {full_path}")
    print(f"Compact report: {compact_path}")


if __name__ == "__main__":
    main()
