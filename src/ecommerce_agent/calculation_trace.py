"""Independent Deterministic analysis calculation traces linked to SQL repair run IDs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CalculationTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    calculation_id: str = Field(min_length=1)
    parent_run_id: str = Field(min_length=1)
    source_sql_attempt: int = Field(ge=1)
    step: str = Field(min_length=1)
    analysis_type: str = Field(min_length=1)
    created_at: datetime
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: dict[str, Any]


def build_calculation_trace(
    *,
    calculation_id: str,
    step: str,
    result: BaseModel,
    created_at: datetime | None = None,
) -> CalculationTrace:
    payload = result.model_dump(mode="json")
    raw_input = {
        "metric": payload.get("metric"),
        "raw_input": payload.get("raw_input"),
        "method": payload.get("method"),
    }
    encoded = json.dumps(
        raw_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    lineage = payload["lineage"]
    return CalculationTrace(
        calculation_id=calculation_id,
        parent_run_id=lineage["parent_run_id"],
        source_sql_attempt=lineage["source_sql_attempt"],
        step=step,
        analysis_type=str(payload["analysis_type"]),
        created_at=created_at or datetime.now(timezone.utc),
        input_sha256=hashlib.sha256(encoded).hexdigest(),
        result=payload,
    )


def write_calculation_trace(path: str | Path, trace: CalculationTrace) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        trace.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

