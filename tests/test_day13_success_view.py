"""Success view-model and Streamlit rendering tests."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.ecommerce_agent.day12_api_models import AnalyzeSuccessResponse, ChartArtifact
from src.ecommerce_agent.day13_api_client import ApiResponse
from src.ecommerce_agent.day13_streamlit import API_CLIENT_KEY
from src.ecommerce_agent.day13_view_models import (
    build_chart_view,
    build_success_view,
    build_table_frame,
)


ROOT = Path(__file__).parents[1]


def _success():
    return AnalyzeSuccessResponse.model_validate(
        {
            "status": "succeeded",
            "run_id": "ui-success-run",
            "answer": "2018 年 7 月已送达商品 GMV 为 120.00。",
            "sql": {
                "statement": "SELECT :start_date AS period, 120 AS value",
                "parameters": {"start_date": "2018-07-01"},
                "sql_attempt": 2,
                "repaired": True,
            },
            "table": {
                "columns": ["period", "value"],
                "rows": [{"period": "2018-07-01", "value": 120.0}],
            },
            "chart": {
                "chart_type": "bar",
                "title": "Monthly GMV",
                "x_field": "period",
                "y_fields": ["value"],
                "data": [{"period": "2018-07-01", "value": 120.0}],
                "notes": ["chart_uses_precomputed_values_only"],
            },
            "calculation_status": "computed",
            "stop_reason": "completed",
            "lineage": {
                "parent_run_id": "ui-success-run",
                "source_sql_attempt": 2,
                "calculation_id": "ui-success-run-calculation",
                "input_sha256": "a" * 64,
            },
            "metadata": {
                "created_at": "2026-09-17T00:00:00Z",
                "workflow_duration_ms": 12.5,
                "node_count": 9,
                "sql_attempt_count": 2,
                "repair_attempt_count": 1,
                "execution_started": True,
                "rows_truncated": False,
            },
        }
    )


class FakeSuccessClient:
    def __init__(self):
        self.calls = 0

    def analyze(self, question):
        self.calls += 1
        return ApiResponse(http_status=200, payload=_success())


def test_success_view_is_only_a_lossless_display_projection():
    response = _success()
    view = build_success_view(response)

    assert view.run_id == response.run_id
    assert view.conclusion == response.answer
    assert view.sql_statement == response.sql.statement
    assert [(row.name, row.value) for row in view.parameters] == [
        ("start_date", "2018-07-01")
    ]
    assert view.calculation_status == "computed"
    assert view.sql_attempt_count == 2


def test_table_and_chart_adapters_preserve_precomputed_api_values():
    response = _success()

    table = build_table_frame(response.table)
    chart = build_chart_view(response.chart)

    assert table.to_dict(orient="records") == list(response.table.rows)
    assert chart.frame.to_dict(orient="records") == list(response.chart.data)
    assert chart.x_field == response.chart.x_field
    assert chart.y_fields == response.chart.y_fields
    assert chart.chart_type == response.chart.chart_type
    assert chart.renderable is True


def test_single_numeric_result_is_kept_as_table_without_forced_chart():
    artifact = ChartArtifact.model_validate({
        "chart_type": "bar",
        "title": "Delivered orders",
        "x_field": "delivered_order_count",
        "y_fields": ["delivered_order_count"],
        "data": [{"delivered_order_count": 96478}],
        "notes": ["single_value"],
    })
    chart = build_chart_view(artifact)
    assert chart.frame.to_dict(orient="records") == [
        {"delivered_order_count": 96478}
    ]
    assert chart.renderable is False


def test_single_numeric_success_page_does_not_crash_chart_renderer():
    payload = _success().model_dump(mode="json")
    payload["table"] = {
        "columns": ["delivered_order_count"],
        "rows": [{"delivered_order_count": 96478}],
    }
    payload["chart"] = {
        "chart_type": "bar",
        "title": "Delivered orders",
        "x_field": "delivered_order_count",
        "y_fields": ["delivered_order_count"],
        "data": [{"delivered_order_count": 96478}],
        "notes": ["single_value"],
    }
    response = AnalyzeSuccessResponse.model_validate(payload)

    class SingleValueClient:
        def analyze(self, question):
            return ApiResponse(http_status=200, payload=response)

    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=10)
    app.session_state[API_CLIENT_KEY] = SingleValueClient()
    app.run()
    app.text_area[0].input("统计已送达订单数量。")
    app.button[0].click().run()

    assert app.exception == []
    assert len(app.get("arrow_vega_lite_chart")) == 0
    assert any("不强制绘图" in item.value for item in app.info)


def test_success_page_shows_conclusion_sql_parameters_and_required_metadata():
    fake = FakeSuccessClient()
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=10)
    app.session_state[API_CLIENT_KEY] = fake

    app.run()
    app.text_area[0].input("分析 2018 年 7 月 GMV。")
    app.button[0].click().run()

    assert fake.calls == 1
    assert app.exception == []
    assert app.success[0].value == "分析完成"
    captions = " ".join(item.value for item in app.caption)
    assert "ui-success-run" in captions
    assert "SQL attempt：2" in captions
    assert app.code[0].value == "SELECT :start_date AS period, 120 AS value"
    assert app.metric[0].value == "computed"
    assert app.metric[1].value == "completed"
    assert len(app.dataframe) == 2
    assert len(app.get("arrow_vega_lite_chart")) == 1
    captions = " ".join(item.value for item in app.caption)
    assert "chart_uses_precomputed_values_only" in captions
    assert "2018 年 7 月已送达商品 GMV 为 120.00。" in [
        item.value for item in app.markdown
    ]
