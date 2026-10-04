from pathlib import Path

import httpx

from src.ecommerce_agent.analysis_planner import AnalysisPlanner
from src.ecommerce_agent.deepseek_client import (
    DeepSeekClient,
    DeepSeekCredentials,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.model_client import ModelConfig


PROJECT_ROOT = Path(__file__).parents[1]


def test_mocked_deepseek_response_reaches_validated_plan():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "model": "deepseek-test-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"status":"ready","plan":{'
                                '"metrics":["delivered_gmv"],'
                                '"dimensions":["purchase_month"],'
                                '"filters":[],'
                                '"time_range":{"mode":"all_data"}}}'
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 30,
                },
            },
        )

    catalog = MetricCatalog.from_csv(
        PROJECT_ROOT / "data" / "metadata" / "metric_dictionary.csv",
        PROJECT_ROOT / "data" / "metadata" / "dimension_dictionary.csv",
    )
    planner = AnalysisPlanner(
        client=DeepSeekClient(
            credentials=DeepSeekCredentials(
                api_key="test-only-deepseek-key"
            ),
            transport=httpx.MockTransport(handler),
        ),
        config=ModelConfig(
            model_name="deepseek-test-model",
            temperature=0,
            timeout_seconds=5,
            max_tokens=512,
        ),
        metric_catalog=catalog,
    )

    result = planner.create_plan("分析全部数据中每月的已送达 GMV。")

    assert result.is_success is True
    assert result.validation.plan.metrics == ["delivered_gmv"]
    assert result.validation.plan.dimensions == ["purchase_month"]
    assert result.model_response.prompt_tokens == 100
    assert result.model_response.completion_tokens == 30
    assert result.model_response.finish_reason == "stop"
