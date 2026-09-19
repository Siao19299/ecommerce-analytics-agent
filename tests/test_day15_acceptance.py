import shutil
from pathlib import Path
from uuid import uuid4

from src.ecommerce_agent.day15_acceptance import (
    build_engineering_results,
    run_three_version_offline_smoke,
)


ROOT = Path(__file__).resolve().parents[1]


def test_three_versions_use_same_sixty_public_cases_and_score_only_after_seal():
    output = ROOT / "data/processed/day15-acceptance-tests" / uuid4().hex
    try:
        results = run_three_version_offline_smoke(ROOT, output)
        assert set(results) == {"direct_sql", "retrieval_sql", "full_agent"}
        for result in results.values():
            assert result["public_case_count"] == 60
            assert result["raw_record_count"] == 60
            assert result["normalized_record_count"] == 60
            assert result["normalized_seal_sha256"] == result["scoring_authorization_sha256"]
            assert result["result_provenance"] == "fake_model"
            assert result["empirical_model_result"] is False
            assert result["comparative_claim_allowed"] is False
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_structured_results_keep_smoke_and_real_model_evidence_separate():
    payload = build_engineering_results(
        ROOT,
        {
            name: {"empirical_model_result": False}
            for name in ("direct_sql", "retrieval_sql", "full_agent")
        },
    )
    real = payload["real_model_main_experiment"]
    assert real["runs"] == 3
    assert real["external_api_calls"] == 229
    assert real["input_tokens"] == 750124
    assert real["output_tokens"] == 33382
    assert real["conservative_cost_usd"] > 0
    assert payload["business_accuracy"]["accuracy"] is None
    assert payload["failure_diagnostics"]["scenario_count"] >= 10
    assert payload["registered_ablations"][0]["status"] == "completed_real_model_main_comparison"
    assert payload["failure_diagnostics"]["real_model_failure_count"] == 127
