import json
from pathlib import Path

from src.ecommerce_agent.artifact_paths import resolve_artifact_path
from types import SimpleNamespace

from src.ecommerce_agent.analysis_plan import AnalysisPlan, TimeRange, TimeRangeMode
from src.ecommerce_agent.workflow_state import WorkflowState
from src.ecommerce_agent.evaluation_schema import canonical_dataset_path, load_dataset
from src.ecommerce_agent.model_experiment import build_live_analyzer, present_live_result
from src.ecommerce_agent.sql_generation import QueryExecutionResult


ROOT = Path(__file__).resolve().parents[1]


def test_live_deterministic_analyzer_reproduces_all_ten_frozen_multistep_results():
    dataset = load_dataset(canonical_dataset_path(ROOT))
    analyzer = build_live_analyzer(ROOT)
    for case in [item for item in dataset.cases if "_MS_" in item.case_id]:
        saved = json.loads(resolve_artifact_path(ROOT, case.result_reference.path).read_text(encoding="utf-8"))
        source = saved["source_sql"]
        state = WorkflowState(run_id=f"test-{case.case_id}", question=case.question)
        state.analysis_plan = AnalysisPlan(
            metrics=[case.metric_ids[0]],
            dimensions=[],
            filters=[],
            time_range=(
                TimeRange(
                    mode=TimeRangeMode.BOUNDED,
                    start_date=case.time_scope.start_date,
                    end_date=case.time_scope.end_date_exclusive,
                )
                if case.time_scope.start_date is not None
                else TimeRange(mode=TimeRangeMode.ALL_DATA)
            ),
        )
        state.execution_result = QueryExecutionResult(
            columns=tuple(source["columns"]),
            rows=tuple(source["rows"]),
            execution_started=True,
        )
        state.sql_attempt_trace = SimpleNamespace(
            attempts=(SimpleNamespace(sql_attempt=1),)
        )
        result = analyzer(state)
        presentation = present_live_result(result)
        assert getattr(result.calculation_status, "value", result.calculation_status) == case.expected_calculation_status.value
        assert presentation.table.columns == tuple(saved["columns"])
        assert presentation.table.rows == tuple(saved["rows"])
