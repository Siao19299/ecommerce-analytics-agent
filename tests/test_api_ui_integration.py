"""In-process API service API to User interface Streamlit integration tests."""

from pathlib import Path

from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from src.ecommerce_agent.workflow_state import WorkflowState, WorkflowStatus
from src.ecommerce_agent.api import create_app
from src.ecommerce_agent.api_client import HttpAnalysisApiClient
from src.ecommerce_agent.ui import API_CLIENT_KEY


ROOT = Path(__file__).parents[1]


class ClarificationService:
    def __init__(self):
        self.calls = []

    def analyze(self, question, *, run_id=None):
        self.calls.append((question, run_id))
        return WorkflowState(
            run_id=run_id,
            question=question,
            status=WorkflowStatus.NEEDS_CLARIFICATION,
            stop_reason="clarification_required",
            clarification_question="销售额具体指商品金额还是支付金额？",
        )


class LocalTestTransport:
    def __init__(self, client):
        self.client = client

    def post(self, url, **kwargs):
        kwargs.pop("timeout", None)
        return self.client.post(url, **kwargs)


def test_streamlit_submission_crosses_real_fastapi_contract_once():
    service = ClarificationService()
    api_client = HttpAnalysisApiClient(
        LocalTestTransport(TestClient(create_app(service)))
    )
    app = AppTest.from_file(ROOT / "streamlit_app.py", default_timeout=10)
    app.session_state[API_CLIENT_KEY] = api_client

    app.run()
    app.text_area[0].input("分析销售额。")
    app.button[0].click().run()

    assert app.exception == []
    assert len(service.calls) == 1
    question, run_id = service.calls[0]
    assert question == "分析销售额。"
    assert run_id is not None and len(run_id) == 32
    assert app.info[0].value == "需要补充信息"
    captions = " ".join(item.value for item in app.caption)
    assert run_id in captions
    assert "needs_clarification" in captions
    assert app.code == []

    app.run()
    assert len(service.calls) == 1
