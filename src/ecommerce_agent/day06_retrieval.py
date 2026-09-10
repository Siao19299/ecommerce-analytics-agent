"""Offline CLI: python -m src.ecommerce_agent.day06_retrieval --question ..."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src.ecommerce_agent.retrieval import (
    DependencyKeywordRetriever, KeywordRetriever, RetrievalFilter, build_documents, retrieve_payload,
)


def main():
    parser = argparse.ArgumentParser(description="Day 6 offline lexical baseline")
    parser.add_argument("--question", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--type", choices=("metric", "schema", "both"), default="both")
    parser.add_argument("--retriever", choices=("baseline", "dependencies"), default="baseline")
    args = parser.parse_args()
    root = Path(__file__).parents[2]
    documents = build_documents(root)
    implementation = KeywordRetriever if args.retriever == "baseline" else DependencyKeywordRetriever
    retriever = implementation(documents)
    types = frozenset({"metric", "schema"}) if args.type == "both" else frozenset({args.type})
    result = retrieve_payload(retriever, args.question, top_k=args.top_k,
                              filters=RetrievalFilter(document_types=types))
    output = root / "data" / "processed" / "retrieval"
    output.mkdir(parents=True, exist_ok=True)
    (output / "documents.json").write_text(
        json.dumps([asdict(doc) for doc in documents], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "latest_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Documents: {len(documents)}; retriever: {retriever.version}")
    for hit in result["hits"]:
        print(hit["rank"], round(hit["score"], 6), hit["document"]["document_id"])
    print(f"Full result: {output / 'latest_result.json'}")


if __name__ == "__main__":
    main()
