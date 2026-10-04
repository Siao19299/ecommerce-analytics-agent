"""Authorized four-question Query pipeline DeepSeek batch.

Only public/synthetic Olist questions and derived metadata are sent. The API
key is read by DeepSeekCredentials at runtime and is never copied into records.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.query_pipeline import QueryAgent
from src.ecommerce_agent.deepseek_client import (
    DeepSeekClient,
    DeepSeekCredentials,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    ModelClient,
    ModelClientError,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    RetryPolicy,
    RetryingModelClient,
)
from src.ecommerce_agent.retrieval import KeywordRetriever, build_documents
from src.ecommerce_agent.sql_generation import (
    SqlGenerator,
    execute_read_only_query,
)
from src.ecommerce_agent.sql_safety import build_global_sql_policy


APPROVED_CASE_IDS = ("D7_01", "D7_02", "D7_04", "D7_05")


@dataclass
class RecordingModelClient:
    """Record safe request/response data around each actual HTTP attempt."""

    client: ModelClient
    stage: str
    events: list[dict] = field(default_factory=list)

    def generate(
        self,
        messages: Sequence[ModelMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        event = {
            "stage": self.stage,
            "request": {
                "messages": [
                    {
                        "role": message.role.value,
                        "content": message.content,
                    }
                    for message in messages
                ],
                "config": asdict(config),
            },
            "response": None,
            "error": None,
        }
        self.events.append(event)
        try:
            response = self.client.generate(messages, config)
        except ModelClientError as error:
            event["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            raise
        event["response"] = {
            "content": response.content,
            "model_name": response.model_name,
            "latency_ms": response.latency_ms,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "finish_reason": response.finish_reason,
        }
        return response


def _evaluate_reference(
    record: dict,
    case: dict,
    database_path: Path,
) -> dict:
    reference = execute_read_only_query(
        database_path,
        case["reference_sql"],
        case.get("reference_parameters", {}),
        safety_policy=build_global_sql_policy(Path(__file__).parents[2]),
    )
    if not reference.is_success:
        raise RuntimeError(
            f"参考查询失败 {case['case_id']}: {reference.error_message}"
        )
    actual_rows = (record.get("query_result") or {}).get("rows")
    match = (
        actual_rows == list(reference.rows)
        if record["status"] == "succeeded"
        else None
    )
    return {
        "source": case["reference_source"],
        "sql": case["reference_sql"],
        "parameters": case.get("reference_parameters", {}),
        "columns": list(reference.columns),
        "rows": list(reference.rows),
        "candidate_result_match": match,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _compact_report(report: dict) -> dict:
    successful_events = [
        event
        for case in report["cases"]
        for stage_events in case["actual_api_events"].values()
        for event in stage_events
        if event["response"] is not None
    ]
    known_prompt_tokens = [
        event["response"]["prompt_tokens"]
        for event in successful_events
        if event["response"]["prompt_tokens"] is not None
    ]
    known_completion_tokens = [
        event["response"]["completion_tokens"]
        for event in successful_events
        if event["response"]["completion_tokens"] is not None
    ]
    return {
        "measurement_scope": report["measurement_scope"],
        "authorization_scope": report["authorization_scope"],
        "model_config": report["model_config"],
        "question_count": len(report["cases"]),
        "actual_http_attempt_count": sum(
            len(events)
            for case in report["cases"]
            for events in case["actual_api_events"].values()
        ),
        "successful_api_response_count": len(successful_events),
        "known_prompt_tokens_total": sum(known_prompt_tokens),
        "known_completion_tokens_total": sum(known_completion_tokens),
        "responses_with_missing_prompt_tokens": (
            len(successful_events) - len(known_prompt_tokens)
        ),
        "responses_with_missing_completion_tokens": (
            len(successful_events) - len(known_completion_tokens)
        ),
        "cost": None,
        "cost_note": "No verified provider price calculation was performed.",
        "cases": [
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "status": case["status"],
                "error": case.get("error"),
                "analysis_plan": case.get("analysis_plan"),
                "analysis_plan_evidence_document_ids": case.get(
                    "analysis_plan_evidence_document_ids"
                ),
                "sql": case.get("sql"),
                "parameters": case.get("parameters"),
                "candidate_rows_preview": (
                    (case.get("query_result") or {}).get("rows") or []
                )[:5],
                "reference_rows_preview": case["reference"]["rows"][:5],
                "reference_match": case["reference"][
                    "candidate_result_match"
                ],
                "actual_http_attempts": {
                    stage: len(events)
                    for stage, events in case["actual_api_events"].items()
                },
                "actual_response_usage": {
                    stage: [
                        {
                            key: event["response"][key]
                            for key in (
                                "model_name",
                                "latency_ms",
                                "prompt_tokens",
                                "completion_tokens",
                                "finish_reason",
                            )
                        }
                        for event in events
                        if event["response"] is not None
                    ]
                    for stage, events in case["actual_api_events"].items()
                },
            }
            for case in report["cases"]
        ],
    }


def run_live_batch(
    root: Path,
    database_path: Path,
    model_name: str,
) -> dict:
    fixture = json.loads(
        (
            root / "tests" / "fixtures" / "query" / "cases.json"
        ).read_text(encoding="utf-8")
    )["cases"]
    by_id = {case["case_id"]: case for case in fixture}
    cases = [by_id[case_id] for case_id in APPROVED_CASE_IDS]
    if any(
        case["question_source"] != "synthetic_for_public_olist"
        for case in cases
    ):
        raise ValueError("真实批次只允许公开 Olist 合成问题")

    documents = build_documents(root)
    retriever = KeywordRetriever(documents)
    catalog = MetricCatalog.from_csv(
        root / "data" / "metadata" / "metric_dictionary.csv",
        root / "data" / "metadata" / "dimension_dictionary.csv",
    )
    credentials = DeepSeekCredentials.from_environment()
    planning_recorder = RecordingModelClient(
        DeepSeekClient(credentials=credentials),
        "planning",
    )
    sql_recorder = RecordingModelClient(
        DeepSeekClient(credentials=credentials),
        "sql_generation",
    )
    planning_client = RetryingModelClient(
        planning_recorder,
        RetryPolicy(max_attempts=2, initial_backoff_seconds=1),
    )
    sql_client = RetryingModelClient(
        sql_recorder,
        RetryPolicy(max_attempts=2, initial_backoff_seconds=1),
    )
    report = {
        "measurement_scope": (
            "authorized_real_deepseek_generation_on_public_olist_questions"
        ),
        "authorization_scope": (
            "user_authorized_real_api_for_the_previously_disclosed_"
            "four_question_batch"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_config": {
            "model_name": model_name,
            "temperature": 0,
            "thinking": "disabled_by_deepseek_client",
            "response_format": "json_object",
            "planning_max_tokens": 1024,
            "sql_max_tokens": 2048,
            "timeout_seconds": 30,
            "max_logical_calls": 8,
            "max_http_attempts": 16,
            "planning_output_corrections": 0,
            "sql_repairs": 0,
        },
        "context_scope": {
            "planning": (
                "keyword_v1_metric_top5_and_schema_top8_full_hits"
            ),
            "sql_generation": (
                "validated_plan_canonical_metric_definitions_required_"
                "schema_dimensions_grain_constraints_and_parameters"
            ),
            "excluded": [
                "database_rows",
                "data/raw contents",
                "API key",
                "company data",
            ],
        },
        "cases": [],
    }
    full_path = root / "data" / "processed" / "query" / "live_batch.json"

    for case in cases:
        planning_start = len(planning_recorder.events)
        sql_start = len(sql_recorder.events)
        agent = QueryAgent(
            root=root,
            database_path=database_path,
            documents=documents,
            retriever=retriever,
            planner=AnalysisPlanner(
                client=planning_client,
                config=ModelConfig(
                    model_name=model_name,
                    temperature=0,
                    timeout_seconds=30,
                    max_tokens=1024,
                ),
                metric_catalog=catalog,
                max_output_corrections=0,
                require_retrieval_grounding=True,
            ),
            sql_generator=SqlGenerator(
                client=sql_client,
                config=ModelConfig(
                    model_name=model_name,
                    temperature=0,
                    timeout_seconds=30,
                    max_tokens=2048,
                ),
            ),
            planning_response_source="real_deepseek_model_response",
            sql_response_source="real_deepseek_model_response",
        )
        record = agent.run(case["question"])
        record["case_id"] = case["case_id"]
        record["question_source"] = case["question_source"]
        record["reference"] = _evaluate_reference(
            record,
            case,
            database_path,
        )
        record["actual_api_events"] = {
            "planning": planning_recorder.events[planning_start:],
            "sql_generation": sql_recorder.events[sql_start:],
        }
        report["cases"].append(record)
        _write_json(full_path, report)

    report["actual_http_attempt_count"] = (
        len(planning_recorder.events) + len(sql_recorder.events)
    )
    if report["actual_http_attempt_count"] > 16:
        raise RuntimeError("真实批次超过已授权 HTTP 请求上限")
    _write_json(full_path, report)
    compact_path = root / "docs" / "reports/query_model_run.json"
    _write_json(compact_path, _compact_report(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--database", type=Path, default=None)
    args = parser.parse_args()
    root = Path(__file__).parents[2]
    database_path = args.database or (
        root / "data" / "processed" / "olist.sqlite3"
    )
    report = run_live_batch(root, database_path, args.model)
    print(
        json.dumps(
            {
                "question_count": len(report["cases"]),
                "actual_http_attempt_count": report[
                    "actual_http_attempt_count"
                ],
                "statuses": {
                    case["case_id"]: case["status"]
                    for case in report["cases"]
                },
                "reference_matches": {
                    case["case_id"]: case["reference"][
                        "candidate_result_match"
                    ]
                    for case in report["cases"]
                },
                "compact_result": str(
                    root / "docs" / "reports/query_model_run.json"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
