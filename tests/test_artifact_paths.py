import hashlib
import json
from pathlib import Path

import pytest

from src.ecommerce_agent.artifact_paths import LAYOUT_MANIFEST, resolve_artifact_path


ROOT = Path(__file__).parents[1]


def test_frozen_locator_resolves_to_relocated_reference_bytes():
    dataset = json.loads((ROOT / "data/evaluation/benchmark_v1/dataset.v1.json").read_text(encoding="utf-8"))
    reference = dataset["cases"][0]["result_reference"]
    resolved = resolve_artifact_path(ROOT, reference["path"])
    assert resolved.is_relative_to(ROOT / "data/evaluation/benchmark_v1")
    assert hashlib.sha256(resolved.read_bytes()).hexdigest() == reference["sha256"]


def test_provenance_locator_resolves_to_matching_original_snapshot():
    manifest = json.loads((ROOT / LAYOUT_MANIFEST).read_text(encoding="utf-8"))
    for original, snapshot in manifest["provenance_sources"].items():
        path = resolve_artifact_path(ROOT, original + "#definition")
        assert path == ROOT / snapshot
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.stem


def test_current_paths_work_without_a_layout_manifest(tmp_path):
    assert resolve_artifact_path(tmp_path, "references/result.json") == tmp_path / "references/result.json"


def test_relative_and_manifest_paths_cannot_escape_project(tmp_path):
    with pytest.raises(ValueError, match="inside the project"):
        resolve_artifact_path(tmp_path, "../outside.json")
    manifest = tmp_path / LAYOUT_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"prefix_aliases": {"references/": "../outside/"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="inside the project"):
        resolve_artifact_path(tmp_path, "references/result.json")
