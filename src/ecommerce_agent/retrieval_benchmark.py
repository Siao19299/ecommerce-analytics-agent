"""Reproducible v1/v2 comparison with reviewed and draft labels kept separate."""

import argparse
import hashlib
import json
from pathlib import Path

from src.ecommerce_agent.retrieval import (
    DependencyKeywordRetriever, KeywordRetriever, RetrievalFilter,
    Retriever, build_documents, retrieve_payload,
)


TOP_K_VALUES = (1, 3, 5, 10)
REVIEWED_STATUSES = {"human_reviewed", "assistant_reviewed"}


def validate_cases(cases: list[dict], document_types: dict[str, str]) -> None:
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Cases must be nonempty with unique IDs")
    for case in cases:
        if not case["question"].strip():
            raise ValueError("Blank question")
        if case["annotation_status"] not in REVIEWED_STATUSES | {"pending_human_review"}:
            raise ValueError("Unknown annotation status")
        if case["retrieval_type"] not in {"metric", "schema", "both"}:
            raise ValueError("Unknown retrieval type")
        seen = set()
        if not case["expected_groups"]:
            raise ValueError("At least one expected target group is required")
        for group in case["expected_groups"]:
            if not group or len(set(group)) != len(group) or seen.intersection(group):
                raise ValueError("Empty or overlapping target groups")
            for identifier in group:
                if identifier not in document_types:
                    raise ValueError(f"Unknown target document: {identifier}")
                if case["retrieval_type"] != "both" and document_types[identifier] != case["retrieval_type"]:
                    raise ValueError("Target conflicts with retrieval type")
            seen.update(group)
        if case["retrieval_type"] == "both" and {document_types[key] for key in seen} != {"metric", "schema"}:
            raise ValueError("Both mode requires metric and schema targets")


def evaluate_cases(retriever: Retriever, cases: list[dict]) -> dict:
    rows = []
    for case in cases:
        scope = case["retrieval_type"]
        types = frozenset({"metric", "schema"}) if scope == "both" else frozenset({scope})
        payload = retrieve_payload(retriever, case["question"], top_k=max(TOP_K_VALUES),
                                   filters=RetrievalFilter(document_types=types))
        at_k = {}
        for k in TOP_K_VALUES:
            actual = {hit["document"]["document_id"] for hit in payload["hits"][:k]}
            found = [bool(actual.intersection(group)) for group in case["expected_groups"]]
            at_k[str(k)] = {
                "required_group_coverage": sum(found) / len(found),
                "all_required_groups_found": all(found),
                "missing_groups": [group for group, hit in zip(case["expected_groups"], found) if not hit],
            }
        rows.append({
            "id": case["id"], "annotation_status": case["annotation_status"],
            "retrieval_type": scope, "expected_behavior_annotation": case["behavior"],
            "expected_groups": case["expected_groups"], "at_k": at_k, "retrieval": payload,
        })
    summaries = {}
    for scope in ("all", "metric", "schema", "both"):
        selected = [row for row in rows if row["annotation_status"] in REVIEWED_STATUSES
                    and (scope == "all" or row["retrieval_type"] == scope)]
        summaries[scope] = {
            "reviewed_count": len(selected),
            "at_k": {str(k): {
                "mean_required_group_coverage": (
                    sum(row["at_k"][str(k)]["required_group_coverage"] for row in selected) / len(selected)
                    if selected else None),
                "complete_question_count": sum(row["at_k"][str(k)]["all_required_groups_found"] for row in selected),
            } for k in TOP_K_VALUES},
        }
    return {
        "summaries_reviewed_only": summaries,
        "pending_count": sum(row["annotation_status"] not in REVIEWED_STATUSES for row in rows),
        "annotation_counts": {status: sum(row["annotation_status"] == status for row in rows)
                              for status in ("human_reviewed", "assistant_reviewed", "pending_human_review")},
        "rows": rows,
        "measurement_scope": "required_target_group_coverage_not_full_SQL_dependencies_or_execution_accuracy",
        "behavior_note": "Behavior labels are not executed or scored as planner decisions by retrieval.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-reviewed", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).parents[2]
    fixture = root / "tests/fixtures/retrieval/retrieval_cases.json"
    dataset = json.loads(fixture.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    documents = build_documents(root)
    validate_cases(cases, {doc.document_id: doc.document_type for doc in documents})
    if args.require_reviewed and (len(cases) < 15 or any(
        case["annotation_status"] not in REVIEWED_STATUSES for case in cases
    )):
        parser.error("Acceptance requires at least 15 reviewed cases with explicit reviewer provenance")
    sources = ["tests/fixtures/retrieval/retrieval_cases.json", "data/metadata/metric_dictionary.csv",
               "data/metadata/dimension_dictionary.csv", "data/metadata/database_data_dictionary.csv",
               "sql/schema.sql", "docs/OLIST_DATA_DICTIONARY.md", "src/ecommerce_agent/retrieval.py",
               "src/ecommerce_agent/retrieval_benchmark.py"]
    report = {
        "dataset_usage": dataset["usage"],
        "document_count": len(documents), "top_k_values": TOP_K_VALUES,
        "input_sha256": {source: hashlib.sha256((root / source).read_bytes()).hexdigest() for source in sources},
        "implementations": {},
    }
    for implementation in (KeywordRetriever, DependencyKeywordRetriever):
        retriever = implementation(documents)
        result = evaluate_cases(retriever, cases)
        report["implementations"][retriever.version] = result
        print(retriever.version)
        print(json.dumps(result["summaries_reviewed_only"], ensure_ascii=False))
        print("Pending review:", result["pending_count"])
    output = root / "data/processed/retrieval/benchmark_comparison.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
