"""Repository text scan that never opens local secret files or API key values."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


TEXT_SUFFIXES = {
    ".csv", ".dockerignore", ".html", ".json", ".jsonl", ".md",
    ".py", ".sql", ".toml", ".txt", ".yaml", ".yml",
}
EXCLUDED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
SECRET_FILENAMES = {".env", ".env.local", ".env.production"}
PATTERNS = {
    "openai_style_secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "assigned_deepseek_key": re.compile(
        r"DEEPSEEK_API_KEY\s*=\s*['\"]?(?!<|your-|example|\$\{|os\.|None\b)[^\s'\"]+",
        re.IGNORECASE,
    ),
}


def scan_repository_text(root: Path) -> dict[str, Any]:
    findings = []
    scanned = 0
    excluded_secret_files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.name in SECRET_FILENAMES:
            excluded_secret_files.append(relative.as_posix())
            continue
        if any(part in EXCLUDED_PARTS for part in relative.parts[:-1]):
            continue
        if len(relative.parts) >= 2 and relative.parts[:2] in {
            ("data", "raw"), ("data", "processed")
        }:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
            "Dockerfile", ".dockerignore"
        }:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                findings.append({
                    "file": relative.as_posix(),
                    "line": text.count("\n", 0, match.start()) + 1,
                    "rule": rule,
                })
    return {
        "passed": not findings,
        "scanned_file_count": scanned,
        "finding_count": len(findings),
        "findings": findings,
        "excluded_secret_files": excluded_secret_files,
        "api_key_values_read": 0,
    }
