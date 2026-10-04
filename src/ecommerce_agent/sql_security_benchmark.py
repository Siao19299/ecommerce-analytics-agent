"""Run assistant-authored SQL security safety cases against the real local SQLite DB."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.ecommerce_agent.sql_generation import execute_read_only_query
from src.ecommerce_agent.sql_safety import SqlSafetyPolicy, load_global_schema


EXPECTED_OUTCOMES = {
    "succeeded",
    "succeeded_truncated",
    "safety_rejected",
    "timeout",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_cases(path: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = payload["source_disclosure"]
    cases = payload["cases"]
    if len(cases) < 10:
        raise ValueError("SQL security 安全批次至少需要 10 个案例")
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("SQL security case_id 必须唯一")
    if any(case["expected_outcome"] not in EXPECTED_OUTCOMES for case in cases):
        raise ValueError("SQL security 包含未知 expected_outcome")
    return source, cases


def _outcome(result) -> str:
    if result.is_success:
        return "succeeded_truncated" if result.rows_truncated else "succeeded"
    if result.error_type is not None and result.error_type.value == "timeout":
        return "timeout"
    if result.error_type is not None and result.error_type.value == "safety":
        return "safety_rejected"
    return f"unexpected_{result.error_type.value if result.error_type else 'none'}"


def _compact_trace(trace) -> dict[str, Any] | None:
    if trace is None:
        return None
    payload = trace.to_dict()
    policy = payload.pop("policy")
    payload["policy"] = {
        "scope_rule": policy["scope_rule"],
        "plan_schema": policy["plan_schema"],
        "effective_schema": policy["effective_schema"],
        "max_rows": policy["max_rows"],
        "timeout_seconds": policy["timeout_seconds"],
        "progress_handler_steps": policy["progress_handler_steps"],
    }
    return payload


def run_benchmark(root: Path, database_path: Path) -> dict[str, Any]:
    fixture_path = root / "tests" / "fixtures" / "security" / "security_cases.json"
    source, cases = load_cases(fixture_path)
    global_schema = load_global_schema(root)
    database_hash_before = _sha256(database_path)
    records = []
    for case in cases:
        plan_schema = {
            table: global_schema[table] for table in case["plan_tables"]
        }
        policy = SqlSafetyPolicy(
            global_schema=global_schema,
            plan_schema=plan_schema,
            max_rows=case.get("max_rows", 1000),
            timeout_seconds=case.get("timeout_seconds", 10.0),
            progress_handler_steps=case.get("progress_handler_steps", 1000),
        )
        result = execute_read_only_query(
            database_path,
            case["sql"],
            safety_policy=policy,
        )
        observed = _outcome(result)
        records.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "case_source": source,
                "sql": case["sql"],
                "expected_outcome": case["expected_outcome"],
                "observed_outcome": observed,
                "expectation_met": observed == case["expected_outcome"],
                "execution_started": result.execution_started,
                "error_type": (
                    result.error_type.value if result.error_type else None
                ),
                "error_message": result.error_message,
                "safety_trace": _compact_trace(result.safety_trace),
                "returned_row_count": len(result.rows),
                "rows_truncated": result.rows_truncated,
                "row_limit": result.row_limit,
                "timeout_seconds": result.timeout_seconds,
                "rows_preview": list(result.rows[:5]),
            }
        )
    database_hash_after = _sha256(database_path)
    report = {
        "measurement_scope": (
            "offline_real_sqlite_execution_with_assistant_authored_"
            "mechanical_security_cases; no_model_or_external_api_calls"
        ),
        "case_source": source,
        "fixture_sha256": _sha256(fixture_path),
        "database_path": "data/processed/olist.sqlite3",
        "global_schema": {
            table: sorted(columns)
            for table, columns in sorted(global_schema.items())
        },
        "database_sha256_before": database_hash_before,
        "database_sha256_after": database_hash_after,
        "database_unchanged": database_hash_before == database_hash_after,
        "case_count": len(records),
        "expectation_met_count": sum(r["expectation_met"] for r in records),
        "observed_outcome_counts": dict(
            sorted(Counter(r["observed_outcome"] for r in records).items())
        ),
        "safety_rejections_entering_sqlite": sum(
            r["execution_started"]
            for r in records
            if r["observed_outcome"] == "safety_rejected"
        ),
        "cases": records,
    }
    if not report["database_unchanged"]:
        raise RuntimeError("SQL security 安全批次修改了数据库文件")
    failures = [r["case_id"] for r in records if not r["expectation_met"]]
    if failures:
        raise RuntimeError(f"SQL security 安全批次未符合预期：{failures}")
    output = root / "docs" / "reports/sql_security.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    args = parser.parse_args()
    root = Path(__file__).parents[2]
    database_path = args.database or root / "data" / "processed" / "olist.sqlite3"
    report = run_benchmark(root, database_path)
    print(
        f"SQL security cases: {report['case_count']}; "
        f"expectations met: {report['expectation_met_count']}"
    )
    print(f"Outcomes: {report['observed_outcome_counts']}")
    print(
        "Safety rejections entering SQLite: "
        f"{report['safety_rejections_entering_sqlite']}"
    )
    print(f"Database unchanged: {report['database_unchanged']}")


if __name__ == "__main__":
    main()
