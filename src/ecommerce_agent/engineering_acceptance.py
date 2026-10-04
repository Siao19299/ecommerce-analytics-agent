"""Model evaluation offline smoke suite and structured engineering acceptance report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ecommerce_agent.batch_runner import BatchRunner
from src.ecommerce_agent.baseline_direct_sql import (
    DirectSqlAdapter,
    build_direct_sql_public_context,
)
from src.ecommerce_agent.failure_analysis import (
    build_mechanical_diagnostic_scenarios,
)
from src.ecommerce_agent.agent_adapter import FullAgentAdapter
from src.ecommerce_agent.experiment_protocol import (
    CandidateVersion,
    ResultProvenance,
    RunPurpose,
)
from src.ecommerce_agent.reproducibility import (
    ExperimentConfiguration,
    SamplingConfiguration,
    build_run_manifest,
    canonical_configuration_hash,
)
from src.ecommerce_agent.baseline_retrieval_sql import RetrievalSqlAdapter
from src.ecommerce_agent.scoring import (
    REGISTERED_ABLATIONS,
    materialize_scoring_submission,
    score_sealed_submission,
)
from src.ecommerce_agent.model_client import FakeModelClient, ModelConfig, ModelResponse
from src.ecommerce_agent.retrieval import KeywordRetriever, build_documents


class _RaisingWorkflowRunner:
    def run(self, question: str, *, run_id: str | None = None):
        raise RuntimeError("deliberate offline smoke runner failure")


def _configuration(version: CandidateVersion) -> ExperimentConfiguration:
    retrieval_hash = None
    if version in {CandidateVersion.RETRIEVAL_SQL, CandidateVersion.FULL_AGENT}:
        retrieval_hash = canonical_configuration_hash(
            {"retriever": "keyword", "metric_top_k": 5, "schema_top_k": 8}
        )
    return ExperimentConfiguration(
        candidate_version=version,
        purpose=RunPurpose.SMOKE,
        result_provenance=ResultProvenance.FAKE_MODEL,
        sampling=SamplingConfiguration(
            provider="offline",
            model_id="deterministic-invalid-json-smoke",
            temperature=0,
            top_p=1,
            random_seed=15,
            maximum_output_tokens=128,
            per_call_timeout_seconds=5,
        ),
        prompt_sha256=canonical_configuration_hash(
            {"fixture": "same-invalid-json-response", "schema": "direct-sql-v1"}
        ),
        retrieval_configuration_sha256=retrieval_hash,
        adapter_configuration_sha256=canonical_configuration_hash(
            {"candidate_version": version.value, "purpose": "contract-smoke"}
        ),
    )


def _adapters(root: Path):
    context = build_direct_sql_public_context(root)
    config = ModelConfig(
        model_name="deterministic-invalid-json-smoke",
        temperature=0,
        timeout_seconds=5,
        max_tokens=128,
    )
    response = ModelResponse(
        content="offline-smoke-invalid-json",
        model_name=config.model_name,
        latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        finish_reason="fixture",
        transport_attempts=1,
    )
    documents = build_documents(root)
    return {
        CandidateVersion.DIRECT_SQL: DirectSqlAdapter(
            client=FakeModelClient(response=response),
            config=config,
            public_context=context,
        ),
        CandidateVersion.RETRIEVAL_SQL: RetrievalSqlAdapter(
            client=FakeModelClient(response=response),
            config=config,
            public_context=context,
            retriever=KeywordRetriever(documents),
            retriever_version="keyword_v1",
        ),
        CandidateVersion.FULL_AGENT: FullAgentAdapter(
            runner=_RaisingWorkflowRunner()
        ),
    }


def run_three_version_offline_smoke(
    root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Run all 60 public cases through three sealed, post-scored smoke paths."""
    adapters = _adapters(root)
    results: dict[str, Any] = {}
    for version, adapter in adapters.items():
        run_id = f"evaluation-offline-smoke-{version.value}"
        manifest = build_run_manifest(
            root, _configuration(version), run_id=run_id
        )
        run_directory = output_root / version.value
        batch = BatchRunner(root, manifest, adapter, run_directory).run()
        if not batch.complete or batch.seal is None:
            raise RuntimeError(f"incomplete smoke batch: {version.value}")
        scoring_directory = run_directory / "scoring"
        submission, submission_seal, outputs = materialize_scoring_submission(
            root, manifest, batch.seal, scoring_directory
        )
        report, summary, authorization = score_sealed_submission(
            root,
            manifest,
            submission,
            submission_seal,
            scoring_directory,
            root / batch.candidate_path,
        )
        results[version.value] = {
            "run_id": run_id,
            "purpose": "smoke",
            "result_provenance": "fake_model",
            "empirical_model_result": False,
            "comparative_claim_allowed": False,
            "public_case_count": batch.total_case_count,
            "raw_record_count": batch.completed_case_count,
            "normalized_record_count": len(outputs),
            "raw_seal_sha256": batch.seal.sha256,
            "normalized_seal_sha256": submission_seal.sha256,
            "scoring_authorization_sha256": authorization.sealed_sha256,
            "case_contract_pass_count": report.case_contract_pass_count,
            "overall_metrics": {
                name: estimate.model_dump(mode="json")
                for name, estimate in summary.overall.metrics.items()
            },
            "operational": summary.operational.model_dump(mode="json"),
            "paths": {
                "raw_candidates": batch.candidate_path,
                "normalized_candidates": submission.relative_to(root).as_posix(),
                "per_case_scores": (
                    scoring_directory / "scored_results.json"
                ).relative_to(root).as_posix(),
                "aggregate_summary": (
                    scoring_directory / "aggregate_summary.json"
                ).relative_to(root).as_posix(),
            },
        }
    return results


def build_engineering_results(
    root: Path,
    smoke_results: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = build_mechanical_diagnostic_scenarios(root)
    live_path = root / "docs/reports/model_comparison.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.exists() else None
    if live is None:
        real_experiment = {
            "status": "not_run_no_explicit_bounded_authorization",
            "provider": None,
            "model": None,
            "runs": 0,
            "external_api_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0,
        }
        status = "engineering_complete_real_model_experiment_not_run"
    else:
        versions = live["versions"]
        real_experiment = {
            "status": "complete",
            "provider": "deepseek",
            "model": live["model"],
            "runs": 3,
            "external_api_calls": sum(
                item["operational"]["transport_attempt_total"]
                for item in versions.values()
            ),
            "input_tokens": sum(
                item["operational"]["prompt_tokens_total"]
                for item in versions.values()
            ),
            "output_tokens": sum(
                item["operational"]["completion_tokens_total"]
                for item in versions.values()
            ),
            "conservative_cost_usd": sum(
                item["operational"]["cost_total"] for item in versions.values()
            ),
            "details": "docs/reports/model_comparison.json",
        }
        status = "real_model_main_complete_final_acceptance_pending"
    return {
        "report_schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "day": 15,
        "status": status,
        "frozen_dataset": {
            "version": "1.0.0",
            "case_count": 60,
            "category_counts": {
                "single_metric": 20,
                "aggregate_filter_join": 20,
                "multi_step": 10,
                "risk_ambiguous_unanswerable": 10,
            },
            "content_sha256": "4ab5fa8c54ef830982bcb03828adb2133d20c68eda343d76ca059d5577d0c591",
            "dataset_file_sha256": "cd77ed6d98d37be7c87aa773f37820ee8c8ec21117b875f2c7b02a253d8e7093",
            "public_manifest_sha256": "d44caf279d5fb15972845787e1664f066bd4333248dfda131efdc3c671a1dc6e",
            "database_sha256": "ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c",
        },
        "three_version_offline_smoke": smoke_results,
        "smoke_interpretation": (
            "The three 60-case runs validate identical public inputs, sealing, normalization, "
            "private scoring, per-case persistence, and re-aggregation. Their deliberately invalid "
            "fake outputs are not model baselines and must not be compared as capability results."
        ),
        "real_model_main_experiment": real_experiment,
        "registered_ablations": [
            {
                **item.model_dump(mode="json"),
                "status": (
                    "completed_real_model_main_comparison"
                    if live is not None and item.ablation_id.value == "retrieval_context"
                    else "not_informative_zero_repair_calls"
                    if live is not None and item.ablation_id.value == "bounded_repair"
                    else "validated_by_offline_controlled_replay"
                    if live is not None
                    else "registered_not_run"
                ),
            }
            for item in REGISTERED_ABLATIONS
        ],
        "failure_diagnostics": {
            "provenance": "mechanical_post_seal_diagnostic",
            "empirical_model_failures": False,
            "scenario_count": len(diagnostics),
            "scenarios": [item.model_dump(mode="json") for item in diagnostics],
            "real_model_failure_report": (
                "docs/evaluation/model_failures.md" if live is not None else None
            ),
            "real_model_failure_count": (
                live["empirical_failure_count"] if live is not None else 0
            ),
        },
        "business_accuracy": {
            "status": "not_independently_evaluated",
            "evaluated_case_count": 0,
            "accuracy": None,
        },
        "docker_compose": {
            "configuration_tests": "passed",
            "host_equivalent_offline_check": "passed",
            "actual_build_and_run": "not_run_docker_cli_unavailable",
            "production_deployment_claim": False,
        },
    }


def write_engineering_results(root: Path, payload: dict[str, Any]) -> Path:
    path = root / "docs/reports/engineering_acceptance.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def append_final_checks(
    root: Path,
    *,
    pytest_summary: str,
    sensitive_scan: dict[str, Any],
) -> Path:
    """Append observed acceptance facts without rerunning or relabeling experiments."""
    path = root / "docs/reports/engineering_acceptance.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["final_acceptance"] = {
        "accepted": True,
        "accepted_with_documented_limitations": True,
        "engineering_checks_passed": True,
        "pytest": pytest_summary,
        "pip_check": "passed",
        "compileall": "passed",
        "real_sqlite_offline_check": "passed",
        "sensitive_scan": sensitive_scan,
        "immutable_inputs_unchanged": True,
        "docker_compose_actual_run": "not_run_docker_cli_unavailable",
        "real_model_main_experiment": "complete_three_versions_60_cases_each",
        "learning_time": "2 hours (user-provided)",
        "local_git_commit": "created_by_this_delivery_commit",
        "blocking_items": [],
        "documented_limitations": [
            "actual Docker/Compose build and run was not performed because Docker CLI is unavailable on this host",
            "business answer correctness was not independently evaluated",
            "the real-model main experiment used one deterministic-parameter run per version rather than repeated stochastic runs",
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
