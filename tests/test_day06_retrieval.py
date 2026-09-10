import csv
from pathlib import Path

import pytest

from src.ecommerce_agent.analysis_plan import try_validate_analysis_plan
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.retrieval import (
    KeywordRetriever, RetrievalFilter, build_documents, retrieve_payload,
)


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def documents():
    return build_documents(ROOT)


@pytest.fixture(scope="module")
def retriever(documents):
    return KeywordRetriever(documents)


def test_documents_preserve_canonical_metrics_and_dimension_permissions(documents):
    with (ROOT / "data/metadata/metric_dictionary.csv").open(encoding="utf-8-sig", newline="") as stream:
        source = list(csv.DictReader(stream))
    catalog = MetricCatalog.from_csv(ROOT / "data/metadata/metric_dictionary.csv",
                                     ROOT / "data/metadata/dimension_dictionary.csv")
    metrics = {doc.identifier: doc for doc in documents if doc.document_type == "metric"}
    assert len(documents) == len({doc.document_id for doc in documents}) == 65
    assert len(metrics) == len(source) == 27
    for row in source:
        doc = metrics[row["metric_id"]]
        assert (doc.description, doc.formula, doc.grain, doc.constraints) == (
            row["definition"], row["formula"], row["base_grain"], row["constraints"],
        )
        assert set(doc.available_dimensions) == catalog.metrics[doc.identifier].available_dimensions
        assert all((ROOT / path).is_file() for path in doc.sources)


def test_schema_preserves_composite_key_and_foreign_key(documents):
    schema = {doc.identifier: doc for doc in documents if doc.document_type == "schema"}
    assert len(schema) == 38
    payment = schema["fact_payments.order_id"]
    assert payment.metadata["references"] == "fact_orders.order_id"
    assert "PRIMARY KEY (order_id, payment_sequential)" in payment.constraints
    assert "PRIMARY KEY (order_id, order_item_id)" in schema["fact_order_items.order_id"].constraints
    assert schema["fact_order_items.price"].chinese_name == "商品价格，不含运费"


def test_reconciliation_keeps_grain_rules_and_context(documents):
    doc = next(doc for doc in documents if doc.identifier == "delivered_payment_reconciliation_difference")
    assert "分别预聚合到 order_id" in doc.constraints
    assert "不自动判定为数据错误" in doc.constraints
    assert {"fact_order_items.price", "fact_order_items.freight_value",
            "fact_payments.payment_value"} <= set(doc.fields)
    assert "not_minimal_dependencies" in doc.metadata["field_scope"]


def test_top_k_is_deterministic_prefix_and_scores_are_explainable(retriever):
    query = "2018 年 6 月，已送达订单的商品金额加运费，与支付金额相差多少？"
    short = retriever.search(query, top_k=2)
    long = retriever.search(query, top_k=5)
    assert short == long[:2]
    assert long == retriever.search(query, top_k=5)
    assert [hit.rank for hit in long] == list(range(1, len(long) + 1))
    assert [hit.score for hit in long] == sorted((hit.score for hit in long), reverse=True)
    for hit in long:
        assert hit.score == pytest.approx(sum(
            sum(values.values()) for values in hit.evidence["field_contributions"].values()
        ))


def test_schema_filter_and_table_filter(retriever):
    hits = retriever.search("商品价格", filters=RetrievalFilter(
        document_types=frozenset({"schema"}), tables=frozenset({"fact_order_items"}),
    ))
    assert hits[0].document.identifier == "fact_order_items.price"
    assert all(hit.document.document_type == "schema" for hit in hits)
    assert all(hit.document.tables == ("fact_order_items",) for hit in hits)
    assert not retriever.search("商品价格", filters=RetrievalFilter(document_types=frozenset()))


def test_unsupported_metric_is_retrievable_but_plan_still_rejected(retriever):
    hits = retriever.search("按商品品类统计已送达订单支付金额", top_k=27,
                            filters=RetrievalFilter(document_types=frozenset({"metric"})))
    payment = next(hit.document for hit in hits if hit.document.identifier == "delivered_payment_amount")
    assert "product_category" not in payment.available_dimensions
    catalog = MetricCatalog.from_csv(ROOT / "data/metadata/metric_dictionary.csv",
                                     ROOT / "data/metadata/dimension_dictionary.csv")
    result = try_validate_analysis_plan({
        "metrics": [payment.identifier], "dimensions": ["product_category"],
        "filters": [], "time_range": {"mode": "all_data"},
    }, catalog)
    assert result.error_type.value == "invalid_semantics"


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_invalid_k_rejected(retriever, top_k):
    with pytest.raises(ValueError):
        retriever.search("GMV", top_k=top_k)


def test_blank_and_no_match_and_invalid_filter(retriever):
    with pytest.raises(ValueError):
        retriever.search(" ")
    with pytest.raises(ValueError):
        RetrievalFilter(document_types=frozenset({"invalid"}))
    assert retriever.search("zzzznonexistent") == ()


def test_business_boundary_accepts_alternative_retriever():
    class EmptyRetriever:
        def search(self, query, *, top_k=5, filters=None):
            return ()

    assert retrieve_payload(EmptyRetriever(), "测试问题")["hits"] == []


def test_primary_evaluation_excludes_boundary_labels_and_counts_rank(documents):
    from src.ecommerce_agent.day06_evaluate import evaluate_primary_metrics
    from src.ecommerce_agent.retrieval import RetrievalHit

    class RankedRetriever:
        def search(self, query, *, top_k=5, filters=None):
            return tuple(RetrievalHit(doc, 10.0 - rank, rank, {})
                         for rank, doc in enumerate(documents[:2], 1))

    report = evaluate_primary_metrics(RankedRetriever(), [
        {"question_id": "test_1", "question": "synthetic question",
         "annotation_status": "human_confirmed_primary_metric_only",
         "primary_metric_id": documents[1].identifier},
        {"question_id": "test_2", "question": "synthetic ambiguity",
         "annotation_status": "human_confirmed_ambiguity_only",
         "primary_metric_id": None},
    ])
    assert report["included_questions"] == 1
    assert report["excluded_question_ids"] == ["test_2"]
    assert report["hit_rate_at_k"] == {"1": 0.0, "3": 1.0, "5": 1.0}


def test_primary_evaluation_rejects_empty_labels(retriever):
    from src.ecommerce_agent.day06_evaluate import evaluate_primary_metrics

    with pytest.raises(ValueError, match="No human-confirmed"):
        evaluate_primary_metrics(retriever, [])
