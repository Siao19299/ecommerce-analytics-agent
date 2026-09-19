from pathlib import Path

import pytest

from src.ecommerce_agent.day15_direct_sql import (
    DirectAction,
    DirectAdapterFailure,
    build_direct_sql_public_context,
)
from src.ecommerce_agent.day15_reproducibility import PublicCase
from src.ecommerce_agent.day15_retrieval_sql import RetrievalSqlAdapter
from src.ecommerce_agent.model_client import FakeModelClient, ModelConfig, ModelResponse
from src.ecommerce_agent.retrieval import KeywordRetriever, build_documents


PROJECT_ROOT = Path(__file__).parents[1]


def _adapter(response: str):
    documents = build_documents(PROJECT_ROOT)
    retriever = KeywordRetriever(documents)
    client = FakeModelClient(ModelResponse(content=response, model_name="fake-retrieval"))
    return (
        RetrievalSqlAdapter(
            client=client,
            config=ModelConfig(model_name="fake-retrieval"),
            public_context=build_direct_sql_public_context(PROJECT_ROOT),
            retriever=retriever,
            retriever_version=retriever.version,
        ),
        client,
    )


def test_retrieval_sql_adds_recorded_metric_and_schema_evidence_to_shared_contract():
    case = PublicCase(
        case_id="D14_SM_001", question="全部数据中有多少笔已送达订单？"
    )
    adapter, client = _adapter(
        '{"action":"sql","sql":"SELECT COUNT(DISTINCT order_id) AS n '
        'FROM fact_orders WHERE order_status = :status",'
        '"parameters":{"status":"delivered"},"message":null}'
    )

    record = adapter.run_case(case)

    assert record.action is DirectAction.SQL
    assert record.model_call_count == 1
    assert record.retriever_version == "keyword-bigram-idf-v1"
    assert len(record.retrieval_evidence) <= 13
    assert {item.document_type for item in record.retrieval_evidence} == {
        "metric",
        "schema",
    }
    system, user = client.requests[0][0]
    assert user.content == case.question
    assert "检索候选" in system.content
    assert "delivered_order_count" in system.content
    assert "不是语义授权" in system.content
    assert "dataset.v1.json" not in system.content
    assert "references/sql" not in system.content


def test_retrieval_sql_keeps_same_non_sql_action_contract():
    case = PublicCase(case_id="D14_RU_001", question="2018 年 6 月销售额是多少？")
    adapter, _ = _adapter(
        '{"action":"clarify","sql":null,"parameters":{},'
        '"message":"请明确销售额口径。"}'
    )

    record = adapter.run_case(case)

    assert record.action is DirectAction.CLARIFY
    assert record.generated_sql is None
    assert record.stop_message == "请明确销售额口径。"


def test_retrieval_sql_does_not_correct_invalid_model_output():
    case = PublicCase(case_id="D14_SM_001", question="已送达订单数")
    adapter, client = _adapter("not-json")

    record = adapter.run_case(case)

    assert record.failure_type is DirectAdapterFailure.NON_JSON
    assert record.raw_response == "not-json"
    assert len(client.requests) == 1
    assert record.retrieval_evidence


def test_retrieval_is_deterministic_for_same_question_and_configuration():
    case = PublicCase(case_id="D14_AJ_001", question="按客户州统计已送达订单数")
    adapter, _ = _adapter(
        '{"action":"unanswerable","sql":null,"parameters":{},"message":"test"}'
    )

    first = adapter.retrieve(case)
    second = adapter.retrieve(case)

    assert first == second
    assert [hit.rank for hit in first[0]] == list(range(1, len(first[0]) + 1))
    assert [hit.rank for hit in first[1]] == list(range(1, len(first[1]) + 1))


@pytest.mark.parametrize("field", ["metric_top_k", "schema_top_k"])
def test_retrieval_configuration_rejects_invalid_top_k(field):
    documents = build_documents(PROJECT_ROOT)
    retriever = KeywordRetriever(documents)
    values = {
        "client": FakeModelClient(ModelResponse(content="{}", model_name="fake")),
        "config": ModelConfig(model_name="fake"),
        "public_context": build_direct_sql_public_context(PROJECT_ROOT),
        "retriever": retriever,
        "retriever_version": retriever.version,
        field: 0,
    }
    with pytest.raises(ValueError, match=field):
        RetrievalSqlAdapter(**values)


def test_retrieval_record_does_not_guess_missing_usage():
    case = PublicCase(case_id="D14_SM_001", question="已送达订单数")
    adapter, _ = _adapter(
        '{"action":"sql","sql":"SELECT 1","parameters":{},"message":null}'
    )

    record = adapter.run_case(case)

    assert record.latency_ms is None
    assert record.prompt_tokens is None
    assert record.completion_tokens is None
