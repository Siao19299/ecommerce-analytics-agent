import json
from pathlib import Path
from typing import Any

from src.ecommerce_agent.basic_metrics import run_metric_queries


def run_business_queries(
    database_path: str | Path,
    standard_sql_path: str | Path,
    advanced_sql_path: str | Path,
) -> dict[str, dict[str, dict[str, Any]]]:
    """执行 Business metrics 标准与进阶指标 SQL。"""
    return {
        "standard": run_metric_queries(database_path, standard_sql_path),
        "advanced": run_metric_queries(database_path, advanced_sql_path),
    }


if __name__ == "__main__":
    project_root = Path(__file__).parents[2]
    results = run_business_queries(
        database_path=project_root / "data" / "processed" / "olist.sqlite3",
        standard_sql_path=(
            project_root / "sql" / "standard_metrics.sql"
        ),
        advanced_sql_path=(
            project_root / "sql" / "advanced_metrics.sql"
        ),
    )
    output_path = (
        project_root
        / "data"
        / "processed"
        / "metrics"
        / "business_metric_results.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for query_group, group_results in results.items():
        for query_name, result in group_results.items():
            print(query_group, query_name, result["rows"][:5])
