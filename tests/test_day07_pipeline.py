import json
import sqlite3
from pathlib import Path

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.day02_database import create_schema
from src.ecommerce_agent.day07_benchmark import validate_cases
from src.ecommerce_agent.day07_live import (
    APPROVED_CASE_IDS,
    RecordingModelClient,
)
from src.ecommerce_agent.day07_pipeline import Day07Agent
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
)
from src.ecommerce_agent.retrieval import KeywordRetriever, build_documents
from src.ecommerce_agent.sql_generation import SqlGenerator


ROOT = Path(__file__).parents[1]


def _catalog():
    return MetricCatalog.from_csv(
        ROOT / "data/metadata/metric_dictionary.csv",
        ROOT / "data/metadata/dimension_dictionary.csv",
    )


def _sample_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    create_schema(connection, str(ROOT / "sql/schema.sql"))
    connection.executescript(
        """
        INSERT INTO dim_customers VALUES
            ('c1', 'u1', '01001', 'city', 'SP'),
            ('c2', 'u2', '20001', 'city', 'RJ');
        INSERT INTO dim_products VALUES
            ('p1', 'category', 10, 100, 1, 200, 10, 5, 8);
        INSERT INTO dim_sellers VALUES
            ('s1', '01001', 'city', 'SP');
        INSERT INTO fact_orders VALUES
            ('o1', 'c1', 'delivered', '2018-06-03', NULL, NULL, NULL,
             '2018-06-10'),
            ('o2', 'c2', 'canceled', '2018-06-04', NULL, NULL, NULL,
             '2018-06-11');
        INSERT INTO fact_order_items VALUES
            ('o1', 1, 'p1', 's1', '2018-06-05', 100, 10),
            ('o2', 1, 'p1', 's1', '2018-06-06', 999, 99);
        """
    )
    connection.commit()
    connection.close()


def _agent(database, question, planning_payload, sql_payload):
    documents = build_documents(ROOT)
    planning_client = FakeModelClient(
        ModelResponse(
            json.dumps(planning_payload, ensure_ascii=False),
            "fake-day07-planner",
        )
    )
    sql_client = FakeModelClient(
        ModelResponse(
            json.dumps(sql_payload, ensure_ascii=False),
            "fake-day07-sql",
        )
    )
    agent = Day07Agent(
        root=ROOT,
        database_path=database,
        documents=documents,
        retriever=KeywordRetriever(documents),
        planner=AnalysisPlanner(
            planning_client,
            ModelConfig(model_name="fake-day07-planner"),
            _catalog(),
            max_output_corrections=0,
            require_retrieval_grounding=True,
        ),
        sql_generator=SqlGenerator(
            sql_client,
            ModelConfig(model_name="fake-day07-sql"),
        ),
        planning_response_source="fake_model_response",
        sql_response_source=(
            "fake_model_response_with_preset_sql_fixture"
        ),
    )
    return agent, planning_client, sql_client


def test_natural_language_reaches_dictionary_rows(tmp_path):
    database = tmp_path / "sample.sqlite3"
    _sample_database(database)
    question = "2018 年 6 月已送达订单 GMV（不含运费）是多少？"
    agent, planning_client, sql_client = _agent(
        database,
        question,
        {
            "status": "ready",
            "plan": {
                "metrics": ["delivered_gmv"],
                "dimensions": [],
                "filters": [],
                "time_range": {
                    "mode": "bounded",
                    "start_date": "2018-06-01",
                    "end_date": "2018-06-30",
                },
            },
            "evidence_document_ids": ["metric:delivered_gmv"],
        },
        {
            "sql": (
                "SELECT SUM(i.price) AS delivered_gmv "
                "FROM fact_orders AS o JOIN fact_order_items AS i "
                "ON o.order_id = i.order_id "
                "WHERE o.order_status = 'delivered' "
                "AND o.order_purchase_timestamp >= :start_date "
                "AND o.order_purchase_timestamp < :end_date_exclusive"
            ),
            "parameters": {
                "start_date": "2018-06-01",
                "end_date_exclusive": "2018-07-01",
            },
        },
    )

    record = agent.run(question)

    assert record["status"] == "succeeded"
    assert record["analysis_plan"]["metrics"] == ["delivered_gmv"]
    assert record["query_result"]["rows"] == [
        {"delivered_gmv": 100}
    ]
    assert record["parameters"]["end_date_exclusive"] == "2018-07-01"
    assert record["metadata"]["database_mode"] == (
        "sqlite_uri_mode_ro_and_query_only"
    )
    assert len(planning_client.requests) == 1
    assert len(sql_client.requests) == 1


def test_missing_retrieved_metric_stops_before_sql_generation(tmp_path):
    database = tmp_path / "sample.sqlite3"
    _sample_database(database)
    question = (
        "2018 年 6 月内，至少完成两笔已送达订单的客户，"
        "占当月已送达客户的比例是多少？"
    )
    agent, _, sql_client = _agent(
        database,
        question,
        {
            "status": "ready",
            "plan": {
                "metrics": ["period_repeat_customer_rate"],
                "dimensions": [],
                "filters": [],
                "time_range": {
                    "mode": "bounded",
                    "start_date": "2018-06-01",
                    "end_date": "2018-06-30",
                },
            },
            "evidence_document_ids": [
                "metric:period_repeat_customer_rate"
            ],
        },
        {"sql": "SELECT 1", "parameters": {}},
    )

    record = agent.run(question)

    assert record["status"] == "failed"
    assert record["error"]["category"] == "plan_grounding"
    assert record["sql"] is None
    assert len(sql_client.requests) == 0


def test_metric_dimension_semantics_stop_before_sql(tmp_path):
    database = tmp_path / "sample.sqlite3"
    _sample_database(database)
    question = "2018 年 6 月按商品品类统计已送达订单支付金额。"
    agent, _, sql_client = _agent(
        database,
        question,
        {
            "status": "ready",
            "plan": {
                "metrics": ["delivered_payment_amount"],
                "dimensions": ["product_category"],
                "filters": [],
                "time_range": {
                    "mode": "bounded",
                    "start_date": "2018-06-01",
                    "end_date": "2018-06-30",
                },
            },
            "evidence_document_ids": [
                "metric:delivered_payment_amount"
            ],
        },
        {"sql": "SELECT 1", "parameters": {}},
    )

    record = agent.run(question)

    assert record["status"] == "failed"
    assert record["error"]["category"] == "business_semantics"
    assert "不支持维度" in record["error"]["message"]
    assert len(sql_client.requests) == 0


def test_day7_fixture_is_exactly_ten_and_discloses_sources():
    cases = json.loads(
        (ROOT / "tests/fixtures/day07/cases.json").read_text(
            encoding="utf-8"
        )
    )["cases"]

    validate_cases(cases)

    assert len(cases) == 10
    assert {case["expected_outcome"] for case in cases} >= {
        "succeeded",
        "retrieval_error",
        "sql_syntax_error",
        "field_error",
        "business_semantics_error",
        "needs_clarification",
    }
    assert APPROVED_CASE_IDS == ("D7_01", "D7_02", "D7_04", "D7_05")


def test_live_recorder_keeps_prompt_and_usage_without_credentials():
    secret = "must-not-appear-in-record"
    response = ModelResponse(
        content='{"status":"needs_clarification"}',
        model_name="fake-live-model",
        latency_ms=12.5,
        prompt_tokens=10,
        completion_tokens=4,
    )
    recorder = RecordingModelClient(
        FakeModelClient(response),
        "planning",
    )

    actual = recorder.generate(
        [],
        ModelConfig(model_name="fake-live-model"),
    )

    assert actual == response
    assert recorder.events[0]["response"]["prompt_tokens"] == 10
    assert secret not in json.dumps(recorder.events)
