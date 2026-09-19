import json

import pytest

from src.ecommerce_agent.day15_api_budget import (
    ApiBudget,
    ApiBudgetExceeded,
    BudgetedModelClient,
)
from src.ecommerce_agent.model_client import (
    FakeModelClient,
    MessageRole,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    TransientModelError,
)


def test_budget_records_usage_cost_without_prompt_response_or_key(tmp_path):
    budget = ApiBudget(tmp_path / "ledger.json")
    response = ModelResponse(
        content="secret candidate output",
        model_name="deepseek-flash",
        prompt_tokens=1000,
        completion_tokens=100,
        transport_attempts=1,
    )
    client = BudgetedModelClient(
        FakeModelClient(response), budget, "direct_sql"
    )
    result = client.generate(
        (ModelMessage(MessageRole.USER, "public question"),),
        ModelConfig(model_name="deepseek-flash", max_tokens=200),
    )
    assert result.cost == pytest.approx(0.00042)
    assert result.cost_currency == "USD"
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["http_calls"] == 1
    serialized = json.dumps(ledger)
    assert "secret candidate output" not in serialized
    assert "public question" not in serialized
    assert "api_key" not in serialized.lower() or "api_key_value_recorded" in serialized


def test_budget_reserves_worst_case_next_response_before_call(tmp_path):
    budget = ApiBudget(
        tmp_path / "ledger.json",
        maximum_cost_usd=0.000001,
    )
    client = BudgetedModelClient(
        FakeModelClient(ModelResponse(content="{}", model_name="fake")),
        budget,
        "test",
    )
    with pytest.raises(ApiBudgetExceeded):
        client.generate(
            (ModelMessage(MessageRole.USER, "question"),),
            ModelConfig(model_name="fake", max_tokens=100),
        )
    assert client.client.requests == []


def test_missing_usage_stops_after_one_unmetered_response(tmp_path):
    budget = ApiBudget(tmp_path / "ledger.json")
    client = BudgetedModelClient(
        FakeModelClient(ModelResponse(content="{}", model_name="fake")),
        budget,
        "test",
    )
    with pytest.raises(ApiBudgetExceeded, match="telemetry"):
        client.generate(
            (ModelMessage(MessageRole.USER, "question"),),
            ModelConfig(model_name="fake", max_tokens=10),
        )


def test_failed_transport_is_counted_without_inventing_tokens_or_cost(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    budget = ApiBudget(ledger_path, maximum_http_calls=2)
    fake = FakeModelClient(
        ModelResponse(content="{}", model_name="fake"),
        errors_before_response=[TransientModelError("temporary")],
    )
    client = BudgetedModelClient(fake, budget, "planning")
    with pytest.raises(TransientModelError):
        client.generate(
            (ModelMessage(MessageRole.USER, "question"),),
            ModelConfig(model_name="fake", max_tokens=10),
        )
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert payload["http_calls"] == 1
    assert payload["input_tokens"] == 0
    assert payload["conservative_cost_usd"] == 0
    assert payload["events"][0]["transport_outcome"] == (
        "failed_without_usage_telemetry"
    )
