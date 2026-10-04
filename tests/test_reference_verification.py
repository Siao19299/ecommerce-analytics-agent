import json
from pathlib import Path

from src.ecommerce_agent.artifact_paths import resolve_artifact_path
from src.ecommerce_agent.result_comparison import compare_results
from src.ecommerce_agent.evaluation_schema import load_dataset
from src.ecommerce_agent.sql_generation import execute_read_only_query
from src.ecommerce_agent.sql_safety import build_global_sql_policy


PROJECT_ROOT = Path(__file__).parents[1]


def test_all_standard_sql_and_saved_reference_rows_were_verified():
    report = json.loads(
        (PROJECT_ROOT / "docs/reports/reference_verification.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["standard_sql_count"] == 52
    assert report["sql_hash_verified_count"] == 52
    assert report["named_parameter_contract_verified_count"] == 52
    assert report["safety_gate_pass_count"] == 52
    assert report["real_sqlite_reference_execution_count"] == 50
    assert report["reference_result_comparison_pass_count"] == 50
    assert report["status_only_standard_sql_count"] == 2
    assert report["candidate_model_runs"] == 0
    assert report["external_api_calls"] == 0
    assert report["database_unchanged"] is True
    assert report["raw_files_unchanged"] is True


def test_different_sql_text_can_pass_by_equivalent_execution_result():
    dataset = load_dataset(
        PROJECT_ROOT / "data/evaluation/benchmark_v1/dataset.v1.draft.json"
    )
    case = next(case for case in dataset.cases if case.case_id == "D14_SM_001")
    saved = json.loads(
        (resolve_artifact_path(PROJECT_ROOT, case.result_reference.path)).read_text(encoding="utf-8")
    )
    equivalent_sql = """SELECT SUM(CASE WHEN o.order_status = :delivered_status THEN 1 ELSE 0 END) AS delivered_order_count
FROM fact_orders AS o
WHERE o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive"""
    parameters = dict(case.sql_reference.named_parameters)
    parameters["delivered_status"] = "delivered"
    execution = execute_read_only_query(
        PROJECT_ROOT / "data/processed/olist.sqlite3",
        equivalent_sql,
        parameters,
        safety_policy=build_global_sql_policy(PROJECT_ROOT),
    )
    assert equivalent_sql.strip() != (
        resolve_artifact_path(PROJECT_ROOT, case.sql_reference.path)
    ).read_text(encoding="utf-8").strip()
    comparison = compare_results(
        expected_columns=saved["columns"],
        expected_rows=saved["rows"],
        actual_columns=execution.columns,
        actual_rows=execution.rows,
        rules=case.comparison_rules,
    )
    assert execution.is_success is True
    assert comparison.result_correct is True
