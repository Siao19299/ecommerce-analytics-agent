import argparse
import json
from pathlib import Path

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.deepseek_client import (
    DeepSeekClient,
    DeepSeekCredentials,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    ModelConfig,
    RetryPolicy,
    RetryingModelClient,
)
from src.ecommerce_agent.planning_log import append_planning_logs


def run_live_plan(
    question: str,
    model_name: str,
    log_path: str | Path,
) -> dict:
    project_root = Path(__file__).parents[2]
    catalog = MetricCatalog.from_csv(
        project_root / "data" / "metadata" / "metric_dictionary.csv",
        project_root / "data" / "metadata" / "dimension_dictionary.csv",
    )
    client = RetryingModelClient(
        client=DeepSeekClient(
            credentials=DeepSeekCredentials.from_environment()
        ),
        policy=RetryPolicy(
            max_attempts=2,
            initial_backoff_seconds=1,
        ),
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(
            model_name=model_name,
            temperature=0,
            timeout_seconds=30,
            max_tokens=1024,
        ),
        metric_catalog=catalog,
        max_output_corrections=1,
    )

    result = planner.create_plan(question)
    append_planning_logs(log_path, result.log_records)

    final_response = result.model_response
    output = {
        "run_id": result.run_id,
        "model_name": (
            final_response.model_name
            if final_response is not None
            else model_name
        ),
        "output_attempts": len(result.model_responses),
        "latency_ms": (
            final_response.latency_ms
            if final_response is not None
            else None
        ),
        "prompt_tokens": (
            final_response.prompt_tokens
            if final_response is not None
            else None
        ),
        "completion_tokens": (
            final_response.completion_tokens
            if final_response is not None
            else None
        ),
        "finish_reason": (
            final_response.finish_reason
            if final_response is not None
            else None
        ),
    }

    if result.client_error_type is not None:
        output.update(
            {
                "status": "failed",
                "error_type": result.client_error_type.value,
                "error_message": result.client_error_message,
            }
        )
    elif result.validation.needs_clarification:
        output.update(
            {
                "status": "needs_clarification",
                "clarification_question": (
                    result.validation.clarification_question
                ),
            }
        )
    elif result.validation.plan is not None:
        output.update(
            {
                "status": "ready",
                "plan": result.validation.plan.model_dump(mode="json"),
            }
        )
    else:
        output.update(
            {
                "status": "failed",
                "error_type": result.validation.error_type.value,
                "error_message": result.validation.error_message,
            }
        )

    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", required=True)
    parser.add_argument(
        "--model",
        default="deepseek-v4-flash",
    )
    parser.add_argument(
        "--log-path",
        default=(
            "data/processed/logs/planning.jsonl"
        ),
    )
    args = parser.parse_args()

    output = run_live_plan(
        question=args.question,
        model_name=args.model,
        log_path=args.log_path,
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
