import json
import re
import sqlite3
from pathlib import Path
from typing import Any


QUERY_NAME_PATTERN = re.compile(
    r"^-- name:\s*([a-z][a-z0-9_]*)\s*$",
    re.MULTILINE,
)


def load_named_queries(sql_path: str | Path) -> dict[str, str]:
    sql_text = Path(sql_path).read_text(encoding="utf-8")
    matches = list(QUERY_NAME_PATTERN.finditer(sql_text))
    queries = {}

    for index, match in enumerate(matches):
        query_name = match.group(1)
        query_end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(sql_text)
        )
        query_sql = sql_text[match.end():query_end].strip()
        if not query_sql:
            raise ValueError(f"查询 {query_name} 没有 SQL 内容")
        if query_name in queries:
            raise ValueError(f"查询名称重复：{query_name}")
        queries[query_name] = query_sql

    if not queries:
        raise ValueError("SQL 文件中未找到 -- name: query_name 标记")

    return queries


def execute_named_queries(
    connection: sqlite3.Connection,
    queries: dict[str, str],
) -> dict[str, dict[str, Any]]:
    results = {}

    for query_name, query_sql in queries.items():
        cursor = connection.execute(query_sql)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        results[query_name] = {
            "sql": query_sql,
            "columns": columns,
            "rows": rows,
        }

    return results


def run_metric_queries(
    database_path: str | Path,
    sql_path: str | Path,
) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(database_path)

    try:
        queries = load_named_queries(sql_path)
        return execute_named_queries(connection, queries)
    finally:
        connection.close()


if __name__ == "__main__":
    project_root = Path(__file__).parents[2]
    metric_results = run_metric_queries(
        database_path=project_root / "data" / "processed" / "olist.sqlite3",
        sql_path=project_root / "sql" / "basic_metrics.sql",
    )
    output_path = (
        project_root
        / "data"
        / "processed"
        / "metrics"
        / "basic_metric_results.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(metric_results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for query_name, result in metric_results.items():
        print(query_name, result["rows"][:5])
