import json

import httpx
import pytest

from src.ecommerce_agent.deepseek_client import (
    DeepSeekClient,
    DeepSeekCredentials,
)
from src.ecommerce_agent.model_client import (
    MessageRole,
    ModelConfig,
    ModelMessage,
    PermanentModelError,
    TransientModelError,
)


TEST_KEY = "test-only-deepseek-key"


def test_deepseek_client_maps_request_and_response_without_network():
    def handler(request):
        assert request.url.path == "/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {TEST_KEY}"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-test-model"
        assert body["temperature"] == 0
        assert body["max_tokens"] == 512
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"}
        assert body["messages"] == [
            {"role": "system", "content": "只返回 JSON。"},
            {"role": "user", "content": "分析全部 GMV。"},
        ]
        return httpx.Response(
            200,
            json={
                "model": "deepseek-test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"status":"needs_clarification",'
                                '"clarification_question":"请选择时间范围"}'
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 12,
                },
            },
        )

    client = DeepSeekClient(
        credentials=DeepSeekCredentials(api_key=TEST_KEY),
        transport=httpx.MockTransport(handler),
    )
    response = client.generate(
        [
            ModelMessage(MessageRole.SYSTEM, "只返回 JSON。"),
            ModelMessage(MessageRole.USER, "分析全部 GMV。"),
        ],
        ModelConfig(
            model_name="deepseek-test-model",
            temperature=0,
            timeout_seconds=5,
            max_tokens=512,
        ),
    )

    assert response.model_name == "deepseek-test-model"
    assert response.prompt_tokens == 40
    assert response.completion_tokens == 12
    assert response.finish_reason == "stop"
    assert response.latency_ms is not None
    assert response.latency_ms >= 0
    assert TEST_KEY not in repr(client)


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (429, TransientModelError),
        (500, TransientModelError),
        (401, PermanentModelError),
        (400, PermanentModelError),
    ],
)
def test_deepseek_client_classifies_http_errors(
    status_code,
    expected_error,
):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status_code,
            json={"error": "synthetic test error"},
        )
    )
    client = DeepSeekClient(
        credentials=DeepSeekCredentials(api_key=TEST_KEY),
        transport=transport,
    )

    with pytest.raises(expected_error) as error:
        client.generate(
            [],
            ModelConfig(model_name="deepseek-test-model"),
        )

    assert TEST_KEY not in str(error.value)


def test_deepseek_client_classifies_timeout_as_transient():
    def timeout_handler(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    client = DeepSeekClient(
        credentials=DeepSeekCredentials(api_key=TEST_KEY),
        transport=httpx.MockTransport(timeout_handler),
    )

    with pytest.raises(TransientModelError):
        client.generate(
            [],
            ModelConfig(model_name="deepseek-test-model"),
        )
