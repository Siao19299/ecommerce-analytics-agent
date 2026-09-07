import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from src.ecommerce_agent.analysis_planner import PlanningLogRecord


def append_planning_logs(
    log_path: str | Path,
    records: Iterable[PlanningLogRecord],
) -> None:
    """将脱敏规划事件追加为一行一个 JSON 对象。"""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open(mode="a", encoding="utf-8", newline="") as file:
        for record in records:
            payload = asdict(record)
            payload["created_at"] = record.created_at.isoformat()
            file.write(
                json.dumps(payload, ensure_ascii=False) + "\n"
            )
