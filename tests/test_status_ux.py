"""Component tests for clarification, failures, and calculation notices."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.ecommerce_agent.api_models import (
    AnalyzeClarificationResponse,
    AnalyzeErrorResponse,
    AnalyzeSuccessResponse,
    ApiErrorDetail,
    PublicFailureStatus,
    RunMetadata,
)
from src.ecommerce_agent.api_client import ApiResponse
from src.ecommerce_agent.error_views import FAILURE_VIEWS
from src.ecommerce_agent.ui import API_CLIENT_KEY


ROOT = Path(__file__).parents[1]


def _metadata(**updates):
    values = {
        "created_at": "2026-09-17T00:00:00Z",
        "workflow_duration_ms": 1,
        "node_count": 4,
        "sql_attempt_count": 0,
        "repair_attempt_count": 0,
        "execution_started": False,
        "rows_truncated": False,
    }
    values.update(updates)
    return RunMetadata.model_validate(values)


class FakeClient:
    def __init__(self, payload, http_status):
        self.result = ApiResponse(http_status=http_status, payload=payload)

    def analyze(self, question):
        return self.result


def _submit(payload, http_status=500):
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=10)
    app.session_state[API_CLIENT_KEY] = FakeClient(payload, http_status)
    app.run()
    app.text_area[0].input("分析经营数据。")
    app.button[0].click().run()
    return app


def test_clarification_is_normal_interaction_and_never_renders_sql():
    response = AnalyzeClarificationResponse(
        run_id="clarification-run",
        clarification_question="销售额具体指商品金额还是支付金额？",
        stop_reason="clarification_required",
        metadata=_metadata(),
    )

    app = _submit(response, 200)

    assert app.exception == []
    assert app.info[0].value == "需要补充信息"
    assert "销售额具体指商品金额还是支付金额？" in [
        item.value for item in app.markdown
    ]
    assert app.code == []
    assert app.dataframe == []


@pytest.mark.parametrize(
    "status,http_status,metadata",
    [
        (PublicFailureStatus.SAFETY_REJECTED, 422, _metadata()),
        (PublicFailureStatus.RESOURCE_FAILED, 503, _metadata()),
        (PublicFailureStatus.ENVIRONMENT_FAILED, 503, _metadata()),
        (
            PublicFailureStatus.REPAIR_LIMIT_REACHED,
            500,
            _metadata(sql_attempt_count=2, repair_attempt_count=1),
        ),
        (
            PublicFailureStatus.CALCULATION_FAILED,
            500,
            _metadata(sql_attempt_count=1, execution_started=True),
        ),
        (PublicFailureStatus.INTERNAL_FAILED, 500, _metadata()),
    ],
)
def test_required_failures_have_distinct_sanitized_component_semantics(
    status, http_status, metadata
):
    response = AnalyzeErrorResponse(
        status=status,
        run_id=f"run-{status.value}",
        stop_reason=f"stop-{status.value}",
        error=ApiErrorDetail(
            code=status.value,
            message=(
                "C:\\Users\\private\\secret Prompt=RAW API_KEY=TOP_SECRET "
                "Traceback"
            ),
            retryable=http_status == 503,
        ),
        metadata=metadata,
    )

    app = _submit(response, http_status)
    rendered = " ".join(
        item.value
        for collection in (
            app.markdown,
            app.caption,
            app.warning,
            app.error,
            app.info,
            app.success,
            app.code,
        )
        for item in collection
    )

    assert app.exception == []
    expected = FAILURE_VIEWS[status]
    notices = app.warning if expected.level == "warning" else app.error
    assert notices[0].value == expected.title
    assert expected.guidance in [item.value for item in app.markdown]
    assert f"run-{status.value}" in " ".join(
        item.value for item in app.caption
    )
    assert "C:\\Users" not in rendered
    assert "TOP_SECRET" not in rendered
    assert "Traceback" not in rendered
    assert app.code == []


def _limited_calculation(status):
    return AnalyzeSuccessResponse.model_validate(
        {
            "run_id": f"run-{status}",
            "answer": "确定性计算保留了边界状态。",
            "sql": {
                "statement": "SELECT :period AS period, 0 AS value",
                "parameters": {"period": "2018-07-01"},
                "sql_attempt": 1,
                "repaired": False,
            },
            "table": {
                "columns": ["period", "value"],
                "rows": [{"period": "2018-07-01", "value": 0.0}],
            },
            "chart": {
                "chart_type": "bar",
                "title": "Boundary state",
                "x_field": "period",
                "y_fields": ["value"],
                "data": [{"period": "2018-07-01", "value": 0.0}],
            },
            "calculation_status": status,
            "stop_reason": "completed",
            "lineage": {
                "parent_run_id": f"run-{status}",
                "source_sql_attempt": 1,
                "calculation_id": f"run-{status}-calculation",
                "input_sha256": "b" * 64,
            },
            "metadata": _metadata(
                sql_attempt_count=1,
                execution_started=True,
            ).model_dump(mode="json"),
        }
    )


@pytest.mark.parametrize(
    "status,expected_fragment",
    [
        ("missing_comparison_period", "没有补零"),
        ("zero_baseline", "不是服务器错误"),
    ],
)
def test_limited_calculation_statuses_remain_success_not_server_errors(
    status, expected_fragment
):
    app = _submit(_limited_calculation(status), 200)

    assert app.exception == []
    assert app.success[0].value == "分析完成"
    assert any(expected_fragment in item.value for item in app.info)
    assert app.metric[0].value == status
    assert app.error == []
