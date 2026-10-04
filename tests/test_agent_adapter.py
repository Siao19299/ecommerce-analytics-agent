import hashlib
from pathlib import Path

from src.ecommerce_agent.workflow_benchmark import (
    MONTHLY_SQL,
    PLAN,
    QUESTION,
    REPAIRABLE_SQL,
    _machine,
)
from src.ecommerce_agent.agent_adapter import (
    AgentSafetyOutcome,
    FullAgentAdapter,
    FullAgentAdapterFailure,
)
from src.ecommerce_agent.reproducibility import PublicCase


PROJECT_ROOT = Path(__file__).parents[1]
DATABASE = PROJECT_ROOT / "data/processed/olist.sqlite3"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(case_id="D14_MS_001", question=QUESTION):
    return PublicCase(case_id=case_id, question=question)


def test_full_agent_projects_real_state_machine_success_and_lineage():
    before = _sha256(DATABASE)
    adapter = FullAgentAdapter(_machine(PROJECT_ROOT))

    record = adapter.run_case(_case(), run_id="agent-success")

    assert record.workflow_status == "succeeded"
    assert record.stop_reason == "completed"
    assert record.safety_outcome is AgentSafetyOutcome.ACCEPTED
    assert record.execution_started is True
    assert record.execution_succeeded is True
    assert record.sql_attempt_count == 1
    assert record.repair_attempt_count == 0
    assert record.generation_model_call_count == 2
    assert record.repair_model_call_count == 0
    assert record.model_call_count == 2
    assert record.calculation_parent_run_id == "agent-success"
    assert record.calculation_source_sql_attempt == 1
    assert record.source_result_columns == ("purchase_month", "monthly_gmv")
    assert record.result_columns == ("role", "period", "value", "completeness")
    assert record.final_answer is not None
    assert record.workflow_snapshot_sha256 is not None
    assert "finalization" in record.node_sequence
    assert _sha256(DATABASE) == before


def test_full_agent_clarification_generates_no_sql_and_never_enters_sqlite():
    machine = _machine(
        PROJECT_ROOT,
        planning_payload={
            "status": "needs_clarification",
            "clarification_question": "请明确销售额口径。",
        },
    )

    record = FullAgentAdapter(machine).run_case(
        _case("D14_RU_001", "2018 年 6 月销售额是多少？"),
        run_id="agent-clarify",
    )

    assert record.workflow_status == "needs_clarification"
    assert record.stop_reason == "clarification_required"
    assert record.initial_generated_sql is None
    assert record.final_sql is None
    assert record.execution_started is False
    assert record.sql_attempt_count == 0
    assert record.repair_attempt_count == 0
    assert record.generation_model_call_count == 1


def test_full_agent_safety_rejection_never_executes_or_repairs():
    record = FullAgentAdapter(_machine(PROJECT_ROOT, sql="DELETE FROM fact_orders")).run_case(
        _case("D14_RU_006"), run_id="agent-safety"
    )

    assert record.workflow_status == "safety_rejected"
    assert record.safety_outcome is AgentSafetyOutcome.REJECTED
    assert record.execution_started is False
    assert record.execution_succeeded is False
    assert record.sql_attempt_count == 1
    assert record.repair_attempt_count == 0
    assert record.repair_model_call_count == 0


def test_full_agent_keeps_sql_repair_and_transport_attempts_separate():
    machine = _machine(
        PROJECT_ROOT,
        planning_payload=PLAN,
        sql=REPAIRABLE_SQL,
        repair_sqls=(MONTHLY_SQL,),
    )

    record = FullAgentAdapter(machine).run_case(
        _case(), run_id="agent-repair"
    )

    assert record.workflow_status == "succeeded"
    assert record.sql_attempt_count == 2
    assert record.repair_attempt_count == 1
    assert record.repair_model_call_count == 1
    assert record.generation_model_call_count == 2
    assert record.final_sql == MONTHLY_SQL
    assert record.initial_generated_sql == REPAIRABLE_SQL
    assert record.calculation_source_sql_attempt == 2


def test_full_agent_does_not_guess_tokens_cost_or_model_latency():
    record = FullAgentAdapter(_machine(PROJECT_ROOT)).run_case(
        _case(), run_id="agent-missing-usage"
    )

    assert record.prompt_tokens is None
    assert record.completion_tokens is None
    assert record.model_latency_ms is None
    assert record.cost is None
    assert record.cost_currency is None
    assert record.end_to_end_latency_ms > 0


def test_full_agent_maps_runner_boundary_exception_without_details():
    class BrokenRunner:
        def run(self, question, *, run_id=None):
            raise RuntimeError("secret local path and credential")

    record = FullAgentAdapter(BrokenRunner()).run_case(
        _case(), run_id="agent-broken"
    )

    assert record.adapter_failure is FullAgentAdapterFailure.RUNNER_EXCEPTION
    assert record.workflow_status is None
    assert record.workflow_snapshot is None
    assert record.model_call_count == 0
