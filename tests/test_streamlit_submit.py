"""Streamlit AppTest coverage for submission and rerun behavior."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.ecommerce_agent.api_client import ApiClientFailure, ClientFailureKind
from src.ecommerce_agent.ui import API_CLIENT_KEY


ROOT = Path(__file__).parents[1]


class FakeApiClient:
    def __init__(self):
        self.questions = []

    def analyze(self, question):
        self.questions.append(question)
        return ApiClientFailure(
            kind=ClientFailureKind.TIMEOUT,
            public_message="分析请求超时，请稍后重试。",
            retryable=True,
        )


def test_form_submission_calls_fake_client_once_and_plain_rerun_does_not_repeat():
    fake = FakeApiClient()
    app = AppTest.from_file(ROOT / "streamlit_app.py")
    app.session_state[API_CLIENT_KEY] = fake

    app.run()
    app.text_area[0].input("  分析月度订单量。  ")
    app.button[0].click().run()

    assert fake.questions == ["分析月度订单量。"]
    assert app.exception == []
    assert app.error[0].value == "分析请求超时，请稍后重试。"
    assert "客户端状态：timeout" in app.caption[0].value

    app.run()
    assert fake.questions == ["分析月度订单量。"]


def test_blank_submission_never_calls_client():
    fake = FakeApiClient()
    app = AppTest.from_file(ROOT / "streamlit_app.py")
    app.session_state[API_CLIENT_KEY] = fake

    app.run()
    app.button[0].click().run()

    assert fake.questions == []
    assert app.warning[0].value == "请输入经营问题后再提交。"
    assert app.exception == []
