"""Public-input isolation, run manifests, and candidate artifact sealing for Day 15."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ConfigDict, Field, model_validator

from src.ecommerce_agent.day14_schema import StrictEvaluationModel
from src.ecommerce_agent.day15_protocol import (
    CandidateVersion,
    DATABASE_SHA256,
    FROZEN_DATASET_CONTENT_SHA256,
    FROZEN_DATASET_FILE_SHA256,
    FROZEN_DATASET_VERSION,
    PUBLIC_MANIFEST_SHA256,
    ResultProvenance,
    RunPurpose,
)


PUBLIC_MANIFEST_RELATIVE_PATH = Path("data/evaluation/day14/cases.public.jsonl")
PRIVATE_DATASET_RELATIVE_PATH = Path("data/evaluation/day14/dataset.v1.json")
PRIVATE_REFERENCE_RELATIVE_PATH = Path("data/evaluation/day14/references")
EXPECTED_PUBLIC_CASE_COUNT = 60


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


class PublicCase(StrictEvaluationModel):
    """The complete and only per-case payload available to candidate adapters."""

    case_id: str = Field(pattern=r"^D14_(SM|AJ|MS|RU)_[0-9]{3}$")
    question: str = Field(min_length=1, max_length=2000)


class SamplingConfiguration(StrictEvaluationModel):
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    temperature: float = Field(ge=0)
    top_p: float = Field(gt=0, le=1)
    random_seed: int | None = None
    maximum_output_tokens: int = Field(gt=0)
    per_call_timeout_seconds: float = Field(gt=0)


class ExperimentConfiguration(StrictEvaluationModel):
    candidate_version: CandidateVersion
    purpose: RunPurpose
    result_provenance: ResultProvenance
    sampling: SamplingConfiguration
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_configuration_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    adapter_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    external_api_authorized: bool = False
    repetition_index: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_provenance_and_retrieval(self):
        if self.result_provenance is ResultProvenance.REAL_MODEL:
            if not self.external_api_authorized:
                raise ValueError("real-model provenance requires explicit API authorization")
            if self.sampling.provider == "offline":
                raise ValueError("real-model provenance cannot use the offline provider")
        elif self.external_api_authorized:
            raise ValueError("offline/fake runs must not carry API authorization")

        requires_retrieval = self.candidate_version in {
            CandidateVersion.RETRIEVAL_SQL,
            CandidateVersion.FULL_AGENT,
        }
        if requires_retrieval != (self.retrieval_configuration_sha256 is not None):
            raise ValueError("retrieval configuration hash must match version capabilities")
        return self


class FrozenInputs(StrictEvaluationModel):
    dataset_version: str = FROZEN_DATASET_VERSION
    dataset_content_sha256: str = FROZEN_DATASET_CONTENT_SHA256
    dataset_file_sha256: str = FROZEN_DATASET_FILE_SHA256
    public_manifest_sha256: str = PUBLIC_MANIFEST_SHA256
    database_sha256: str = DATABASE_SHA256


class RuntimeMetadata(StrictEvaluationModel):
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    git_worktree_clean: bool
    python_version: str
    platform: str
    executable_is_project_venv: bool


class RunManifest(StrictEvaluationModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_schema_version: str = "1.0.0"
    run_id: str = Field(min_length=1)
    created_at: datetime
    configuration: ExperimentConfiguration
    frozen_inputs: FrozenInputs
    runtime: RuntimeMetadata
    public_case_count: int = EXPECTED_PUBLIC_CASE_COUNT
    candidate_received_reference_assets: bool = False
    private_references_loaded: bool = False
    api_key_values_read: int = 0

    @model_validator(mode="after")
    def validate_isolation(self):
        if self.candidate_received_reference_assets or self.private_references_loaded:
            raise ValueError("candidate-phase manifest cannot include loaded private references")
        if self.api_key_values_read != 0:
            raise ValueError("run metadata must never read or store API key values")
        return self


class SealedCandidateArtifact(StrictEvaluationModel):
    seal_schema_version: str = "1.0.0"
    run_id: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=1)
    nonempty_line_count: int = Field(ge=1)
    sealed_at: datetime
    complete_public_case_set: bool
    private_references_loaded_before_seal: bool = False

    @model_validator(mode="after")
    def validate_seal_order(self):
        if self.private_references_loaded_before_seal:
            raise ValueError("candidate output cannot be sealed after private references were loaded")
        return self


class ScoringAuthorization(StrictEvaluationModel):
    """Capability produced only after a sealed artifact is re-verified."""

    run_id: str
    sealed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorized_at: datetime


def load_public_cases(root: Path) -> tuple[PublicCase, ...]:
    path = root / PUBLIC_MANIFEST_RELATIVE_PATH
    if _sha256_file(path) != PUBLIC_MANIFEST_SHA256:
        raise ValueError("public manifest SHA-256 does not match frozen Day 14 input")
    raw_lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = tuple(PublicCase.model_validate_json(line) for line in raw_lines)
    case_ids = [case.case_id for case in cases]
    if len(cases) != EXPECTED_PUBLIC_CASE_COUNT:
        raise ValueError("public manifest must contain exactly 60 non-empty records")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("public manifest contains duplicate case IDs")
    return cases


def capture_runtime_metadata(root: Path) -> RuntimeMetadata:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    return RuntimeMetadata(
        git_commit=commit,
        git_worktree_clean=not porcelain.strip(),
        python_version=platform.python_version(),
        platform=platform.platform(),
        executable_is_project_venv=Path(sys.executable).resolve() == venv_python.resolve(),
    )


def build_run_manifest(
    root: Path,
    configuration: ExperimentConfiguration,
    *,
    run_id: str | None = None,
) -> RunManifest:
    load_public_cases(root)
    if _sha256_file(root / PRIVATE_DATASET_RELATIVE_PATH) != FROZEN_DATASET_FILE_SHA256:
        raise ValueError("private frozen dataset file hash changed")
    if _sha256_file(root / "data/processed/olist.sqlite3") != DATABASE_SHA256:
        raise ValueError("SQLite snapshot hash changed")
    return RunManifest(
        run_id=run_id or uuid4().hex,
        created_at=datetime.now(timezone.utc),
        configuration=configuration,
        frozen_inputs=FrozenInputs(),
        runtime=capture_runtime_metadata(root),
    )


def write_run_manifest(path: Path, manifest: RunManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump_json(indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def seal_candidate_artifact(
    root: Path,
    candidate_path: Path,
    *,
    run_id: str,
    expected_case_count: int = EXPECTED_PUBLIC_CASE_COUNT,
) -> SealedCandidateArtifact:
    resolved_root = root.resolve()
    resolved_candidate = candidate_path.resolve()
    try:
        relative = resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("candidate artifact must remain inside the project root") from error
    if not resolved_candidate.is_file():
        raise ValueError("candidate artifact does not exist")
    payload = resolved_candidate.read_bytes()
    nonempty_lines = sum(bool(line.strip()) for line in payload.splitlines())
    return SealedCandidateArtifact(
        run_id=run_id,
        relative_path=relative.as_posix(),
        sha256=_sha256_bytes(payload),
        byte_count=len(payload),
        nonempty_line_count=nonempty_lines,
        sealed_at=datetime.now(timezone.utc),
        complete_public_case_set=nonempty_lines == expected_case_count,
    )


def authorize_scoring(
    root: Path,
    seal: SealedCandidateArtifact,
) -> ScoringAuthorization:
    artifact = root / seal.relative_path
    if not seal.complete_public_case_set:
        raise ValueError("incomplete candidate artifacts cannot enter private scoring")
    if not artifact.is_file() or _sha256_file(artifact) != seal.sha256:
        raise ValueError("candidate artifact changed after sealing")
    return ScoringAuthorization(
        run_id=seal.run_id,
        sealed_sha256=seal.sha256,
        authorized_at=datetime.now(timezone.utc),
    )


def private_reference_paths(
    root: Path,
    authorization: ScoringAuthorization,
) -> tuple[Path, Path]:
    """Return private paths only to a caller holding post-seal authorization."""

    if not authorization.sealed_sha256:
        raise ValueError("valid scoring authorization is required")
    return root / PRIVATE_DATASET_RELATIVE_PATH, root / PRIVATE_REFERENCE_RELATIVE_PATH


def canonical_configuration_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(serialized)
