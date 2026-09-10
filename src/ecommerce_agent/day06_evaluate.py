"""Evaluate only human-confirmed primary metric labels, not SQL accuracy."""

import json
from pathlib import Path

from src.ecommerce_agent.retrieval import (
    KeywordRetriever, RetrievalFilter, Retriever, build_documents, retrieve_payload,
)


def evaluate_primary_metrics(retriever: Retriever, labels: list[dict]) -> dict:
    selected = [row for row in labels
                if row["annotation_status"] == "human_confirmed_primary_metric_only"]
    if not selected:
        raise ValueError("No human-confirmed primary metric labels")
    if len({row["question_id"] for row in labels}) != len(labels):
        raise ValueError("Duplicate question ID")
    rows = []
    filters = RetrievalFilter(document_types=frozenset({"metric"}))
    for label in selected:
        payload = retrieve_payload(retriever, label["question"], top_k=5, filters=filters)
        expected = f"metric:{label['primary_metric_id']}"
        rank = next((hit["rank"] for hit in payload["hits"]
                     if hit["document"]["document_id"] == expected), None)
        rows.append({
            "question_id": label["question_id"],
            "expected_document_id": expected,
            "expected_rank_within_top5": rank,
            "hit_at_k": {str(k): rank is not None and rank <= k for k in (1, 3, 5)},
            "retrieval": payload,
        })
    return {
        "scope": "primary_metric_hit_rate_only_not_full_recall_or_sql_accuracy",
        "evaluation_usage": "development_examples_not_held_out_test_set",
        "included_questions": len(rows),
        "excluded_question_ids": [row["question_id"] for row in labels if row not in selected],
        "hit_rate_at_k": {str(k): sum(row["hit_at_k"][str(k)] for row in rows) / len(rows)
                          for k in (1, 3, 5)},
        "rows": rows,
    }


def main():
    root = Path(__file__).parents[2]
    labels = json.loads((root / "tests/fixtures/day06/human_labels.json").read_text(encoding="utf-8"))
    documents = build_documents(root)
    document_ids = {doc.document_id for doc in documents}
    for label in labels:
        if label["primary_metric_id"]:
            if f"metric:{label['primary_metric_id']}" not in document_ids:
                raise ValueError("Unknown annotated metric")
    report = evaluate_primary_metrics(KeywordRetriever(documents), labels)
    output = root / "data/processed/retrieval/primary_metric_evaluation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Primary metric questions:", report["included_questions"])
    print("Hit rates:", report["hit_rate_at_k"])
    for row in report["rows"]:
        print(row["question_id"], row["expected_document_id"],
              "rank within Top-5:", row["expected_rank_within_top5"])
        print([(hit["document"]["identifier"], round(hit["score"], 6))
               for hit in row["retrieval"]["hits"]])


if __name__ == "__main__":
    main()
