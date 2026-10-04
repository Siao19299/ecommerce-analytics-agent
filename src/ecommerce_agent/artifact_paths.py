"""Resolve references embedded in immutable evaluation artifacts after relocation."""

from __future__ import annotations

import json
from pathlib import Path


LAYOUT_MANIFEST = "data/evaluation/benchmark_v1/layout_manifest.json"


def resolve_artifact_path(root: Path, relative_path: str) -> Path:
    """Keep frozen reference bytes intact while mapping their original locators."""
    locator = relative_path.split("#", 1)[0].replace("\\", "/")
    manifest_path = root / LAYOUT_MANIFEST
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = manifest.get("provenance_sources", {}).get(locator)
        if source is not None:
            locator = source
        else:
            for original, current in manifest.get("prefix_aliases", {}).items():
                if locator.startswith(original):
                    locator = current + locator[len(original):]
                    break
    path = (root / locator).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Evaluation artifact path must stay inside the project")
    return path
