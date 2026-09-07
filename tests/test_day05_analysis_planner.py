from dataclasses import asdict
from pathlib import Path

from src.ecommerce_agent.analysis_plan import PlanValidationErrorType
from src.ecommerce_agent.analysis_planner import (
    AnalysisPlanner,
    PlanningErrorType,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    MessageRole,
    ModelConfig,
    ModelResponse,
    PermanentModelError,
    RetryPolicy,
    RetryingModelClient,
    TransientModelError,
)


PROJECT_ROOT = Path(__file__).parents[1]


def load_catalog():
    return MetricCatalog.from_csv(
        PROJECT_ROOT / "data" / "metadata" / "metric_dictionary.csv",
        PROJECT_ROOT / "data" / "metadata" / "dimension_dictionary.csv",
    )


def test_planner_converts_question_to_validated_plan_with_fake_client():
    response = ModelResponse(
        content="""
        {
          "status": "ready",
          "plan": {
            "metrics": ["delivered_gmv"],
            "dimensions": ["purchase_month"],
            "filters": [
              {
                "field": "customer_state",
                "operator": "eq",
                "value": "SP"
              }
            ],
            "time_range": {
              "mode": "bounded",
              "start_date": "2018-01-01",
              "end_date": "2018-06-30"
            }
          }
        }
        """,
        model_name="fake-test-model",
    )
    client = FakeModelClient(response)
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(
            model_name="fake-test-model",
            temperature=0,
            timeout_seconds=5,
        ),
        metric_catalog=load_catalog(),
    )

    result = planner.create_plan(
        "分析 2018 年 1 月至 6 月 SP 州每月的已送达 GMV。"
    )

    assert result.validation.is_success is True
    assert result.validation.plan.metrics == ["delivered_gmv"]
    assert result.validation.plan.dimensions == ["purchase_month"]
    assert result.model_response.model_name == "fake-test-model"

    sent_messages, sent_config = client.requests[0]
    assert sent_messages[0].role == MessageRole.SYSTEM
    assert "可用 metric_id" in sent_messages[0].content
    assert "JSON Schema" in sent_messages[0].content
    assert '"eq"' in sent_messages[0].content
    assert '"bounded"' in sent_messages[0].content
    assert sent_messages[1].role == MessageRole.USER
    assert "SP 州" in sent_messages[1].content
    assert sent_config.temperature == 0


def test_planner_returns_controlled_failure_for_non_json_model_output():
    client = FakeModelClient(
        ModelResponse(
            content="我无法生成分析计划。",
            model_name="fake-test-model",
        )
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-test-model"),
        metric_catalog=load_catalog(),
    )

    result = planner.create_plan("分析全部数据中的已送达 GMV。")

    assert result.validation.is_success is False
    assert (
        result.validation.error_type
        == PlanValidationErrorType.NON_JSON
    )
    assert len(result.model_responses) == 2


def test_planner_corrects_invalid_output_once_then_succeeds():
    invalid_response = ModelResponse(
        content="这不是 JSON",
        model_name="fake-test-model",
    )
    valid_response = ModelResponse(
        content="""
        {
          "status": "ready",
          "plan": {
            "metrics": ["delivered_gmv"],
            "dimensions": [],
            "filters": [],
            "time_range": {"mode": "all_data"}
          }
        }
        """,
        model_name="fake-test-model",
    )
    client = FakeModelClient(
        response=valid_response,
        scripted_responses=[invalid_response, valid_response],
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-test-model"),
        metric_catalog=load_catalog(),
        max_output_corrections=1,
    )

    result = planner.create_plan("分析全部数据中的已送达 GMV。")

    assert result.validation.is_success is True
    assert len(result.model_responses) == 2
    assert len(client.requests) == 2
    second_messages, _ = client.requests[1]
    assert "错误类型：non_json" in second_messages[-1].content


def test_planner_records_safe_metadata_without_prompt_or_response_text():
    client = FakeModelClient(
        ModelResponse(
            content="""
            {
              "status": "ready",
              "plan": {
                "metrics": ["delivered_gmv"],
                "dimensions": [],
                "filters": [],
                "time_range": {"mode": "all_data"}
              }
            }
            """,
            model_name="fake-test-model",
            latency_ms=125.0,
            prompt_tokens=40,
            completion_tokens=20,
        )
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-test-model"),
        metric_catalog=load_catalog(),
    )

    result = planner.create_plan("测试问题原文不应进入日志")
    record = asdict(result.log_records[0])

    assert record["run_id"] == result.run_id
    assert record["model_name"] == "fake-test-model"
    assert record["latency_ms"] == 125.0
    assert record["prompt_tokens"] == 40
    assert record["completion_tokens"] == 20
    assert record["status"] == "succeeded"
    assert record["error_type"] is None
    assert "content" not in record
    assert "question" not in record
    assert "api_key" not in record


def test_planner_returns_structured_clarification_without_guessing():
    client = FakeModelClient(
        ModelResponse(
            content="""
            {
              "status": "needs_clarification",
              "clarification_question": "请指定分析时间范围，或确认使用全部数据。"
            }
            """,
            model_name="fake-test-model",
        )
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-test-model"),
        metric_catalog=load_catalog(),
    )

    result = planner.create_plan("分析 GMV。")

    assert result.validation.is_success is True
    assert result.validation.needs_clarification is True
    assert result.validation.plan is None
    assert result.validation.clarification_question == (
        "请指定分析时间范围，或确认使用全部数据。"
    )
    assert len(result.model_responses) == 1


def test_planner_controls_transient_error_after_retries_are_exhausted():
    base_client = FakeModelClient(
        response=ModelResponse(
            content="{}",
            model_name="fake-test-model",
        ),
        errors_before_response=[
            TransientModelError("timeout 1"),
            TransientModelError("timeout 2"),
        ],
    )
    client = RetryingModelClient(
        client=base_client,
        policy=RetryPolicy(
            max_attempts=2,
            initial_backoff_seconds=0,
        ),
        sleep=lambda _: None,
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-test-model"),
        metric_catalog=load_catalog(),
    )

    result = planner.create_plan("分析全部数据中的已送达 GMV。")

    assert result.is_success is False
    assert result.validation is None
    assert result.model_response is None
    assert (
        result.client_error_type
        == PlanningErrorType.TRANSIENT_MODEL_CLIENT_ERROR
    )
    assert result.log_records[0].latency_ms is None
    assert len(base_client.requests) == 2


def test_planner_controls_permanent_error_without_retry():
    client = FakeModelClient(
        response=ModelResponse(
            content="{}",
            model_name="fake-test-model",
        ),
        errors_before_response=[
            PermanentModelError("invalid api key"),
        ],
    )
    planner = AnalysisPlanner(
        client=client,
        config=ModelConfig(model_name="fake-test-model"),
        metric_catalog=load_catalog(),
    )

    result = planner.create_plan("分析全部数据中的已送达 GMV。")

    assert result.is_success is False
    assert result.validation is None
    assert (
        result.client_error_type
        == PlanningErrorType.PERMANENT_MODEL_CLIENT_ERROR
    )
    assert result.client_error_message == "模型凭据或配置无效"
    assert len(client.requests) == 1
