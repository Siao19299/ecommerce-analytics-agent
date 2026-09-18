"""Offline verification of Day 14 SQL assets and saved reference rows."""

from __future__ import annotations

import json
from pathlib import Path

from src.ecommerce_agent.day14_comparison import compare_results
from src.ecommerce_agent.day14_schema import (
    ComparisonRules,
    DatasetCategory,
    NumericTolerance,
    OrderKey,
    RowComparison,
    SortDirection,
    SqlReferenceKind,
    canonical_dataset_path,
    load_dataset,
    validate_named_parameter_contract,
)
from src.ecommerce_agent.day14_single_metric import (
    DATABASE_RELATIVE_PATH,
    _raw_hashes,
    _sha256_file,
)
from src.ecommerce_agent.sql_generation import execute_read_only_query
from src.ecommerce_agent.sql_safety import build_global_sql_policy, validate_sql_safety


def _source_rules(columns: tuple[str, ...]) -> ComparisonRules:
    return ComparisonRules(
        expected_columns=columns,
        row_comparison=RowComparison.ORDERED,
        order_keys=(OrderKey(column=columns[0], direction=SortDirection.ASCENDING),),
        numeric_tolerance=NumericTolerance(absolute=1e-6, relative=1e-9),
    )


def verify_references(root: Path) -> dict[str, object]:
    dataset = load_dataset(canonical_dataset_path(root))
    database = root / DATABASE_RELATIVE_PATH
    database_before = _sha256_file(database)
    raw_before = _raw_hashes(root)
    policy = build_global_sql_policy(root, max_rows=1000)
    records: list[dict[str, object]] = []

    for case in dataset.cases:
        if case.sql_reference.kind is not SqlReferenceKind.STANDARD_SQL_FILE:
            continue
        sql_path = root / case.sql_reference.path
        sql_text = sql_path.read_text(encoding="utf-8")
        sql_hash_correct = _sha256_file(sql_path) == case.sql_reference.sha256
        validate_named_parameter_contract(
            sql_text,
            case.sql_reference.named_parameters,
        )
        safety = validate_sql_safety(sql_text, policy)
        if not sql_hash_correct or not safety.is_safe:
            raise RuntimeError(f"{case.case_id} reference SQL integrity/safety failure")

        record: dict[str, object] = {
            "case_id": case.case_id,
            "category": case.category.value,
            "sql_sha256_verified": sql_hash_correct,
            "named_parameter_contract_verified": True,
            "safety_gate_accepted": safety.is_safe,
            "execution_expected": case.result_reference.path is not None,
            "execution_started": False,
            "result_comparison_correct": None,
        }
        if case.result_reference.path is not None:
            result_path = root / case.result_reference.path
            if _sha256_file(result_path) != case.result_reference.sha256:
                raise RuntimeError(f"{case.case_id} result reference hash mismatch")
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            execution = execute_read_only_query(
                database,
                sql_text,
                case.sql_reference.named_parameters,
                safety_policy=policy,
            )
            if not execution.is_success or not execution.execution_started:
                raise RuntimeError(f"{case.case_id} reference SQL did not execute")
            if case.category is DatasetCategory.MULTI_STEP:
                expected = saved["source_sql"]
                expected_columns = tuple(expected["columns"])
                rules = _source_rules(expected_columns)
            else:
                expected = saved
                expected_columns = tuple(expected["columns"])
                rules = case.comparison_rules
            comparison = compare_results(
                expected_columns=expected_columns,
                expected_rows=expected["rows"],
                actual_columns=execution.columns,
                actual_rows=execution.rows,
                rules=rules,
            )
            if not comparison.result_correct:
                raise RuntimeError(
                    f"{case.case_id} saved rows no longer match SQLite: "
                    f"{comparison.to_dict()}"
                )
            record.update(
                {
                    "execution_started": execution.execution_started,
                    "rows_truncated": execution.rows_truncated,
                    "result_comparison_correct": comparison.result_correct,
                    "comparison": comparison.to_dict(),
                }
            )
        records.append(record)

    database_after = _sha256_file(database)
    raw_after = _raw_hashes(root)
    report: dict[str, object] = {
        "measurement_scope": (
            "offline_reference_integrity_and_self_consistency; not_candidate_accuracy; "
            "not_independent_business_accuracy"
        ),
        "dataset_version": dataset.dataset_version,
        "standard_sql_count": len(records),
        "sql_hash_verified_count": sum(bool(row["sql_sha256_verified"]) for row in records),
        "named_parameter_contract_verified_count": sum(
            bool(row["named_parameter_contract_verified"]) for row in records
        ),
        "safety_gate_pass_count": sum(bool(row["safety_gate_accepted"]) for row in records),
        "real_sqlite_reference_execution_count": sum(
            bool(row["execution_started"]) for row in records
        ),
        "reference_result_comparison_pass_count": sum(
            row["result_comparison_correct"] is True for row in records
        ),
        "status_only_standard_sql_count": sum(
            row["result_comparison_correct"] is None for row in records
        ),
        "independent_business_reference_count": 0,
        "candidate_model_runs": 0,
        "external_api_calls": 0,
        "database_sha256_before": database_before,
        "database_sha256_after": database_after,
        "database_unchanged": database_before == database_after,
        "raw_file_hashes_before": raw_before,
        "raw_file_hashes_after": raw_after,
        "raw_files_unchanged": raw_before == raw_after,
        "cases": records,
    }
    output = root / "docs/DAY14_REFERENCE_VERIFICATION.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    root = Path(__file__).parents[2]
    report = verify_references(root)
    print(
        "Day 14 SQL references: "
        f"{report['safety_gate_pass_count']}/{report['standard_sql_count']} safe; "
        f"{report['reference_result_comparison_pass_count']} SQLite result comparisons passed"
    )
    print("Candidate-model and external API calls: 0")


if __name__ == "__main__":
    main()
