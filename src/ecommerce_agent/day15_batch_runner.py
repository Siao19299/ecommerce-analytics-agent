"""Append-only, resumable, gold-blind batch runner for Day 15 candidates."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Protocol
from uuid import uuid4

from pydantic import Field, model_validator

from src.ecommerce_agent.day14_schema import StrictEvaluationModel
from src.ecommerce_agent.day15_protocol import CandidateVersion, RunPurpose
from src.ecommerce_agent.day15_reproducibility import (
    PublicCase,
    RunManifest,
    SealedCandidateArtifact,
    load_public_cases,
    seal_candidate_artifact,
    write_run_manifest,
)


CANDIDATE_FILENAME = "candidate_outputs.jsonl"
CHECKPOINT_FILENAME = "checkpoint.json"
MANIFEST_FILENAME = "run_manifest.json"
SEAL_FILENAME = "candidate_outputs.seal.json"


class BatchAdapter(Protocol):
    candidate_version: CandidateVersion

    def run_case(self, case: PublicCase, *, run_id: str): ...


class BatchCandidateRecord(StrictEvaluationModel):
    record_schema_version: str = "1.0.0"
    experiment_run_id: str
    case_run_id: str
    case_id: str
    ordinal: int = Field(ge=1)
    candidate_version: CandidateVersion
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    adapter_record_type: str
    adapter_error: str | None = None
    payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        return self


class BatchCheckpoint(StrictEvaluationModel):
    checkpoint_schema_version: str = "1.0.0"
    experiment_run_id: str
    candidate_version: CandidateVersion
    completed_case_count: int = Field(ge=0, le=60)
    completed_case_ids: tuple[str, ...]
    candidate_prefix_byte_count: int = Field(ge=0)
    candidate_prefix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: datetime

    @model_validator(mode="after")
    def validate_counts(self):
        if self.completed_case_count != len(self.completed_case_ids):
            raise ValueError("checkpoint count and case IDs differ")
        return self


class BatchRunResult(StrictEvaluationModel):
    experiment_run_id: str
    candidate_version: CandidateVersion
    completed_case_count: int
    total_case_count: int
    resumed_case_count: int
    newly_run_case_count: int
    complete: bool
    candidate_path: str
    checkpoint_path: str
    seal: SealedCandidateArtifact | None
    private_references_loaded: bool = False


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _bytes_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    # Windows indexers/antivirus can briefly hold a freshly replaced JSON
    # checkpoint. Keep atomic replacement semantics while tolerating only a
    # short transient lock; persistent permission errors still propagate.
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            sleep(0.02 * (attempt + 1))


def _load_records(path: Path) -> tuple[BatchCandidateRecord, ...]:
    if not path.exists():
        return ()
    return tuple(
        BatchCandidateRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def load_batch_candidate_records(path: Path) -> tuple[BatchCandidateRecord, ...]:
    """Public strict loader used by the post-seal materialization stage."""

    return _load_records(path)


class Day15BatchRunner:
    def __init__(
        self,
        root: Path,
        manifest: RunManifest,
        adapter: BatchAdapter,
        output_directory: Path,
    ) -> None:
        self.root = root.resolve()
        self.manifest = manifest
        self.adapter = adapter
        self.output_directory = output_directory.resolve()
        try:
            self.output_directory.relative_to(self.root)
        except ValueError as error:
            raise ValueError("batch output directory must be inside project root") from error
        if adapter.candidate_version is not manifest.configuration.candidate_version:
            raise ValueError("adapter version does not match the run manifest")
        if (
            manifest.configuration.purpose is RunPurpose.MAIN_EXPERIMENT
            and not manifest.runtime.git_worktree_clean
        ):
            raise ValueError("main experiment requires a clean committed worktree")

    @property
    def candidate_path(self) -> Path:
        return self.output_directory / CANDIDATE_FILENAME

    @property
    def checkpoint_path(self) -> Path:
        return self.output_directory / CHECKPOINT_FILENAME

    def run(self, *, max_new_cases: int | None = None) -> BatchRunResult:
        if max_new_cases is not None and (
            isinstance(max_new_cases, bool)
            or not isinstance(max_new_cases, int)
            or max_new_cases < 1
        ):
            raise ValueError("max_new_cases must be a positive integer")
        cases = load_public_cases(self.root)
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._ensure_manifest()
        records = list(_load_records(self.candidate_path))
        self._validate_existing_prefix(records, cases)
        self._validate_checkpoint_or_recover(records)
        resumed = len(records)
        limit = len(cases) if max_new_cases is None else min(len(cases), resumed + max_new_cases)

        for index in range(resumed, limit):
            case = cases[index]
            record = self._run_one(case, index + 1)
            self._append(record)
            records.append(record)
            self._write_checkpoint(records)

        complete = len(records) == len(cases)
        seal = None
        if complete:
            seal = seal_candidate_artifact(
                self.root,
                self.candidate_path,
                run_id=self.manifest.run_id,
                expected_case_count=len(cases),
            )
            _write_atomic(
                self.output_directory / SEAL_FILENAME,
                seal.model_dump_json(indent=2) + "\n",
            )
        return BatchRunResult(
            experiment_run_id=self.manifest.run_id,
            candidate_version=self.adapter.candidate_version,
            completed_case_count=len(records),
            total_case_count=len(cases),
            resumed_case_count=resumed,
            newly_run_case_count=len(records) - resumed,
            complete=complete,
            candidate_path=self.candidate_path.relative_to(self.root).as_posix(),
            checkpoint_path=self.checkpoint_path.relative_to(self.root).as_posix(),
            seal=seal,
        )

    def _ensure_manifest(self) -> None:
        path = self.output_directory / MANIFEST_FILENAME
        if path.exists():
            existing = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != self.manifest:
                raise ValueError("existing run manifest differs from requested manifest")
            return
        write_run_manifest(path, self.manifest)

    def _validate_existing_prefix(
        self,
        records: list[BatchCandidateRecord],
        cases: tuple[PublicCase, ...],
    ) -> None:
        if len(records) > len(cases):
            raise ValueError("candidate file has more than 60 records")
        seen: set[str] = set()
        for index, record in enumerate(records):
            expected = cases[index]
            if record.experiment_run_id != self.manifest.run_id:
                raise ValueError("candidate record belongs to another experiment run")
            if record.candidate_version is not self.adapter.candidate_version:
                raise ValueError("candidate record version mismatch")
            if record.case_id != expected.case_id or record.ordinal != index + 1:
                raise ValueError("candidate records are not a public-manifest prefix")
            if record.case_id in seen:
                raise ValueError("duplicate case ID in candidate records")
            if _payload_hash(record.payload) != record.payload_sha256:
                raise ValueError("candidate payload hash mismatch")
            seen.add(record.case_id)

    def _validate_checkpoint_or_recover(
        self, records: list[BatchCandidateRecord]
    ) -> None:
        if not self.checkpoint_path.exists():
            if records:
                raise ValueError("candidate records exist without a checkpoint")
            self._write_checkpoint(records)
            return
        checkpoint = BatchCheckpoint.model_validate_json(
            self.checkpoint_path.read_text(encoding="utf-8")
        )
        if checkpoint.experiment_run_id != self.manifest.run_id:
            raise ValueError("checkpoint belongs to another experiment run")
        if checkpoint.candidate_version is not self.adapter.candidate_version:
            raise ValueError("checkpoint version mismatch")
        payload = self.candidate_path.read_bytes() if self.candidate_path.exists() else b""
        prefix = payload[: checkpoint.candidate_prefix_byte_count]
        if _bytes_hash(prefix) != checkpoint.candidate_prefix_sha256:
            raise ValueError("sealed checkpoint prefix was modified")
        if checkpoint.completed_case_ids != tuple(
            record.case_id for record in records[: checkpoint.completed_case_count]
        ):
            raise ValueError("checkpoint case IDs differ from candidate records")
        if checkpoint.completed_case_count > len(records):
            raise ValueError("checkpoint is ahead of candidate records")
        # Extra fully validated records mean the process stopped after append and
        # before the atomic checkpoint update. Rebuild only the checkpoint.
        if checkpoint.completed_case_count != len(records):
            self._write_checkpoint(records)

    def _run_one(self, case: PublicCase, ordinal: int) -> BatchCandidateRecord:
        case_run_id = f"{self.manifest.run_id}-{case.case_id.lower()}"
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        adapter_error = None
        try:
            output = self.adapter.run_case(case, run_id=case_run_id)
            if hasattr(output, "model_dump"):
                payload = output.model_dump(mode="json")
                record_type = type(output).__name__
            else:
                raise TypeError("adapter output must be a validated model")
        except Exception:
            payload = {}
            record_type = "adapter_exception"
            adapter_error = "adapter_exception"
        finished_at = datetime.now(timezone.utc)
        return BatchCandidateRecord(
            experiment_run_id=self.manifest.run_id,
            case_run_id=case_run_id,
            case_id=case.case_id,
            ordinal=ordinal,
            candidate_version=self.adapter.candidate_version,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(perf_counter() - started) * 1000,
            adapter_record_type=record_type,
            adapter_error=adapter_error,
            payload=payload,
            payload_sha256=_payload_hash(payload),
        )

    def _append(self, record: BatchCandidateRecord) -> None:
        line = _canonical_json(record.model_dump(mode="json")) + "\n"
        with self.candidate_path.open("ab") as stream:
            stream.write(line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())

    def _write_checkpoint(self, records: list[BatchCandidateRecord]) -> None:
        payload = self.candidate_path.read_bytes() if self.candidate_path.exists() else b""
        checkpoint = BatchCheckpoint(
            experiment_run_id=self.manifest.run_id,
            candidate_version=self.adapter.candidate_version,
            completed_case_count=len(records),
            completed_case_ids=tuple(record.case_id for record in records),
            candidate_prefix_byte_count=len(payload),
            candidate_prefix_sha256=_bytes_hash(payload),
            updated_at=datetime.now(timezone.utc),
        )
        _write_atomic(
            self.checkpoint_path,
            checkpoint.model_dump_json(indent=2) + "\n",
        )
