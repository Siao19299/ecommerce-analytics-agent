import os
from dataclasses import dataclass, field
from time import perf_counter
from typing import Sequence

import httpx

from src.ecommerce_agent.model_client import (
    ModelConfig,
    ModelMessage,
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)


@dataclass(frozen=True)
class DeepSeekCredentials:
    api_key: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        variable_name: str = "DEEPSEEK_API_KEY",
    ) -> "DeepSeekCredentials":
        api_key = os.environ.get(variable_name)
        if api_key is None or not api_key.strip():
            raise PermanentModelError(
                f"缺少模型凭据环境变量：{variable_name}"
            )
        return cls(api_key=api_key)


@dataclass
class DeepSeekClient:
    credentials: DeepSeekCredentials = field(repr=False)
    base_url: str = "https://api.deepseek.com"
    transport: httpx.BaseTransport | None = field(
        default=None,
        repr=False,
    )

    def generate(
        self,
        messages: Sequence[ModelMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        request_body = {
            "model": config.model_name,
            "messages": [
                {
                    "role": message.role.value,
                    "content": message.content,
                }
                for message in messages
            ],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        started_at = perf_counter()

        try:
            with httpx.Client(
                base_url=self.base_url,
                transport=self.transport,
                timeout=config.timeout_seconds,
            ) as client:
                response = client.post(
                    "/chat/completions",
                    headers={
                        "Authorization": (
                            f"Bearer {self.credentials.api_key}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise TransientModelError(
                "模型服务网络请求失败"
            ) from error

        if response.status_code == 429 or response.status_code >= 500:
            raise TransientModelError(
                f"模型服务暂时不可用，HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise PermanentModelError(
                f"模型请求或凭据无效，HTTP {response.status_code}"
            )

        try:
            payload = response.json()
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise TransientModelError(
                "模型服务返回了无法识别的响应结构"
            ) from error

        if not isinstance(content, str):
            raise TransientModelError("模型响应 content 不是字符串")

        usage = payload.get("usage") or {}
        return ModelResponse(
            content=content,
            model_name=payload.get("model") or config.model_name,
            latency_ms=(perf_counter() - started_at) * 1000,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(
                usage.get("completion_tokens")
            ),
            finish_reason=_optional_str(choice.get("finish_reason")),
            transport_attempts=1,
        )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
