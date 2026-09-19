import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from src.ecommerce_agent.day15_batch_runner import Day15BatchRunner
from src.ecommerce_agent.day15_protocol import CandidateVersion
from src.ecommerce_agent.day15_reproducibility import (
    ExperimentConfiguration,
    SamplingConfiguration,
    build_run_manifest,
)


PROJECT_ROOT = Path(__file__).parents[1]
HASH = "0" * 64


class StubOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    run_id: str
    marker: str = "offline-stub"


class StubAdapter:
    candidate_version = CandidateVersion.DIRECT_SQL

    def __init__(self):
        self.calls = []

    def run_case(self, case, *, run_id):
        self.calls.append(case.case_id)
        return StubOutput(case_id=case.case_id, run_id=run_id)


def _manifest(run_id: str):
    configuration = ExperimentConfiguration(
        candidate_version="direct_sql",
        purpose="smoke",
        result_provenance="fake_model",
        sampling=SamplingConfiguration(
            provider="offline",
            model_id="batch-stub",
            temperature=0,
            top_p=1,
            random_seed=15,
            maximum_output_tokens=128,
            per_call_timeout_seconds=5,
        ),
        prompt_sha256=HASH,
        adapter_configuration_sha256=HASH,
    )
    return build_run_manifest(PROJECT_ROOT, configuration, run_id=run_id)


@pytest.fixture
def output_directory():
    path = PROJECT_ROOT / "data" / "processed" / "day15-tests" / uuid4().hex
    yield path
    if path.exists():
        shutil.rmtree(path)


def test_batch_runner_uses_all_public_cases_in_order_and_seals(output_directory):
    adapter = StubAdapter()
    runner = Day15BatchRunner(
        PROJECT_ROOT, _manifest("batch-complete"), adapter, output_directory
    )

    result = runner.run()

    assert result.complete is True
    assert result.completed_case_count == 60
    assert result.newly_run_case_count == 60
    assert len(adapter.calls) == 60
    assert adapter.calls[0] == "D14_SM_001"
    assert adapter.calls[-1] == "D14_RU_010"
    assert result.seal is not None
    assert result.seal.complete_public_case_set is True
    assert result.private_references_loaded is False

    resumed = runner.run()
    assert resumed.resumed_case_count == 60
    assert resumed.newly_run_case_count == 0
    assert len(adapter.calls) == 60
    assert resumed.seal.sha256 == result.seal.sha256


def test_batch_runner_resumes_without_rerunning_completed_prefix(output_directory):
    first_adapter = StubAdapter()
    manifest = _manifest("batch-resume")
    first = Day15BatchRunner(
        PROJECT_ROOT, manifest, first_adapter, output_directory
    ).run(max_new_cases=7)
    assert first.complete is False
    assert first.completed_case_count == 7
    assert first.seal is None

    second_adapter = StubAdapter()
    second = Day15BatchRunner(
        PROJECT_ROOT, manifest, second_adapter, output_directory
    ).run()
    assert second.complete is True
    assert second.resumed_case_count == 7
    assert second.newly_run_case_count == 53
    assert "D14_SM_001" not in second_adapter.calls
    assert second_adapter.calls[0] == "D14_SM_008"


def test_batch_runner_rejects_modified_checkpoint_prefix(output_directory):
    adapter = StubAdapter()
    manifest = _manifest("batch-tamper")
    runner = Day15BatchRunner(PROJECT_ROOT, manifest, adapter, output_directory)
    runner.run(max_new_cases=2)
    candidate = runner.candidate_path
    payload = candidate.read_text(encoding="utf-8")
    candidate.write_text(payload.replace("offline-stub", "changed-stub", 1), encoding="utf-8")

    with pytest.raises(ValueError, match="payload hash|prefix was modified"):
        Day15BatchRunner(PROJECT_ROOT, manifest, StubAdapter(), output_directory).run()


def test_batch_runner_rejects_adapter_manifest_version_mismatch(output_directory):
    adapter = StubAdapter()
    adapter.candidate_version = CandidateVersion.RETRIEVAL_SQL
    with pytest.raises(ValueError, match="adapter version"):
        Day15BatchRunner(
            PROJECT_ROOT, _manifest("batch-version"), adapter, output_directory
        )


def test_batch_runner_requires_output_under_project_root(tmp_path):
    with pytest.raises(ValueError, match="inside project root"):
        Day15BatchRunner(
            PROJECT_ROOT,
            _manifest("batch-outside"),
            StubAdapter(),
            tmp_path,
        )
