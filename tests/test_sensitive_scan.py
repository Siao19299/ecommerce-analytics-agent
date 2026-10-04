from pathlib import Path

from src.ecommerce_agent.sensitive_scan import scan_repository_text


ROOT = Path(__file__).resolve().parents[1]


def test_repository_scan_finds_no_committed_or_candidate_secret_values():
    report = scan_repository_text(ROOT)
    assert report["passed"] is True
    assert report["finding_count"] == 0
    assert report["api_key_values_read"] == 0


def test_scanner_never_opens_dotenv_values(tmp_path):
    key_name = "DEEPSEEK" + "_API_KEY"
    (tmp_path / ".env").write_text(key_name + "=do-not-read-this-value")
    (tmp_path / "safe.py").write_text("name = 'DEEPSEEK_API_KEY'")
    report = scan_repository_text(tmp_path)
    assert report["passed"] is True
    assert report["excluded_secret_files"] == [".env"]
    assert report["api_key_values_read"] == 0
