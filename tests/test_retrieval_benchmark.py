import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.ecommerce_agent.retrieval_benchmark import evaluate_cases, validate_cases
from src.ecommerce_agent.retrieval import (
    DependencyKeywordRetriever, KeywordRetriever, RetrievalHit, build_documents,
)


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def documents():
    return build_documents(ROOT)


def test_dependency_roles_and_definitions_are_copied_from_canonical_metrics(documents):
    lookup = {doc.identifier: doc for doc in documents if doc.document_type == "metric"}
    rate = lookup["period_repeat_customer_rate"]
    references = rate.metadata["formula_dependencies"]
    assert {ref["role"]: ref["metric_id"] for ref in references} == {
        "numerator": "period_repeat_customer_count", "denominator": "delivered_customer_count",
    }
    for doc in lookup.values():
        for ref in doc.metadata["formula_dependencies"]:
            assert ref["definition"] == lookup[ref["metric_id"]].description
            assert ref["constraints"] == lookup[ref["metric_id"]].constraints
            assert ref["metric_id"] != doc.identifier
    assert "product_category" not in rate.available_dimensions


def test_v1_does_not_score_new_dependencies(documents):
    plain = tuple(replace(doc, metadata={}) for doc in documents)
    original = KeywordRetriever(plain).search("两笔客户比例")
    enriched = KeywordRetriever(documents).search("两笔客户比例")
    assert [(hit.rank, hit.score, hit.document.document_id) for hit in original] == [
        (hit.rank, hit.score, hit.document.document_id) for hit in enriched]


def test_v2_explains_added_field_without_mutating_canonical_definitions(documents):
    retriever = DependencyKeywordRetriever(documents)
    hits = retriever.search("期间复购率 两笔订单 比例", top_k=65)
    rate = next(hit for hit in hits if hit.document.identifier == "period_repeat_customer_rate")
    assert "两笔" in rate.evidence["field_contributions"]["dependencies"]
    assert rate.document.formula == "period_repeat_customer_count / delivered_customer_count"
    assert rate.score == pytest.approx(sum(sum(field.values()) for field in rate.evidence["field_contributions"].values()))


def test_fifteen_case_fixture_has_valid_ids_and_coverage_modes(documents):
    cases = json.loads((ROOT / "tests/fixtures/retrieval/retrieval_cases.json").read_text(encoding="utf-8"))["cases"]
    validate_cases(cases, {doc.document_id: doc.document_type for doc in documents})
    assert len(cases) >= 15
    assert {row["retrieval_type"] for row in cases} == {"metric", "schema", "both"}


def test_group_alternatives_and_draft_exclusion(documents):
    first, second = documents[:2]

    class StubRetriever:
        def search(self, query, *, top_k=5, filters=None):
            return (RetrievalHit(first, 1, 1, {}),)

    reviewed = {"id": "reviewed", "question": "synthetic", "retrieval_type": "metric",
                "annotation_status": "human_reviewed", "behavior": "supported",
                "expected_groups": [[first.document_id, second.document_id]]}
    pending = {**reviewed, "id": "pending", "annotation_status": "pending_human_review",
               "expected_groups": [[second.document_id]]}
    report = evaluate_cases(StubRetriever(), [reviewed, pending])
    assert report["pending_count"] == 1
    summary = report["summaries_reviewed_only"]["all"]
    assert summary["reviewed_count"] == 1
    assert summary["at_k"]["1"]["mean_required_group_coverage"] == 1
    assert report["rows"][1]["at_k"]["1"]["required_group_coverage"] == 0
    assert report["summaries_reviewed_only"]["schema"]["at_k"]["1"]["mean_required_group_coverage"] is None


def test_unknown_target_rejected_before_evaluation(documents):
    cases = json.loads((ROOT / "tests/fixtures/retrieval/retrieval_cases.json").read_text(encoding="utf-8"))["cases"]
    invalid = copy.deepcopy(cases)
    invalid[0]["expected_groups"] = [["metric:invented_metric"]]
    with pytest.raises(ValueError, match="Unknown target"):
        validate_cases(invalid, {doc.document_id: doc.document_type for doc in documents})


def test_assistant_review_provenance_and_field_mentions_are_not_field_hits(documents):
    metric = next(doc for doc in documents if doc.identifier == "delivered_gmv")
    assert "fact_order_items.price" in metric.fields

    class MetricOnlyRetriever:
        def search(self, query, *, top_k=5, filters=None):
            return (RetrievalHit(metric, 1, 1, {}),)

    report = evaluate_cases(MetricOnlyRetriever(), [{
        "id": "mixed", "question": "synthetic", "retrieval_type": "both",
        "annotation_status": "assistant_reviewed", "behavior": "supported",
        "expected_groups": [[metric.document_id], ["schema:fact_order_items.price"]],
    }])
    assert report["annotation_counts"]["human_reviewed"] == 0
    assert report["annotation_counts"]["assistant_reviewed"] == 1
    assert report["rows"][0]["at_k"]["1"]["required_group_coverage"] == 0.5
    assert report["rows"][0]["at_k"]["1"]["all_required_groups_found"] is False
