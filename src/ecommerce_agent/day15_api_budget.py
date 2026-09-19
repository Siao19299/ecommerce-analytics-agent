"""Shared, persisted API-call and conservative spend budget for Day 15."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from src.ecommerce_agent.model_client import (
    ModelClient,
    ModelClientError,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    PermanentModelError,
)


class ApiBudgetExceeded(PermanentModelError):
    pass


@dataclass
class ApiBudget:
    ledger_path: Path
    maximum_http_calls: int = 480
    maximum_input_tokens: int = 2_400_000
    maximum_output_tokens: int = 180_000
    maximum_cost_usd: float = 0.90
    input_usd_per_million: float = 0.30
    output_usd_per_million: float = 1.20
    usd_cny_reference_rate: float = 6.70
    http_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    conservative_cost_usd: float = 0.0
    events: list[dict[str, object]] = field(default_factory=list)
    operation_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.ledger_path.exists():
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            self.http_calls = payload["http_calls"]
            self.input_tokens = payload["input_tokens"]
            self.output_tokens = payload["output_tokens"]
            self.conservative_cost_usd = payload["conservative_cost_usd"]
            self.events = list(payload["events"])
            limits = payload["limits"]
            expected = self._limits()
            if limits != expected:
                raise ValueError("existing API budget ledger uses different limits")

    def _limits(self) -> dict[str, int | float]:
        return {
            "maximum_http_calls": self.maximum_http_calls,
            "maximum_input_tokens": self.maximum_input_tokens,
            "maximum_output_tokens": self.maximum_output_tokens,
            "maximum_cost_usd": self.maximum_cost_usd,
            "input_usd_per_million": self.input_usd_per_million,
            "output_usd_per_million": self.output_usd_per_million,
            "usd_cny_reference_rate": self.usd_cny_reference_rate,
        }

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.input_usd_per_million
            + completion_tokens * self.output_usd_per_million
        ) / 1_000_000

    @staticmethod
    def _estimated_prompt_tokens(messages: Sequence[ModelMessage]) -> int:
        # Conservative for mixed Chinese/English JSON prompts; used only to
        # reserve the next request before sending it.
        return sum(max(1, len(message.content) // 2) for message in messages)

    def reserve(self, messages: Sequence[ModelMessage], config: ModelConfig) -> None:
        prompt = self._estimated_prompt_tokens(messages)
        projected_input = self.input_tokens + prompt
        projected_output = self.output_tokens + config.max_tokens
        projected_cost = self.conservative_cost_usd + self._cost(
            prompt, config.max_tokens
        )
        if self.http_calls >= self.maximum_http_calls:
            raise ApiBudgetExceeded("authorized HTTP request limit reached")
        if projected_input > self.maximum_input_tokens:
            raise ApiBudgetExceeded("authorized input-token budget would be exceeded")
        if projected_output > self.maximum_output_tokens:
            raise ApiBudgetExceeded("authorized output-token budget would be exceeded")
        if projected_cost > self.maximum_cost_usd:
            raise ApiBudgetExceeded("authorized conservative cost budget would be exceeded")

    def record(
        self,
        response: ModelResponse,
        *,
        stage: str,
        model_id: str,
    ) -> float:
        if response.prompt_tokens is None or response.completion_tokens is None:
            raise ApiBudgetExceeded("provider usage telemetry is missing")
        event_cost = self._cost(response.prompt_tokens, response.completion_tokens)
        self.http_calls += response.transport_attempts or 1
        self.input_tokens += response.prompt_tokens
        self.output_tokens += response.completion_tokens
        self.conservative_cost_usd += event_cost
        self.events.append({
            "sequence": len(self.events) + 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "model_id": model_id,
            "http_attempts": response.transport_attempts or 1,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "conservative_cost_usd": event_cost,
            "response_content_recorded": False,
            "api_key_value_recorded": False,
        })
        self._write()
        if (
            self.http_calls > self.maximum_http_calls
            or self.input_tokens > self.maximum_input_tokens
            or self.output_tokens > self.maximum_output_tokens
            or self.conservative_cost_usd > self.maximum_cost_usd
        ):
            raise ApiBudgetExceeded("provider usage crossed an authorized budget limit")
        return event_cost

    def record_failed_transport(self, *, stage: str, model_id: str) -> None:
        """Count a sent request that failed before usage telemetry was returned."""

        self.http_calls += 1
        self.events.append({
            "sequence": len(self.events) + 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "model_id": model_id,
            "http_attempts": 1,
            "prompt_tokens": None,
            "completion_tokens": None,
            "conservative_cost_usd": None,
            "transport_outcome": "failed_without_usage_telemetry",
            "response_content_recorded": False,
            "api_key_value_recorded": False,
        })
        self._write()
        if self.http_calls > self.maximum_http_calls:
            raise ApiBudgetExceeded("provider attempts crossed the HTTP request limit")

    def _write(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ledger_schema_version": "1.0.0",
            "pricing_basis": "deepseek_flash_peak_cache_miss_conservative_estimate",
            "limits": self._limits(),
            "http_calls": self.http_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "conservative_cost_usd": self.conservative_cost_usd,
            "conservative_cost_cny": (
                self.conservative_cost_usd * self.usd_cny_reference_rate
            ),
            "events": self.events,
        }
        temporary = self.ledger_path.with_name(
            f".{self.ledger_path.name}.{uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.ledger_path)


@dataclass
class BudgetedModelClient:
    client: ModelClient
    budget: ApiBudget
    stage: str

    def generate(
        self,
        messages: Sequence[ModelMessage],
        config: ModelConfig,
    ) -> ModelResponse:
        # Reserve, transport and persistence form one critical section. The
        # live FastAPI server may execute sync requests in multiple worker
        # threads; serializing model calls prevents two requests from both
        # passing the same remaining-budget check.
        with self.budget.operation_lock:
            self.budget.reserve(messages, config)
            try:
                response = self.client.generate(messages, config)
            except ApiBudgetExceeded:
                raise
            except ModelClientError:
                self.budget.record_failed_transport(
                    stage=self.stage, model_id=config.model_name
                )
                raise
            event_cost = self.budget.record(
                response, stage=self.stage, model_id=config.model_name
            )
            return replace(response, cost=event_cost, cost_currency="USD")
