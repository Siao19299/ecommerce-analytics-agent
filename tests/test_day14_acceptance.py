from pathlib import Path

from src.ecommerce_agent.day14_acceptance import scan_sensitive_artifacts
from src.ecommerce_agent.day14_schema import (
    DatasetState,
    load_dataset,
    verify_dataset_content_sha256,
)


PROJECT_ROOT = Path(__file__).parents[1]


def test_frozen_dataset_has_verified_content_hash_and_exact_distribution():
    dataset = load_dataset(
        PROJECT_ROOT / "data/evaluation/day14/dataset.v1.json"
    )
    assert dataset.state is DatasetState.FROZEN
    assert dataset.dataset_version == "1.0.0"
    assert len(dataset.cases) == 60
    assert verify_dataset_content_sha256(dataset) is True


def test_frozen_cases_equal_reviewed_draft_cases_before_release():
    frozen = load_dataset(PROJECT_ROOT / "data/evaluation/day14/dataset.v1.json")
    draft = load_dataset(
        PROJECT_ROOT / "data/evaluation/day14/dataset.v1.draft.json"
    )
    assert frozen.cases == draft.cases


def test_sensitive_artifact_scan_and_blind_manifest_pass():
    result = scan_sensitive_artifacts(PROJECT_ROOT)
    assert result["passed"] is True
    assert result["finding_count"] == 0
    assert result["public_manifest_line_count"] == 60
    assert result["public_manifest_only_case_id_and_question"] is True
    assert result["api_key_values_read"] == 0
