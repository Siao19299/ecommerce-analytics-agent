import json
from pathlib import Path

import pytest

from src.ecommerce_agent.experiment_protocol import CandidateVersion
from src.ecommerce_agent.reproducibility import (
    ExperimentConfiguration,
    SamplingConfiguration,
    authorize_scoring,
    build_run_manifest,
    canonical_configuration_hash,
    load_public_cases,
    private_reference_paths,
    seal_candidate_artifact,
)


PROJECT_ROOT = Path(__file__).parents[1]
HASH = "0" * 64


def _configuration(version=CandidateVersion.DIRECT_SQL):
    return ExperimentConfiguration(
        candidate_version=version,
        purpose="smoke",
        result_provenance="fake_model",
        sampling=SamplingConfiguration(
            provider="offline",
            model_id="scripted-evaluation",
            temperature=0,
            top_p=1,
            random_seed=15,
            maximum_output_tokens=1024,
            per_call_timeout_seconds=30,
        ),
        prompt_sha256=HASH,
        retrieval_configuration_sha256=(
            HASH if version is not CandidateVersion.DIRECT_SQL else None
        ),
        adapter_configuration_sha256=HASH,
    )


def test_public_loader_exposes_only_case_id_and_question():
    cases = load_public_cases(PROJECT_ROOT)
    assert len(cases) == 60
    assert all(set(case.model_dump()) == {"case_id", "question"} for case in cases)
    serialized = json.dumps([case.model_dump() for case in cases], ensure_ascii=False)
    for forbidden in (
        "sql_reference",
        "expected_workflow_status",
        "comparison_rules",
        "business_reference_status",
        "references/sql",
    ):
        assert forbidden not in serialized


def test_retrieval_hash_is_required_exactly_for_versions_that_retrieve():
    _configuration(CandidateVersion.DIRECT_SQL)
    _configuration(CandidateVersion.RETRIEVAL_SQL)
    _configuration(CandidateVersion.FULL_AGENT)

    payload = _configuration().model_dump()
    payload["retrieval_configuration_sha256"] = HASH
    with pytest.raises(ValueError, match="retrieval configuration hash"):
        ExperimentConfiguration.model_validate(payload)


def test_real_model_metadata_requires_explicit_authorization():
    payload = _configuration().model_dump()
    payload["result_provenance"] = "real_model"
    payload["sampling"]["provider"] = "example-provider"
    with pytest.raises(ValueError, match="explicit API authorization"):
        ExperimentConfiguration.model_validate(payload)


def test_manifest_records_environment_without_loading_private_references():
    manifest = build_run_manifest(PROJECT_ROOT, _configuration(), run_id="evaluation-smoke")
    assert manifest.public_case_count == 60
    assert manifest.candidate_received_reference_assets is False
    assert manifest.private_references_loaded is False
    assert manifest.api_key_values_read == 0
    assert manifest.runtime.python_version == "3.11.9"
    assert manifest.runtime.executable_is_project_venv is True


def test_private_scoring_requires_complete_unchanged_sealed_artifact(tmp_path):
    artifact = PROJECT_ROOT / "data" / "processed" / "evaluation-test-candidates.jsonl"
    try:
        artifact.write_text("{}\n" * 60, encoding="utf-8")
        seal = seal_candidate_artifact(PROJECT_ROOT, artifact, run_id="sealed-run")
        assert seal.complete_public_case_set is True
        authorization = authorize_scoring(PROJECT_ROOT, seal)
        dataset, references = private_reference_paths(PROJECT_ROOT, authorization)
        assert dataset.name == "dataset.v1.json"
        assert references.name == "references"

        artifact.write_text("{}\n" * 59, encoding="utf-8")
        with pytest.raises(ValueError, match="changed after sealing"):
            authorize_scoring(PROJECT_ROOT, seal)
    finally:
        artifact.unlink(missing_ok=True)


def test_incomplete_artifact_cannot_authorize_scoring():
    artifact = PROJECT_ROOT / "data" / "processed" / "evaluation-test-incomplete.jsonl"
    try:
        artifact.write_text("{}\n", encoding="utf-8")
        seal = seal_candidate_artifact(PROJECT_ROOT, artifact, run_id="incomplete")
        assert seal.complete_public_case_set is False
        with pytest.raises(ValueError, match="incomplete"):
            authorize_scoring(PROJECT_ROOT, seal)
    finally:
        artifact.unlink(missing_ok=True)


def test_configuration_hash_is_canonical():
    assert canonical_configuration_hash({"b": 2, "a": 1}) == canonical_configuration_hash(
        {"a": 1, "b": 2}
    )
