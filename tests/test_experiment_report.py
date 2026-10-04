import json
from pathlib import Path

from src.ecommerce_agent.experiment_report import build_live_delivery


ROOT = Path(__file__).resolve().parents[1]


def test_live_delivery_rebuilds_from_sealed_per_case_records():
    run_directory = ROOT / "data/processed/day15/live_main_rerun1"
    if not run_directory.exists():
        return
    payload = build_live_delivery(ROOT)
    assert payload["result_provenance"] == "real_model"
    assert len(payload["versions"]) == 3
    assert len(payload["paired_comparisons"]) == 12
    assert payload["empirical_failure_count"] == 127
    assert len(payload["representative_empirical_failures"]) >= 10
    assert payload["provider_console_intermediate_snapshot"]["final_billing_snapshot"] is False
    written = json.loads((ROOT / "docs/reports/model_comparison.json").read_text(encoding="utf-8"))
    assert written["source_run_label"] == "day15-main-rerun1"
