import pytest

from src.ecommerce_agent.model_client import (
    FakeModelClient,
    MessageRole,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    PermanentModelError,
    RetryPolicy,
    RetryingModelClient,
    TransientModelError,
)


def test_fake_client_returns_unified_response_and_records_request():
    response = ModelResponse(
        content='{"metrics": ["delivered_gmv"]}',
        model_name="fake-test-model",
    )
    client = FakeModelClient(response=response)
    messages = [
        ModelMessage(
            role=MessageRole.SYSTEM,
            content="只返回 AnalysisPlan JSON。",
        ),
        ModelMessage(
            role=MessageRole.USER,
            content="分析已送达 GMV。",
        ),
    ]
    config = ModelConfig(
        model_name="fake-test-model",
        temperature=0,
        timeout_seconds=5,
    )

    actual = client.generate(messages, config)

    assert actual == response
    assert client.requests == [(tuple(messages), config)]
    assert actual.latency_ms is None
    assert actual.prompt_tokens is None
    assert actual.completion_tokens is None


@pytest.mark.parametrize(
    "config",
    [
        {"model_name": ""},
        {"model_name": "test", "temperature": -0.1},
        {"model_name": "test", "temperature": 2.1},
        {"model_name": "test", "timeout_seconds": 0},
        {"model_name": "test", "max_tokens": 0},
    ],
)
def test_model_config_rejects_invalid_values(config):
    with pytest.raises(ValueError):
        ModelConfig(**config)


def test_retrying_client_retries_transient_errors_with_backoff():
    base_client = FakeModelClient(
        response=ModelResponse(
            content="{}",
            model_name="fake-test-model",
        ),
        errors_before_response=[
            TransientModelError("timeout"),
            TransientModelError("rate limit"),
        ],
    )
    delays = []
    client = RetryingModelClient(
        client=base_client,
        policy=RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=0.5,
            backoff_multiplier=2,
        ),
        sleep=delays.append,
    )

    response = client.generate(
        [],
        ModelConfig(model_name="fake-test-model"),
    )

    assert response.content == "{}"
    assert len(base_client.requests) == 3
    assert delays == [0.5, 1.0]


def test_retrying_client_does_not_retry_permanent_error():
    base_client = FakeModelClient(
        response=ModelResponse(
            content="{}",
            model_name="fake-test-model",
        ),
        errors_before_response=[
            PermanentModelError("invalid api key"),
        ],
    )
    delays = []
    client = RetryingModelClient(
        client=base_client,
        policy=RetryPolicy(max_attempts=3),
        sleep=delays.append,
    )

    with pytest.raises(PermanentModelError):
        client.generate([], ModelConfig(model_name="fake-test-model"))

    assert len(base_client.requests) == 1
    assert delays == []


def test_retrying_client_stops_after_max_attempts():
    base_client = FakeModelClient(
        response=ModelResponse(
            content="{}",
            model_name="fake-test-model",
        ),
        errors_before_response=[
            TransientModelError("timeout 1"),
            TransientModelError("timeout 2"),
            TransientModelError("timeout 3"),
        ],
    )
    client = RetryingModelClient(
        client=base_client,
        policy=RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=0,
        ),
        sleep=lambda _: None,
    )

    with pytest.raises(TransientModelError):
        client.generate([], ModelConfig(model_name="fake-test-model"))

    assert len(base_client.requests) == 3
