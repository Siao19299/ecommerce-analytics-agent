from pathlib import Path

import pytest

from src.ecommerce_agent.baseline_direct_sql import (
    DirectAction,
    DirectAdapterFailure,
    DirectSqlAdapter,
    build_direct_sql_public_context,
)
from src.ecommerce_agent.reproducibility import PublicCase
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    ModelConfig,
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)


PROJECT_ROOT = Path(__file__).parents[1]
CASE = PublicCase(case_id="D14_SM_001", question="全部数据中有多少笔已送达订单？")


def _adapter(response: str, **metadata):
    client = FakeModelClient(
        ModelResponse(content=response, model_name="fake-direct", **metadata)
    )
    adapter = DirectSqlAdapter(
        client=client,
        config=ModelConfig(model_name="fake-direct"),
        public_context=build_direct_sql_public_context(PROJECT_ROOT),
    )
    return adapter, client


def test_direct_baseline_uses_one_call_and_schema_only_context():
    adapter, client = _adapter(
        '{"action":"sql","sql":"SELECT COUNT(*) AS n FROM fact_orders",'
        '"parameters":{},"message":null}',
        latency_ms=12.5,
        prompt_tokens=300,
        completion_tokens=30,
        transport_attempts=1,
    )

    record = adapter.run_case(CASE)

    assert record.action is DirectAction.SQL
    assert record.generated_sql == "SELECT COUNT(*) AS n FROM fact_orders"
    assert record.model_call_count == 1
    assert record.transport_attempt_count == 1
    assert record.latency_ms == 12.5
    assert record.prompt_tokens == 300
    assert record.completion_tokens == 30
    assert len(client.requests) == 1
    system, user = client.requests[0][0]
    assert user.content == CASE.question
    assert "fact_orders" in system.content
    assert "customer_unique_id" in system.content
    assert "metric_dictionary" not in system.content
    assert "references/sql" not in system.content.lower()
    assert "dataset.v1.json" not in system.content.lower()
    assert "sql_reference" not in system.content.lower()
    assert "expected_workflow_status" not in system.content


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ("clarify", "请明确销售额口径。"),
        ("refuse", "该请求会修改数据。"),
        ("unanswerable", "现有字段不支持该分析。"),
    ],
)
def test_direct_baseline_can_stop_without_fabricating_sql(action, message):
    adapter, _ = _adapter(
        f'{{"action":"{action}","sql":null,"parameters":{{}},'
        f'"message":"{message}"}}'
    )

    record = adapter.run_case(CASE)

    assert record.action.value == action
    assert record.generated_sql is None
    assert record.named_parameters == {}
    assert record.stop_message == message


@pytest.mark.parametrize(
    ("response", "failure"),
    [
        ("not-json", DirectAdapterFailure.NON_JSON),
        (
            '{"action":"sql","sql":null,"parameters":{},"message":null}',
            DirectAdapterFailure.INVALID_STRUCTURE,
        ),
        (
            '{"action":"clarify","sql":"SELECT 1","parameters":{},'
            '"message":"clarify"}',
            DirectAdapterFailure.INVALID_STRUCTURE,
        ),
        (
            '{"action":"sql","sql":"SELECT 1","parameters":{},'
            '"message":null,"gold":"hidden"}',
            DirectAdapterFailure.INVALID_STRUCTURE,
        ),
    ],
)
def test_direct_baseline_preserves_raw_invalid_output_without_correction(
    response, failure
):
    adapter, client = _adapter(response)

    record = adapter.run_case(CASE)

    assert record.failure_type is failure
    assert record.raw_response == response
    assert record.raw_response_sha256 is not None
    assert record.generated_sql is None
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (TransientModelError("timeout secret detail"), DirectAdapterFailure.TRANSIENT_MODEL_ERROR),
        (PermanentModelError("key secret detail"), DirectAdapterFailure.PERMANENT_MODEL_ERROR),
    ],
)
def test_direct_baseline_maps_model_errors_without_exposing_exception(error, failure):
    client = FakeModelClient(
        response=ModelResponse(content="{}", model_name="unused"),
        errors_before_response=[error],
    )
    adapter = DirectSqlAdapter(
        client=client,
        config=ModelConfig(model_name="fake-direct"),
        public_context=build_direct_sql_public_context(PROJECT_ROOT),
    )

    record = adapter.run_case(CASE)

    assert record.failure_type is failure
    assert "secret detail" not in record.failure_message
    assert record.raw_response is None
    assert len(client.requests) == 1


def test_unknown_usage_stays_unavailable_instead_of_being_guessed():
    adapter, _ = _adapter(
        '{"action":"sql","sql":"SELECT 1","parameters":{},"message":null}'
    )

    record = adapter.run_case(CASE)

    assert record.latency_ms is None
    assert record.prompt_tokens is None
    assert record.completion_tokens is None
