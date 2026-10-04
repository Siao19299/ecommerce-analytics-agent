"""Freeze and fully verify the Day 14 fixed evaluation dataset offline."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from src.ecommerce_agent.day14_dataset_validation import validate_dataset
from src.ecommerce_agent.day14_evaluator import export_blind_manifest, run_self_test
from src.ecommerce_agent.day14_reference_verification import verify_references
from src.ecommerce_agent.day14_schema import (
    ChangeActor,
    ChangeRecord,
    DatasetState,
    EvaluationDataset,
    compute_dataset_content_sha256,
    load_dataset,
    verify_dataset_content_sha256,
    write_schema_artifacts,
)
from src.ecommerce_agent.day14_single_metric import DATABASE_RELATIVE_PATH, _raw_hashes, _sha256_file


FREEZE_DATE = date(2026, 9, 18)
EXPECTED_DATABASE_SHA256 = "ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c"


def freeze_dataset(root: Path) -> Path:
    write_schema_artifacts(root)
    draft = load_dataset(root / "data/evaluation/day14/dataset.v1.draft.json")
    reason = "Frozen Day 14 v1.0.0 after offline reference, coverage, and evaluator validation."
    history = tuple(
        item for item in draft.provenance.change_history if item.reason != reason
    ) + (
        ChangeRecord(
            changed_on=FREEZE_DATE,
            changed_by=ChangeActor.ASSISTANT,
            change_type="frozen",
            reason=reason,
        ),
    )
    payload = draft.model_dump(mode="json")
    payload.update(
        {
            "dataset_version": "1.0.0",
            "state": DatasetState.FROZEN.value,
            "content_sha256": None,
        }
    )
    payload["provenance"]["updated_on"] = FREEZE_DATE.isoformat()
    payload["provenance"]["authorship_disclosure"] = (
        "All 60 frozen cases were mechanically authored by the assistant. "
        "Fifty executable business references were verified on real SQLite; "
        "multi-step outputs additionally use deterministic Python; ten risk cases "
        "use fixed stop/safety contracts. No case is user-authored or independently "
        "business-verified, and no candidate model produced or modified the gold assets."
    )
    payload["provenance"]["change_history"] = [
        item.model_dump(mode="json") for item in history
    ]
    payload["content_sha256"] = compute_dataset_content_sha256(payload)
    frozen = EvaluationDataset.model_validate(payload)
    if not verify_dataset_content_sha256(frozen):
        raise RuntimeError("frozen dataset content hash verification failed")
    output = root / "data/evaluation/day14/dataset.v1.json"
    output.write_text(frozen.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output


def _database_shape(path: Path) -> tuple[list[str], int]:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        fields = sum(
            len(connection.execute(f'PRAGMA table_info("{table}")').fetchall())
            for table in tables
        )
        return tables, fields
    finally:
        connection.close()


def scan_sensitive_artifacts(root: Path) -> dict[str, object]:
    targets = [
        root / "README.md",
        root / "PLAN.md",
        root / "LEARNING_LOG.md",
        root / "docs/archive/PLAN.md",
        root / "docs/archive/LEARNING_LOG.md",
        root / "docs/archive/DEVELOPMENT_NOTES.md",
        *sorted((root / "src/ecommerce_agent").glob("day14_*.py")),
        *sorted((root / "data/evaluation/day14").rglob("*")),
        *sorted((root / "docs").glob("DAY14_*")),
    ]
    excluded_names = {"DAY14_ACCEPTANCE.md", "DAY14_RESULTS.json"}
    patterns = {
        "openai_style_secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "assigned_deepseek_key": re.compile(
            r"DEEPSEEK_API_KEY\s*=\s*['\"]?(?!<|your-|example|\s*$)[^\s'\"]+",
            re.IGNORECASE | re.MULTILINE,
        ),
        "windows_user_absolute_path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\"),
    }
    findings: list[dict[str, object]] = []
    scanned = 0
    seen: set[Path] = set()
    for path in targets:
        if path in seen or not path.is_file() or path.name in excluded_names:
            continue
        seen.add(path)
        if path.suffix.lower() not in {".py", ".md", ".json", ".jsonl", ".csv", ".sql", ".txt"}:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in patterns.items():
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "file": path.relative_to(root).as_posix(),
                        "line": text.count("\n", 0, match.start()) + 1,
                        "rule": name,
                    }
                )
    public_lines = (
        root / "data/evaluation/day14/cases.public.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    public_keys_valid = all(
        set(json.loads(line)) == {"case_id", "question"}
        for line in public_lines
        if line.strip()
    )
    return {
        "scanned_file_count": scanned,
        "finding_count": len(findings),
        "findings": findings,
        "public_manifest_line_count": len(public_lines),
        "public_manifest_only_case_id_and_question": public_keys_valid,
        "passed": not findings and public_keys_valid and len(public_lines) == 60,
        "api_key_values_read": 0,
    }


def _run_check(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _write_acceptance_markdown(root: Path, report: dict[str, object]) -> None:
    coverage = report["coverage_summary"]
    checks = report["final_checks"]
    lines = [
        "# Day 14 验收：固定 Agent 评测集",
        "",
        "## 1. 范围与结论",
        "",
        "Day 14 建立并冻结了 60 道固定评测题、标准 SQL/结果或停止合同、覆盖与重复度校验、语义结果比较器、盲测清单以及逐题执行框架。完整三版本实验、真实模型批量评测、失败统计和 Docker 保留到 Day 15。",
        "",
        f"验收结论：`{'通过' if report['accepted'] else '未通过'}`。数据集版本 `{report['dataset_version']}`，内容哈希 `{report['dataset_content_sha256']}`。",
        "",
        "## 2. 固定集与覆盖",
        "",
        f"- 总题数：{report['case_count']}；分类：20 单指标 / 20 聚合筛选多表 / 10 多步骤 / 10 风险歧义不可回答。",
        f"- 难度：easy {coverage['difficulty']['easy']} / medium {coverage['difficulty']['medium']} / hard {coverage['difficulty']['hard']}。",
        f"- 直接覆盖指标字典 ID：{coverage['covered_dictionary_metric_count']}/{coverage['dictionary_metric_count']}；六个受支持维度全部覆盖。",
        f"- 归一化模板完全重复：0；高相似问题对：{coverage['high_similarity_pair_count']}。",
        "- 当前 60 题均为助手机械编写；用户独立编写、用户审核和独立业务参考均为 0。",
        "",
        "## 3. 金标准与隔离",
        "",
        "- 候选系统只接收 `cases.public.jsonl` 中的 case_id 与 question。",
        "- 候选输出先完成校验并计算 SHA-256，评测器随后才加载内部参考 SQL、结果和评分规则。",
        "- 52 份标准 SQL 全部通过哈希、命名参数与现有安全门；50 道业务 SQL 重新读取真实 SQLite 并与保存结果一致。",
        "- SQL 文本不做完全相等判断；优先比较执行结果、列、行、多重集、稳定顺序、NULL、ISO 日期和数值容差。",
        "- 状态题比较工作流状态、停止原因、安全行为、执行边界和修复边界。",
        "",
        "## 4. 逐题执行框架",
        "",
        "逐题结果分别记录 SQL 生成、执行、状态、停止原因、calculation status、源结果、最终结果、安全、lineage、attempt 计数和业务参考状态。SQL attempt、修复轮次与模型传输 attempt 保持分离。",
        "",
        "内部 oracle replay 的 60/60 仅用于验证评测器，不是待评模型准确率。真实待评模型运行数为 0，外部 API 调用为 0，模型生成数值为 0。",
        "",
        "## 5. 离线验收",
        "",
        f"- 全量测试：`{checks['pytest_summary']}`。",
        f"- pip check：`{checks['pip_check']}`。",
        f"- compileall：`{checks['compileall']}`。",
        f"- SQLite：6 张核心表、38 字段；SHA-256 `{report['database_sha256']}`。",
        "- data/raw/ 的 archive.zip 和九个 CSV 与 Day 13 基线哈希一致。",
        f"- 敏感信息扫描：{report['sensitive_scan']['scanned_file_count']} 个文件，发现 {report['sensitive_scan']['finding_count']} 项。没有读取 API Key 值。",
        "",
        "## 6. 边界与诚实披露",
        "",
        "- HTTP 成功、状态机成功、SQL 执行、确定性计算和业务正确性仍是不同结论。",
        "- 真实客户使用 customer_unique_id；明细与支付分别预聚合；支付金额不按商品品类拆分；销售额保持澄清；不完整月份不包装为标准同比或环比。",
        "- Day 8 安全、资源和环境失败不进入修复；Day 9 三套 attempt 计数不合并；Day 10 数值只来自 SQL/Python；Day 11 lineage 与停止条件没有绕过。",
        "- 没有运行 Day 15 的直接 SQL、检索+SQL、完整 Agent 三版本实验，因此没有模型执行率、结果正确率、延迟、成本或消融结论。",
        "- Day 14 实际学习时间为用户明确提供的 2 小时；不包含助手编码、测试运行或对话等待时间。",
        "",
        "## 7. 复现命令",
        "",
        "```powershell",
        ".\\.venv\\Scripts\\python.exe -m src.ecommerce_agent.day14_dataset_validation",
        ".\\.venv\\Scripts\\python.exe -m src.ecommerce_agent.day14_reference_verification",
        ".\\.venv\\Scripts\\python.exe -m src.ecommerce_agent.day14_evaluator",
        ".\\.venv\\Scripts\\python.exe -m src.ecommerce_agent.day14_acceptance",
        "```",
        "",
    ]
    (root / "docs/DAY14_ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")


def run_acceptance(root: Path) -> dict[str, object]:
    frozen_path = freeze_dataset(root)
    export_blind_manifest(root)
    validation = validate_dataset(root)
    references = verify_references(root)
    evaluator = run_self_test(root)
    sensitive = scan_sensitive_artifacts(root)

    database = root / DATABASE_RELATIVE_PATH
    database_before = _sha256_file(database)
    raw_before = _raw_hashes(root)
    day13 = json.loads((root / "docs/DAY13_RESULTS.json").read_text(encoding="utf-8"))
    tables, field_count = _database_shape(database)

    pytest_result = _run_check([sys.executable, "-m", "pytest", "-q"], root)
    pip_result = _run_check([sys.executable, "-m", "pip", "check"], root)
    compile_result = _run_check(
        [sys.executable, "-m", "compileall", "-q", "src", "tests", "streamlit_app.py"],
        root,
    )
    pytest_text = pytest_result.stdout + pytest_result.stderr
    match = re.search(r"(\d+) passed(?:, (\d+) warning)?", pytest_text)
    pytest_summary = match.group(0) if match else "unparsed"

    database_after = _sha256_file(database)
    raw_after = _raw_hashes(root)
    dataset = load_dataset(frozen_path)
    category_counts = dict(sorted(Counter(case.category.value for case in dataset.cases).items()))
    accepted = all(
        (
            validation["passed"],
            references["safety_gate_pass_count"] == 52,
            references["reference_result_comparison_pass_count"] == 50,
            evaluator.case_contract_pass_count == 60,
            verify_dataset_content_sha256(dataset),
            sensitive["passed"],
            database_before == database_after == EXPECTED_DATABASE_SHA256,
            raw_before == raw_after == day13["raw_file_hashes_after"],
            len(tables) == 6,
            field_count == 38,
            pytest_result.returncode == 0,
            pip_result.returncode == 0,
            compile_result.returncode == 0,
        )
    )
    report: dict[str, object] = {
        "measurement_scope": (
            "day14_fixed_dataset_offline_acceptance; real_sqlite_reference_replay; "
            "scripted_evaluator_self_test; no_candidate_model_accuracy"
        ),
        "accepted": accepted,
        "dataset_version": dataset.dataset_version,
        "dataset_state": dataset.state.value,
        "dataset_content_sha256": dataset.content_sha256,
        "dataset_file_sha256": _sha256_file(frozen_path),
        "case_count": len(dataset.cases),
        "category_counts": category_counts,
        "coverage_summary": {
            "difficulty": validation["coverage"]["difficulty"],
            "dictionary_metric_count": validation["coverage"]["dictionary_metric_count"],
            "covered_dictionary_metric_count": validation["coverage"]["covered_dictionary_metric_count"],
            "dictionary_dimension_count": validation["coverage"]["dictionary_dimension_count"],
            "covered_dictionary_dimension_count": validation["coverage"]["covered_dictionary_dimension_count"],
            "high_similarity_pair_count": validation["template_repetition"]["high_similarity_pair_count"],
        },
        "reference_verification": {
            "standard_sql_count": references["standard_sql_count"],
            "safety_gate_pass_count": references["safety_gate_pass_count"],
            "real_sqlite_reference_execution_count": references["real_sqlite_reference_execution_count"],
            "reference_result_comparison_pass_count": references["reference_result_comparison_pass_count"],
        },
        "evaluator_self_test": {
            "submission_source": evaluator.submission_source.value,
            "per_case_result_count": evaluator.per_case_result_count,
            "case_contract_pass_count": evaluator.case_contract_pass_count,
            "business_evaluated_count": evaluator.business_evaluated_count,
            "candidate_received_reference_assets": evaluator.candidate_received_reference_assets,
        },
        "authorship": {
            "assistant_mechanical_case_count": 60,
            "user_authored_case_count": 0,
            "user_reviewed_case_count": 0,
            "independent_business_reference_count": 0,
        },
        "external_api_calls": 0,
        "real_candidate_model_runs": 0,
        "model_generated_numeric_results": 0,
        "learning_time": {
            "status": "user_provided",
            "hours": 2,
            "assistant_estimate_used": False,
        },
        "database_tables": tables,
        "database_field_count": field_count,
        "database_sha256": database_after,
        "database_unchanged": database_before == database_after,
        "raw_file_hashes": raw_after,
        "raw_files_unchanged_from_day13": raw_after == day13["raw_file_hashes_after"],
        "sensitive_scan": sensitive,
        "final_checks": {
            "pytest_returncode": pytest_result.returncode,
            "pytest_summary": pytest_summary,
            "pip_check_returncode": pip_result.returncode,
            "pip_check": pip_result.stdout.strip(),
            "compileall_returncode": compile_result.returncode,
            "compileall": "passed" if compile_result.returncode == 0 else "failed",
        },
        "day15_metrics_not_computed": [
            "direct_sql_baseline_accuracy",
            "retrieval_plus_sql_accuracy",
            "full_agent_accuracy",
            "ablation_comparison",
            "candidate_latency",
            "candidate_cost",
            "candidate_failure_analysis",
        ],
    }
    (root / "docs/DAY14_RESULTS.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_acceptance_markdown(root, report)
    if not accepted:
        raise RuntimeError("Day 14 offline acceptance failed")
    return report


def main() -> None:
    root = Path(__file__).parents[2]
    report = run_acceptance(root)
    print(
        f"Day 14 acceptance: accepted={report['accepted']}; "
        f"cases={report['case_count']}; tests={report['final_checks']['pytest_summary']}"
    )
    print("External API calls and real candidate-model runs: 0")


if __name__ == "__main__":
    main()
