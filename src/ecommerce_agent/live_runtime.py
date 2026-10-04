"""Production-style assembly for the local interactive analytics demo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.repair_limits import RepairLimits
from src.ecommerce_agent.repair_workflow import SqlRepairWorkflow
from src.ecommerce_agent.sql_repair import SqlRepairer
from src.ecommerce_agent.workflow import WorkflowServices, AgentStateMachine
from src.ecommerce_agent.api import create_app
from src.ecommerce_agent.analysis_service import AgentService
from src.ecommerce_agent.model_budget import ApiBudget, BudgetedModelClient
from src.ecommerce_agent.model_experiment import (
    build_live_analyzer,
    present_live_result,
)
from src.ecommerce_agent.deepseek_client import DeepSeekClient, DeepSeekCredentials
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    ModelClient,
    ModelConfig,
    RetryingModelClient,
    RetryPolicy,
)
from src.ecommerce_agent.retrieval import KeywordRetriever, build_documents
from src.ecommerce_agent.sql_generation import SqlGenerator


@dataclass(frozen=True)
class LiveRuntimeSettings:
    model_id: str = "deepseek-flash"
    temperature: float = 0.0
    timeout_seconds: float = 60.0
    planning_max_tokens: int = 1024
    sql_max_tokens: int = 2048
    maximum_repair_attempts: int = 2
    maximum_http_calls: int = 40
    maximum_input_tokens: int = 400_000
    maximum_output_tokens: int = 40_000
    maximum_cost_usd: float = 0.15

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("model_id must not be blank")
        if self.maximum_http_calls < 1:
            raise ValueError("maximum_http_calls must be positive")
        if self.maximum_cost_usd <= 0:
            raise ValueError("maximum_cost_usd must be positive")


def settings_from_environment() -> LiveRuntimeSettings:
    """Read non-secret demo controls; the API key is handled separately."""

    return LiveRuntimeSettings(
        model_id=os.getenv("ECOMMERCE_AGENT_MODEL", "deepseek-flash"),
        maximum_http_calls=int(os.getenv("ECOMMERCE_AGENT_MAX_HTTP_CALLS", "40")),
        maximum_cost_usd=float(os.getenv("ECOMMERCE_AGENT_MAX_COST_USD", "0.15")),
    )


def build_live_machine(
    root: Path,
    base_client: ModelClient,
    *,
    settings: LiveRuntimeSettings | None = None,
    ledger_path: Path | None = None,
) -> AgentStateMachine:
    """Assemble existing components without duplicating workflow behavior."""

    settings = settings or LiveRuntimeSettings()
    ledger_path = ledger_path or (
        root / "data/processed/live_demo/api_budget_ledger.json"
    )
    budget = ApiBudget(
        ledger_path,
        maximum_http_calls=settings.maximum_http_calls,
        maximum_input_tokens=settings.maximum_input_tokens,
        maximum_output_tokens=settings.maximum_output_tokens,
        maximum_cost_usd=settings.maximum_cost_usd,
    )
    clients = {
        stage: RetryingModelClient(
            BudgetedModelClient(base_client, budget, stage),
            RetryPolicy(max_attempts=2, initial_backoff_seconds=1),
        )
        for stage in ("planning", "sql_generation", "repair")
    }
    documents = build_documents(root)
    catalog = MetricCatalog.from_csv(
        root / "data/metadata/metric_dictionary.csv",
        root / "data/metadata/dimension_dictionary.csv",
    )
    services = WorkflowServices(
        root=root,
        database_path=root / "data/processed/olist.sqlite3",
        documents=documents,
        retriever=KeywordRetriever(documents),
        planner=AnalysisPlanner(
            clients["planning"],
            ModelConfig(
                settings.model_id,
                temperature=settings.temperature,
                timeout_seconds=settings.timeout_seconds,
                max_tokens=settings.planning_max_tokens,
            ),
            catalog,
            max_output_corrections=0,
            require_retrieval_grounding=True,
        ),
        sql_generator=SqlGenerator(
            clients["sql_generation"],
            ModelConfig(
                settings.model_id,
                temperature=settings.temperature,
                timeout_seconds=settings.timeout_seconds,
                max_tokens=settings.sql_max_tokens,
            ),
        ),
        repair_workflow=SqlRepairWorkflow(
            database_path=root / "data/processed/olist.sqlite3",
            repairer=SqlRepairer(
                clients["repair"],
                ModelConfig(
                    settings.model_id,
                    temperature=settings.temperature,
                    timeout_seconds=settings.timeout_seconds,
                    max_tokens=settings.sql_max_tokens,
                ),
            ),
            limits=RepairLimits(
                max_repair_attempts=settings.maximum_repair_attempts
            ),
            response_source="interactive_deepseek_model_response",
        ),
        analyzer=build_live_analyzer(root),
        presenter=present_live_result,
    )
    return AgentStateMachine(services)


def create_live_app(
    *,
    root: Path | None = None,
    base_client: ModelClient | None = None,
    settings: LiveRuntimeSettings | None = None,
    ledger_path: Path | None = None,
) -> FastAPI:
    """FastAPI factory used by Uvicorn and injectable offline tests."""

    root = (root or Path(__file__).resolve().parents[2]).resolve()
    if base_client is None:
        base_client = DeepSeekClient(
            credentials=DeepSeekCredentials.from_environment()
        )
    machine = build_live_machine(
        root,
        base_client,
        settings=settings or settings_from_environment(),
        ledger_path=ledger_path,
    )
    app = create_app(AgentService(machine))
    app.title = "E-commerce Analytics Agent - Live Demo"
    app.version = "0.15.0"
    return app
