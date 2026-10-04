"""Read-only, network-free acceptance check for the Model evaluation container image."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ecommerce_agent.evaluator import (
    SubmissionSource,
    build_scripted_self_test_outputs,
    evaluate_candidate_outputs,
)
from src.ecommerce_agent.evaluation_schema import (
    canonical_dataset_path,
    load_dataset,
    verify_dataset_content_sha256,
)


EXPECTED_PYTHON = "3.11.9"
EXPECTED_DATASET_VERSION = "1.0.0"
EXPECTED_DATASET_CONTENT_SHA256 = (
    "4ab5fa8c54ef830982bcb03828adb2133d20c68eda343d76ca059d5577d0c591"
)
EXPECTED_DATASET_FILE_SHA256 = (
    "cd77ed6d98d37be7c87aa773f37820ee8c8ec21117b875f2c7b02a253d8e7093"
)
EXPECTED_PUBLIC_MANIFEST_SHA256 = (
    "d44caf279d5fb15972845787e1664f066bd4333248dfda131efdc3c671a1dc6e"
)
EXPECTED_DATABASE_SHA256 = (
    "ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c"
)
EXPECTED_CATEGORY_COUNTS = {
    "aggregate_filter_join": 20,
    "multi_step": 10,
    "risk_ambiguous_unanswerable": 10,
    "single_metric": 20,
}
EXPECTED_RAW_SHA256 = {
    "archive.zip": "967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784",
    "olist_customers_dataset.csv": "983a422239e1712ded753b3bf9ecf47dc73f144d306029dcfa99e70a226883d2",
    "olist_geolocation_dataset.csv": "b514f6fc991b9566aeba02aa5d67e2c3630f034b60a0e05aa0d082a3b66d88d6",
    "olist_order_items_dataset.csv": "0bc4d068c4fe38cbb01bd90e8746e3c613fe7b4baef75fab7b0e329701c3e279",
    "olist_order_payments_dataset.csv": "4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0",
    "olist_order_reviews_dataset.csv": "012b61c7593e34f51fa614efdf802b9c7056ce6aae5307ddb93236e7cfc797d7",
    "olist_orders_dataset.csv": "8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf",
    "olist_products_dataset.csv": "3e6569628a17fbc75fd206ee357b59e20364b9afa90f5b6cd5b4d624c58aa9cc",
    "olist_sellers_dataset.csv": "1f643d2b950373b85735e7794b20986f528d7a000432e7c6f9bcbb44d0846a0e",
    "product_category_name_translation.csv": "a81f0d1f27b27e7293f761bc79e3ce8f348ee39c4b3ed3e49bde38f478586278",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def run_offline_check(root: Path) -> dict[str, Any]:
    """Verify immutable inputs and replay the private scorer without model calls."""
    started = datetime.now(timezone.utc)
    dataset_path = canonical_dataset_path(root)
    public_path = root / "data/evaluation/benchmark_v1/cases.public.jsonl"
    database_path = root / "data/processed/olist.sqlite3"
    dataset = load_dataset(dataset_path)
    public_rows = [
        json.loads(line)
        for line in public_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    raw_hashes = {
        path.name: _sha256(path)
        for path in sorted((root / "data/raw").iterdir())
        if path.is_file() and path.name != ".gitkeep"
    }
    oracle_outputs = build_scripted_self_test_outputs(root)
    scorer = evaluate_candidate_outputs(
        root,
        oracle_outputs,
        submission_source=SubmissionSource.SCRIPTED_EVALUATOR_SELF_TEST,
        candidate_model_runs=0,
        external_api_calls=0,
    )
    pip_check = _run([sys.executable, "-m", "pip", "check"], root)
    compileall = _run(
        [sys.executable, "-m", "compileall", "-q", "src", "tests", "streamlit_app.py"],
        root,
    )
    category_counts = dict(
        sorted(Counter(case.category.value for case in dataset.cases).items())
    )
    checks = {
        "python_exact": platform.python_version() == EXPECTED_PYTHON,
        "dataset_version": dataset.dataset_version == EXPECTED_DATASET_VERSION,
        "dataset_content_hash": (
            dataset.content_sha256 == EXPECTED_DATASET_CONTENT_SHA256
            and verify_dataset_content_sha256(dataset)
        ),
        "dataset_file_hash": _sha256(dataset_path) == EXPECTED_DATASET_FILE_SHA256,
        "public_manifest_hash": _sha256(public_path) == EXPECTED_PUBLIC_MANIFEST_SHA256,
        "public_manifest_shape": (
            len(public_rows) == 60
            and all(set(row) == {"case_id", "question"} for row in public_rows)
        ),
        "category_counts": category_counts == EXPECTED_CATEGORY_COUNTS,
        "database_hash": _sha256(database_path) == EXPECTED_DATABASE_SHA256,
        "raw_file_hashes": raw_hashes == EXPECTED_RAW_SHA256,
        "private_scorer_replay": (
            scorer.case_count == 60 and scorer.case_contract_pass_count == 60
        ),
        "pip_check": pip_check["returncode"] == 0,
        "compileall": compileall["returncode"] == 0,
    }
    finished = datetime.now(timezone.utc)
    return {
        "report_schema_version": "1.0.0",
        "report_kind": "offline_container_reproducibility_check",
        "accepted": all(checks.values()),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "source_commit": os.environ.get("SOURCE_COMMIT", "unknown"),
        "python_version": platform.python_version(),
        "dataset_version": dataset.dataset_version,
        "dataset_content_sha256": dataset.content_sha256,
        "case_count": len(dataset.cases),
        "category_counts": category_counts,
        "checks": checks,
        "pip_check": pip_check,
        "compileall": compileall,
        "candidate_system": None,
        "candidate_model_runs": 0,
        "external_api_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost": 0,
        "measurement_scope": (
            "immutable-input verification and scripted private-scorer replay; "
            "not candidate accuracy, load testing, security certification, or production deployment"
        ),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    report = run_offline_check(root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["accepted"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
