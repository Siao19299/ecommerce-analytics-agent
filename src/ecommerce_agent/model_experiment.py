"""Authorized, budgeted DeepSeek runs for the frozen Model evaluation comparison."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.repair_limits import RepairLimits
from src.ecommerce_agent.repair_workflow import SqlRepairWorkflow
from src.ecommerce_agent.sql_repair import SqlRepairer
from src.ecommerce_agent.analysis_adapter import (
    load_metric_provenance,
    monthly_observations_from_query_result,
)
from src.ecommerce_agent.anomaly_detection import AnomalyRequest, AnomalyResult, detect_monthly_anomaly
from src.ecommerce_agent.period_comparison import (
    ComparisonRequest,
    ComparisonResult,
    ComparisonType,
    calculate_period_comparison,
)
from src.ecommerce_agent.contribution_analysis import (
    ContributionComponent,
    ContributionRequest,
    ContributionResult,
    ContributionScope,
    calculate_contribution,
)
from src.ecommerce_agent.analysis_models import CalculationLineage, MetricProvenance, PeriodCompleteness
from src.ecommerce_agent.analysis_presentation import (
    ChartSpec,
    ChartType,
    DeterministicPresentation,
    TableSpec,
    present_anomaly,
    present_comparison,
    present_contribution,
)
from src.ecommerce_agent.workflow_state import WorkflowState
from src.ecommerce_agent.workflow import WorkflowServices, AgentStateMachine
from src.ecommerce_agent.evaluation_schema import CalculationStatus
from src.ecommerce_agent.model_budget import ApiBudget, BudgetedModelClient
from src.ecommerce_agent.batch_runner import BatchRunner
from src.ecommerce_agent.baseline_direct_sql import DirectSqlAdapter, build_direct_sql_public_context
from src.ecommerce_agent.agent_adapter import FullAgentAdapter
from src.ecommerce_agent.experiment_protocol import CandidateVersion, ResultProvenance, RunPurpose
from src.ecommerce_agent.reproducibility import (
    ExperimentConfiguration,
    SamplingConfiguration,
    build_run_manifest,
    canonical_configuration_hash,
)
from src.ecommerce_agent.baseline_retrieval_sql import RetrievalSqlAdapter
from src.ecommerce_agent.scoring import materialize_scoring_submission, score_sealed_submission
from src.ecommerce_agent.deepseek_client import DeepSeekClient, DeepSeekCredentials
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import ModelConfig
from src.ecommerce_agent.retrieval import KeywordRetriever, build_documents
from src.ecommerce_agent.sql_generation import SqlGenerator


MODEL_ID = "deepseek-flash"


class SqlResultAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: str = "sql_result"
    metric: MetricProvenance
    lineage: CalculationLineage
    calculation_status: CalculationStatus = CalculationStatus.NOT_APPLICABLE
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


def _target_month(question: str) -> date:
    matches = re.findall(
        r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月", question
    )
    if not matches:
        raise ValueError("multi-step question has no explicit target month")
    current_year = None
    periods = []
    for year, month in matches:
        if year:
            current_year = int(year)
        if current_year is not None:
            periods.append(date(current_year, int(month), 1))
    if not periods:
        raise ValueError("multi-step target month has no year context")
    return max(periods)


def _lineage(state: WorkflowState) -> CalculationLineage:
    assert state.sql_attempt_trace is not None
    return CalculationLineage(
        parent_run_id=state.run_id,
        source_sql_attempt=state.sql_attempt_trace.attempts[-1].sql_attempt,
        input_reference="query_trace.attempts[-1].result_summary.rows",
    )


def _filters(state: WorkflowState) -> dict[str, str | int | float | bool]:
    assert state.analysis_plan is not None
    return {
        item.field: (
            item.value if isinstance(item.value, (str, int, float, bool)) else str(item.value)
        )
        for item in state.analysis_plan.filters
    }


def build_live_analyzer(root: Path):
    def analyze(state: WorkflowState):
        assert state.execution_result is not None
        assert state.analysis_plan is not None
        metric_id = state.analysis_plan.metrics[0]
        metric = load_metric_provenance(root, metric_id, filters=_filters(state))
        lineage = _lineage(state)
        question = state.question
        target = _target_month(question) if any(
            marker in question for marker in ("环比", "同比", "贡献度", "异常")
        ) else None

        if "贡献度" in question:
            rows = state.execution_result.rows
            components = tuple(
                ContributionComponent(
                    group=str(row["product_category"]),
                    value=row["metric_value"],
                    is_unknown_group=str(row["product_category"]) == "unknown",
                )
                for row in rows
            )
            scope = ContributionScope(
                metric_id=metric_id,
                period=target,
                filters=_filters(state),
                status_scope="validated_analysis_plan_filters",
                amount_basis="metric_dictionary_formula",
                completeness=PeriodCompleteness.COMPLETE,
            )
            return calculate_contribution(ContributionRequest(
                metric=metric,
                dimension="product_category",
                scope=scope,
                components=components,
                lineage=lineage,
            ))

        if "环比" in question or "同比" in question or "异常" in question:
            incomplete = {}
            if "不完整" in question or "未结束" in question:
                incomplete[target] = "question_explicitly_marks_incomplete_period"
            observations = monthly_observations_from_query_result(
                state.execution_result,
                period_column="purchase_month",
                value_column="metric_value",
                incomplete_period_reasons=incomplete,
            )
            if "异常" in question:
                return detect_monthly_anomaly(AnomalyRequest(
                    metric=metric,
                    target_period=target,
                    observations=observations,
                    lineage=lineage,
                    history_window=5,
                    minimum_history=5,
                    threshold=3.5,
                ))
            return calculate_period_comparison(ComparisonRequest(
                analysis_type=(
                    ComparisonType.YOY if "同比" in question else ComparisonType.MOM
                ),
                target_period=target,
                metric=metric,
                observations=observations,
                lineage=lineage,
            ))

        return SqlResultAnalysis(
            metric=metric,
            lineage=lineage,
            columns=state.execution_result.columns,
            rows=state.execution_result.rows,
        )

    return analyze


def present_live_result(result: BaseModel) -> DeterministicPresentation:
    if isinstance(result, ComparisonResult):
        return present_comparison(result)
    if isinstance(result, ContributionResult):
        return present_contribution(result)
    if isinstance(result, AnomalyResult):
        return present_anomaly(result)
    if not isinstance(result, SqlResultAnalysis):
        raise TypeError("unsupported deterministic result type")
    x_field = result.columns[0] if result.columns else "row"
    numeric = tuple(
        column
        for column in result.columns
        if any(
            isinstance(row.get(column), (int, float))
            and not isinstance(row.get(column), bool)
            for row in result.rows
        )
    )
    return DeterministicPresentation(
        analysis_type=result.analysis_type,
        metric_id=result.metric.metric_id,
        metric_definition_source=result.metric.definition_source,
        lineage=result.lineage,
        table=TableSpec(columns=result.columns, rows=result.rows),
        chart=ChartSpec(
            chart_type=ChartType.BAR,
            title=f"{result.metric.metric_id} query result",
            x_field=x_field,
            y_fields=numeric,
            data=result.rows,
            notes=("direct_sql_result_without_model_numeric_rewrite",),
        ),
        conclusion="结果来自只读 SQL；未使用模型改写数值或生成业务归因。",
        boundary_notes=("business_conclusion_not_independently_evaluated",),
    )


def _configuration(version: CandidateVersion, *, repair_enabled: bool = True):
    retrieval_hash = None
    if version in {CandidateVersion.RETRIEVAL_SQL, CandidateVersion.FULL_AGENT}:
        retrieval_hash = canonical_configuration_hash(
            {"retriever": "keyword_v1", "metric_top_k": 5, "schema_top_k": 8}
        )
    purpose = RunPurpose.MAIN_EXPERIMENT if repair_enabled else RunPurpose.ABLATION
    return ExperimentConfiguration(
        candidate_version=version,
        purpose=purpose,
        result_provenance=ResultProvenance.REAL_MODEL,
        sampling=SamplingConfiguration(
            provider="deepseek",
            model_id=MODEL_ID,
            temperature=0,
            top_p=1,
            random_seed=None,
            maximum_output_tokens=2048,
            per_call_timeout_seconds=60,
        ),
        prompt_sha256=canonical_configuration_hash(
            {"adapter": version.value, "prompt_contract": "evaluation_v1"}
        ),
        retrieval_configuration_sha256=retrieval_hash,
        adapter_configuration_sha256=canonical_configuration_hash(
            {"adapter": version.value, "repair_enabled": repair_enabled}
        ),
        external_api_authorized=True,
    )


def _full_machine(
    root: Path,
    clients: dict[str, BudgetedModelClient],
    *,
    max_repairs: int,
) -> AgentStateMachine:
    documents = build_documents(root)
    catalog = MetricCatalog.from_csv(
        root / "data/metadata/metric_dictionary.csv",
        root / "data/metadata/dimension_dictionary.csv",
    )
    return AgentStateMachine(WorkflowServices(
        root=root,
        database_path=root / "data/processed/olist.sqlite3",
        documents=documents,
        retriever=KeywordRetriever(documents),
        planner=AnalysisPlanner(
            clients["planning"],
            ModelConfig(MODEL_ID, temperature=0, timeout_seconds=60, max_tokens=1024),
            catalog,
            max_output_corrections=0,
            require_retrieval_grounding=True,
        ),
        sql_generator=SqlGenerator(
            clients["sql_generation"],
            ModelConfig(MODEL_ID, temperature=0, timeout_seconds=60, max_tokens=2048),
        ),
        repair_workflow=SqlRepairWorkflow(
            database_path=root / "data/processed/olist.sqlite3",
            repairer=SqlRepairer(
                clients["repair"],
                ModelConfig(MODEL_ID, temperature=0, timeout_seconds=60, max_tokens=2048),
            ),
            limits=RepairLimits(max_repair_attempts=max_repairs),
            response_source="real_deepseek_model_response",
        ),
        analyzer=build_live_analyzer(root),
        presenter=present_live_result,
    ))


def run_authorized_experiment(
    root: Path,
    output_root: Path,
    *,
    run_label: str = "day15-main-rerun1",
    budget_ledger_path: Path | None = None,
) -> dict[str, Any]:
    ledger = budget_ledger_path or (output_root / "api_budget_ledger.json")
    budget = ApiBudget(ledger)
    credentials = DeepSeekCredentials.from_environment()
    base = DeepSeekClient(credentials=credentials)
    clients = {
        stage: BudgetedModelClient(base, budget, stage)
        for stage in ("direct_sql", "retrieval_sql", "planning", "sql_generation", "repair")
    }
    context = build_direct_sql_public_context(root)
    config = ModelConfig(MODEL_ID, temperature=0, timeout_seconds=60, max_tokens=2048)
    documents = build_documents(root)
    adapters = {
        CandidateVersion.DIRECT_SQL: DirectSqlAdapter(clients["direct_sql"], config, context),
        CandidateVersion.RETRIEVAL_SQL: RetrievalSqlAdapter(
            clients["retrieval_sql"], config, context,
            KeywordRetriever(documents), "keyword_v1",
        ),
        CandidateVersion.FULL_AGENT: FullAgentAdapter(
            _full_machine(root, clients, max_repairs=2)
        ),
    }
    results = {}
    for version, adapter in adapters.items():
        manifest = build_run_manifest(
            root, _configuration(version), run_id=f"{run_label}-{version.value}"
        )
        directory = output_root / version.value
        batch = BatchRunner(root, manifest, adapter, directory).run()
        if not batch.complete or batch.seal is None:
            raise RuntimeError(f"incomplete real-model batch: {version.value}")
        scoring = directory / "scoring"
        submission, normalized_seal, _ = materialize_scoring_submission(
            root, manifest, batch.seal, scoring
        )
        report, summary, authorization = score_sealed_submission(
            root, manifest, submission, normalized_seal, scoring,
            root / batch.candidate_path,
        )
        results[version.value] = {
            "run_id": manifest.run_id,
            "purpose": manifest.configuration.purpose.value,
            "result_provenance": "real_model",
            "case_count": report.case_count,
            "case_contract_pass_count": report.case_contract_pass_count,
            "raw_seal_sha256": batch.seal.sha256,
            "normalized_seal_sha256": normalized_seal.sha256,
            "scoring_authorization_sha256": authorization.sealed_sha256,
            "overall": summary.overall.model_dump(mode="json"),
            "by_category": {
                key: value.model_dump(mode="json") for key, value in summary.by_category.items()
            },
            "operational": summary.operational.model_dump(mode="json"),
            "paths": {
                "raw": batch.candidate_path,
                "scored": (scoring / "scored_results.json").relative_to(root).as_posix(),
                "summary": (scoring / "aggregate_summary.json").relative_to(root).as_posix(),
            },
        }
    payload = {
        "report_schema_version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "authorization": {
            "authorized_by_user": True,
            "balance_disclosed_cny": 7,
            "maximum_cost_usd": budget.maximum_cost_usd,
            "usd_cny_reference_rate": budget.usd_cny_reference_rate,
            "maximum_cost_cny": budget.maximum_cost_usd * budget.usd_cny_reference_rate,
        },
        "model": MODEL_ID,
        "run_label": run_label,
        "prior_invalid_run_note": (
            "The first direct-SQL development run consumed 60 calls but produced adapter_exception "
            "records because the temporary evaluation snapshot omitted the updated ModelResponse "
            "telemetry fields. One additional call diagnosed the mismatch. No prompt, gold, or "
            "scoring rule was changed; the invalid artifacts remain separate."
        ),
        "thinking": "disabled",
        "temperature": 0,
        "random_seed": None,
        "random_seed_note": "not supported by current provider transport",
        "versions": results,
        "budget": json.loads(ledger.read_text(encoding="utf-8")),
    }
    (output_root / "live_experiment_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload
