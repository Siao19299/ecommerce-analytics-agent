import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.ecommerce_agent.live_runtime import LiveRuntimeSettings, create_live_app
from src.ecommerce_agent.model_client import FakeModelClient, ModelResponse


ROOT = Path(__file__).resolve().parents[1]


def _response(payload):
    return ModelResponse(
        content=json.dumps(payload, ensure_ascii=False),
        model_name="fake-live-demo",
        latency_ms=1,
        prompt_tokens=10,
        completion_tokens=5,
        transport_attempts=1,
    )


def test_live_factory_is_injectable_and_health_does_not_call_model(tmp_path):
    fake = FakeModelClient(_response({"unused": True}))
    app = create_live_app(
        root=ROOT,
        base_client=fake,
        settings=LiveRuntimeSettings(model_id="fake-live-demo"),
        ledger_path=tmp_path / "ledger.json",
    )
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert fake.requests == []


def test_arbitrary_question_can_reach_clarification_without_sqlite(tmp_path):
    fake = FakeModelClient(_response({
        "status": "needs_clarification",
        "clarification_question": "销售额是指不含运费 GMV、含运费成交额，还是支付金额？",
    }))
    ledger = tmp_path / "ledger.json"
    app = create_live_app(
        root=ROOT,
        base_client=fake,
        settings=LiveRuntimeSettings(
            model_id="fake-live-demo",
            maximum_http_calls=3,
            maximum_input_tokens=10_000,
            maximum_output_tokens=2_000,
            maximum_cost_usd=0.01,
        ),
        ledger_path=ledger,
    )
    response = TestClient(app).post(
        "/analyze", json={"question": "2018 年 6 月的销售额是多少？"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_clarification"
    assert body["sql"] is None
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    assert payload["http_calls"] == 1
    assert "销售额" not in ledger.read_text(encoding="utf-8")
    assert payload["events"][0]["api_key_value_recorded"] is False


def test_live_budget_defaults_are_small_and_explicit():
    settings = LiveRuntimeSettings()
    assert settings.maximum_http_calls == 40
    assert settings.maximum_cost_usd == 0.15
    assert settings.temperature == 0
