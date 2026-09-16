from dataclasses import dataclass, field, replace
from enum import Enum
import time
from typing import Callable, Protocol, Sequence


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"


@dataclass(frozen=True)
class ModelMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class ModelConfig:
    model_name: str
    temperature: float = 0.0
    timeout_seconds: float = 30.0
    max_tokens: int = 1024

    def __post_init__(self):
        if not self.model_name.strip():
            raise ValueError("model_name 不能为空")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature 必须位于 0 到 2 之间")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if self.max_tokens < 1:
            raise ValueError("max_tokens 必须至少为 1")


@dataclass(frozen=True)
class ModelResponse:
    content: str
    model_name: str
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    transport_attempts: int | None = None


class ModelClient(Protocol):
    def generate(
        self,
        messages: Sequence[ModelMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        """根据消息和配置返回统一模型响应。"""


class ModelClientError(RuntimeError):
    """模型客户端可识别错误的基类。"""


class TransientModelError(ModelClientError):
    """超时、限流等可能通过稍后重试恢复的错误。"""


class PermanentModelError(ModelClientError):
    """认证或配置等重复调用无法修复的错误。"""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0

    def __post_init__(self):
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds 不得为负数")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier 不得小于 1")


@dataclass
class RetryingModelClient:
    client: ModelClient
    policy: RetryPolicy
    sleep: Callable[[float], None] = time.sleep

    def generate(
        self,
        messages: Sequence[ModelMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                response = self.client.generate(messages, config)
                return replace(response, transport_attempts=attempt)
            except TransientModelError as error:
                if attempt == self.policy.max_attempts:
                    error.transport_attempts = attempt
                    raise
                delay = (
                    self.policy.initial_backoff_seconds
                    * self.policy.backoff_multiplier ** (attempt - 1)
                )
                self.sleep(delay)

        raise AssertionError("重试循环不应执行到此处")


@dataclass
class FakeModelClient:
    """返回测试预设响应，不进行任何外部 API 调用。"""

    response: ModelResponse
    errors_before_response: list[ModelClientError] = field(
        default_factory=list
    )
    scripted_responses: list[ModelResponse] = field(default_factory=list)
    requests: list[tuple[tuple[ModelMessage, ...], ModelConfig]] = field(
        default_factory=list
    )

    def generate(
        self,
        messages: Sequence[ModelMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        self.requests.append((tuple(messages), config))
        if self.errors_before_response:
            raise self.errors_before_response.pop(0)
        if self.scripted_responses:
            return self.scripted_responses.pop(0)
        return self.response
