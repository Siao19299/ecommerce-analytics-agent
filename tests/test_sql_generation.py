import sqlite3
from pathlib import Path

import pytest

from src.ecommerce_agent.analysis_plan import validate_analysis_plan
from src.ecommerce_agent.analysis_plan import PlanValidationErrorType
from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
)
from src.ecommerce_agent.retrieval import (
    KeywordRetriever,
    RetrievalFilter,
    build_documents,
)
from src.ecommerce_agent.sql_generation import (
    QueryExecutionErrorType,
    SqlGenerationErrorType,
    SqlGenerator,
    build_sql_generation_context,
    execute_read_only_query,
    rows_to_dicts,
    validate_single_read_only_statement,
)
from src.ecommerce_agent.sql_safety import SqlSafetyPolicy


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def catalog():
    return MetricCatalog.from_csv(
        ROOT / "data/metadata/metric_dictionary.csv",
        ROOT / "data/metadata/dimension_dictionary.csv",
    )


@pytest.fixture(scope="module")
def documents():
    return build_documents(ROOT)


def _gmv_plan(catalog):
    return validate_analysis_plan(
        {
            "metrics": ["delivered_gmv"],
            "dimensions": ["customer_state"],
            "filters": [],
            "time_range": {
                "mode": "bounded",
                "start_date": "2018-06-01",
                "end_date": "2018-06-30",
            },
        },
        catalog,
    )


def _retrieval_hits(documents):
    retriever = KeywordRetriever(documents)
    question = "2018 年 6 月各客户州已送达 GMV（不含运费）"
    return retriever.search(
        question,
        top_k=5,
        filters=RetrievalFilter(
            document_types=frozenset({"metric"})
        ),
    ) + retriever.search(
        question,
        top_k=8,
        filters=RetrievalFilter(
            document_types=frozenset({"schema"})
        ),
    )


def test_sql_context_hydrates_full_canonical_semantics(
    catalog,
    documents,
):
    plan = _gmv_plan(catalog)
    context = build_sql_generation_context(
        ROOT,
        "2018 年 6 月各客户州已送达 GMV（不含运费）",
        plan,
        _retrieval_hits(documents),
        documents,
    )

    metric = context.metric_definitions[0]
    assert metric["metric_id"] == "delivered_gmv"
    assert metric["formula"] == "SUM(fact_order_items.price)"
    assert metric["default_time_field"] == "order_purchase_timestamp"
    assert "预聚合到 order_id" in metric["constraints"]
    assert metric["base_grain"] == "订单明细"
    assert "customer_state" in metric["available_dimensions"]
    assert {
        "fact_orders.order_purchase_timestamp",
        "fact_order_items.price",
        "dim_customers.customer_state",
    } <= {row["qualified_name"] for row in context.schema_fields}
    assert {row["table"] for row in context.table_context} == {
        "fact_orders",
        "fact_order_items",
        "dim_customers",
    }
    assert context.parameter_contract == {
        "start_date": "2018-06-01",
        "end_date_exclusive": "2018-07-01",
    }
    assert "not arbitrary join permission" in context.field_scope


def test_planner_receives_retrieved_definitions_not_only_ids(
    catalog,
    documents,
):
    client = FakeModelClient(
        ModelResponse(
            content=(
                '{"status":"ready","plan":{"metrics":'
                '["delivered_gmv"],"dimensions":[],"filters":[],'
                '"time_range":{"mode":"all_data"}}}'
            ),
            model_name="fake-query-planner",
        )
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-query-planner"),
        metric_catalog=catalog,
    )

    planner.create_plan(
        "全部数据中已送达 GMV",
        _retrieval_hits(documents),
    )

    system = client.requests[0][0][0].content
    assert "本次检索候选" in system
    assert "SUM(fact_order_items.price)" in system
    assert "检索内容只是候选，不是语义授权" in system


def test_grounded_planner_exposes_only_retrieved_metrics_and_cites_them(
    catalog,
    documents,
):
    hits = _retrieval_hits(documents)
    response = ModelResponse(
        content=(
            '{"status":"ready","plan":{"metrics":'
            '["delivered_gmv"],"dimensions":[],"filters":[],'
            '"time_range":{"mode":"all_data"}},'
            '"evidence_document_ids":["metric:delivered_gmv"]}'
        ),
        model_name="fake-query-planner",
    )
    client = FakeModelClient(response)
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-query-planner"),
        metric_catalog=catalog,
        max_output_corrections=0,
        require_retrieval_grounding=True,
    )

    result = planner.create_plan("全部数据中已送达 GMV", hits)

    assert result.is_success is True
    assert result.validation.evidence_document_ids == (
        "metric:delivered_gmv",
    )
    system = client.requests[0][0][0].content
    assert "本次只允许选择以下已召回 metric_id" in system
    assert "period_repeat_customer_rate" not in system


def test_grounding_rejects_known_metric_outside_retrieval(
    catalog,
    documents,
):
    hits = _retrieval_hits(documents)
    client = FakeModelClient(
        ModelResponse(
            content=(
                '{"status":"ready","plan":{"metrics":'
                '["period_repeat_customer_rate"],"dimensions":[],'
                '"filters":[],"time_range":{"mode":"all_data"}},'
                '"evidence_document_ids":'
                '["metric:period_repeat_customer_rate"]}'
            ),
            model_name="fake-query-planner",
        )
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-query-planner"),
        metric_catalog=catalog,
        max_output_corrections=0,
        require_retrieval_grounding=True,
    )

    result = planner.create_plan("全部数据中已送达 GMV", hits)

    assert result.is_success is False
    assert result.validation.error_type == PlanValidationErrorType.UNGROUNDED
    assert "证据不在召回集合" in result.validation.error_message


def test_sql_generator_requires_exact_named_parameter_contract(
    catalog,
    documents,
):
    context = build_sql_generation_context(
        ROOT,
        "2018 年 6 月各客户州已送达 GMV（不含运费）",
        _gmv_plan(catalog),
        _retrieval_hits(documents),
        documents,
    )
    response = ModelResponse(
        content=(
            '{"sql":"SELECT :start_date AS start_date, '
            ':end_date_exclusive AS end_date_exclusive",'
            '"parameters":{"start_date":"2018-06-01",'
            '"end_date_exclusive":"2018-07-01"}}'
        ),
        model_name="fake-query-sql",
    )
    client = FakeModelClient(response)
    result = SqlGenerator(
        client,
        ModelConfig(model_name="fake-query-sql"),
    ).generate(context)

    assert result.is_success is True
    system = client.requests[0][0][0].content
    assert "metric_definitions" in system
    assert "schema_fields" in system
    assert "parameter_contract" in system

    missing_parameter = FakeModelClient(
        ModelResponse(
            content=(
                '{"sql":"SELECT :start_date",'
                '"parameters":{"start_date":"2018-06-01"}}'
            ),
            model_name="fake-query-sql",
        )
    )
    invalid = SqlGenerator(
        missing_parameter,
        ModelConfig(model_name="fake-query-sql"),
    ).generate(context)
    assert invalid.error_type == SqlGenerationErrorType.PARAMETER_MISMATCH


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM fact_orders",
        "SELECT 1; SELECT 2",
        "/* open comment SELECT 1",
    ],
)
def test_statement_guard_rejects_non_read_only_or_multiple_sql(sql):
    with pytest.raises(ValueError):
        validate_single_read_only_statement(sql)


def test_statement_guard_allows_one_query_and_semicolon_inside_value():
    validate_single_read_only_statement("SELECT ';' AS value;")
    validate_single_read_only_statement("WITH value AS (SELECT 1) SELECT * FROM value")


def test_rows_convert_to_dicts_and_executor_is_read_only(tmp_path):
    database = tmp_path / "sample.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample (id INTEGER, name TEXT)")
    connection.executemany(
        "INSERT INTO sample VALUES (?, ?)",
        [(1, "alpha"), (2, "beta")],
    )
    connection.commit()
    cursor = connection.execute(
        "SELECT id, name FROM sample ORDER BY id"
    )
    assert rows_to_dicts(cursor) == [
        {"id": 1, "name": "alpha"},
        {"id": 2, "name": "beta"},
    ]
    connection.close()

    result = execute_read_only_query(
        database,
        "SELECT id, name FROM sample WHERE id >= :minimum ORDER BY id",
        {"minimum": 2},
        safety_policy=SqlSafetyPolicy(
            global_schema={"sample": {"id", "name"}},
            plan_schema={"sample": {"id", "name"}},
        ),
    )
    assert result.is_success is True
    assert list(result.rows) == [{"id": 2, "name": "beta"}]

    rejected = execute_read_only_query(
        database,
        "UPDATE sample SET name = 'changed'",
        safety_policy=SqlSafetyPolicy(
            global_schema={"sample": {"id", "name"}},
            plan_schema={"sample": {"id", "name"}},
        ),
    )
    assert rejected.error_type == QueryExecutionErrorType.SAFETY
    check = sqlite3.connect(database).execute(
        "SELECT name FROM sample ORDER BY id"
    ).fetchall()
    assert check == [("alpha",), ("beta",)]


def test_executor_distinguishes_syntax_and_field_errors(tmp_path):
    database = tmp_path / "errors.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample (id INTEGER)")
    connection.close()

    policy = SqlSafetyPolicy(
        global_schema={"sample": {"id"}},
        plan_schema={"sample": {"id"}},
    )
    syntax = execute_read_only_query(
        database,
        "SELECT FROM sample",
        safety_policy=policy,
    )
    field = execute_read_only_query(
        database,
        "SELECT missing FROM sample",
        safety_policy=policy,
    )

    assert syntax.error_type == QueryExecutionErrorType.SAFETY
    assert syntax.execution_started is False
    assert field.error_type == QueryExecutionErrorType.SAFETY
    assert field.execution_started is False
