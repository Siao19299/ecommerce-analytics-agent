import pytest

from src.ecommerce_agent.deepseek_client import DeepSeekCredentials
from src.ecommerce_agent.model_client import PermanentModelError


def test_credentials_are_read_from_environment_and_hidden_from_repr(
    monkeypatch,
):
    test_key = "test-only-secret-value"
    monkeypatch.setenv("DEEPSEEK_API_KEY", test_key)

    credentials = DeepSeekCredentials.from_environment()

    assert credentials.api_key == test_key
    assert test_key not in repr(credentials)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_credentials_fail_without_exposing_value(
    monkeypatch,
    value,
):
    if value is None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    else:
        monkeypatch.setenv("DEEPSEEK_API_KEY", value)

    with pytest.raises(
        PermanentModelError,
        match="DEEPSEEK_API_KEY",
    ) as error:
        DeepSeekCredentials.from_environment()

    assert str(error.value) == (
        "缺少模型凭据环境变量：DEEPSEEK_API_KEY"
    )
