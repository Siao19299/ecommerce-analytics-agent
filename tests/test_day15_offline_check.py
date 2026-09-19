import json
from pathlib import Path

from src.ecommerce_agent.day15_offline_check import run_offline_check


ROOT = Path(__file__).resolve().parents[1]


def test_offline_check_replays_frozen_assets_without_model_calls():
    report = run_offline_check(ROOT)
    assert report["accepted"] is True
    assert report["case_count"] == 60
    assert all(report["checks"].values())
    assert report["candidate_system"] is None
    assert report["candidate_model_runs"] == 0
    assert report["external_api_calls"] == 0
    assert report["prompt_tokens"] == 0
    assert report["completion_tokens"] == 0
    assert report["cost"] == 0


def test_compose_runtime_is_networkless_read_only_and_does_not_mount_secrets():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "network_mode: none" in compose
    assert "read_only: true" in compose
    assert "DEEPSEEK_API_KEY" not in compose
    assert ".env" not in compose
    assert "day15_offline_check" in compose


def test_dockerfile_uses_exact_python_and_locked_direct_dependencies():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lock_lines = [
        line.strip()
        for line in (ROOT / "requirements-day15.lock.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert dockerfile.startswith("FROM python:3.11.9-slim-bookworm")
    assert "requirements-day15.lock.txt" in dockerfile
    assert all("==" in line and ">=" not in line and "<" not in line for line in lock_lines)


def test_docker_context_keeps_required_data_but_excludes_local_secrets():
    rules = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in rules
    assert ".venv" in rules
    assert "data/processed/*" in rules
    assert "!data/processed/olist.sqlite3" in rules
    assert "data/raw/*" not in rules


def test_compose_file_is_structurally_parseable_yaml():
    import yaml

    payload = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = payload["services"]["day15-offline-check"]
    assert service["network_mode"] == "none"
    assert service["read_only"] is True
