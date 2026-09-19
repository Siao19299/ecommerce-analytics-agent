"""Rebuild the Day 15 real-model delivery artifacts from sealed per-case records."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ecommerce_agent.day14_evaluator import EvaluationRunReport
from src.ecommerce_agent.day15_failure_analysis import (
    load_empirical_failures,
    select_representative_empirical_failures,
)
from src.ecommerce_agent.day15_scoring import paired_metric_difference


VERSIONS = ("direct_sql", "retrieval_sql", "full_agent")
PAIRED_METRICS = (
    "case_contract_correct",
    "result_correct",
    "status_correct",
    "stop_reason_correct",
    "calculation_status_correct",
    "safety_correct",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_reports(run_directory: Path) -> dict[str, EvaluationRunReport]:
    return {
        version: EvaluationRunReport.model_validate_json(
            (run_directory / version / "scoring/scored_results.json").read_text(
                encoding="utf-8"
            )
        )
        for version in VERSIONS
    }


def build_live_delivery(root: Path) -> dict[str, Any]:
    run_directory = root / "data/processed/day15/live_main_rerun1"
    live_summary = _load_json(run_directory / "live_experiment_summary.json")
    provider_snapshot = _load_json(root / "docs/DAY15_PROVIDER_USAGE_SNAPSHOT.json")
    reports = _load_reports(run_directory)
    comparisons = []
    for left, right in (("direct_sql", "retrieval_sql"), ("retrieval_sql", "full_agent")):
        for metric in PAIRED_METRICS:
            comparisons.append(paired_metric_difference(
                reports[left], reports[right], metric,
                left_label=left, right_label=right,
            ).model_dump(mode="json"))

    failures = load_empirical_failures(root, run_directory)
    selected = select_representative_empirical_failures(failures, minimum_count=10)
    boundary_counts = Counter(
        (failure.candidate_version, failure.diagnosis.primary_boundary.value)
        for failure in failures
    )
    payload = {
        "report_schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_run_label": live_summary["run_label"],
        "result_provenance": "real_model",
        "model": live_summary["model"],
        "sampling": {
            "temperature": live_summary["temperature"],
            "thinking": live_summary["thinking"],
            "random_seed": live_summary["random_seed"],
            "random_seed_note": live_summary["random_seed_note"],
        },
        "versions": live_summary["versions"],
        "paired_comparisons": comparisons,
        "uncertainty_note": (
            "Paired percentile bootstrap intervals use 10,000 case resamples and seed 15015. "
            "They describe sensitivity to this fixed 60-case sample, not model sampling "
            "variance or deployment-population generalization."
        ),
        "business_accuracy_note": (
            "All three versions have denominator 0 because no frozen case has an "
            "independent business-answer reference. Business accuracy is not independently evaluated."
        ),
        "ablation_interpretation": {
            "retrieval_context": (
                "The direct_sql versus retrieval_sql comparison is the real-model retrieval-context "
                "ablation, with extra context length and prompt layout recorded confounders."
            ),
            "bounded_repair": (
                "No repair model call occurred in the full-agent main run. A new real-model "
                "repair-off run would be observationally identical for repair contribution and was not run."
            ),
            "full_agent_bundle": (
                "Retrieval_sql versus full_agent changes planning, safety gating, state handling, "
                "execution, and deterministic calculation together; their individual causal effects "
                "cannot be separated by this comparison."
            ),
        },
        "budget_ledger": live_summary["budget"],
        "provider_console_intermediate_snapshot": provider_snapshot,
        "prior_invalid_run_note": live_summary["prior_invalid_run_note"],
        "empirical_failure_count": len(failures),
        "empirical_primary_boundary_counts": {
            f"{version}:{boundary}": count
            for (version, boundary), count in sorted(boundary_counts.items())
        },
        "representative_empirical_failures": [
            failure.model_dump(mode="json") for failure in selected
        ],
    }
    (root / "docs/DAY15_LIVE_RESULTS.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_failure_markdown(root, payload)
    return payload


def _write_failure_markdown(root: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Day 15 真实模型失败分析",
        "",
        "本报告只分析候选批次封存后的真实模型结果。主分类取最早失守边界；后续连锁错误保留在各层结果与 scorer 分类中，但不重复计作独立根因。这里的案例不得用于回头调优本次正式提示词。",
        "",
        f"真实失败记录共 {payload['empirical_failure_count']} 条（三版本逐题契约失败的合计，允许同一道题在不同版本各出现一次）。代表案例覆盖三种版本与不同主边界。",
        "",
    ]
    for index, failure in enumerate(payload["representative_empirical_failures"], 1):
        diagnosis = failure["diagnosis"]
        layers = failure["layer_results"]
        sql = failure["generated_sql"]
        sql_preview = "无 SQL" if sql is None else " ".join(sql.split())[:300]
        wrong_layers = ", ".join(
            name for name, value in layers.items() if value is False
        ) or "无（由组合契约或其他分类判失败）"
        lines.extend([
            f"## {index}. {failure['candidate_version']} / {failure['case_id']}",
            "",
            f"- 类别：`{failure['category']}`",
            f"- 问题：{failure['question']}",
            f"- 状态 / 停止原因：`{failure['workflow_status']}` / `{failure['stop_reason']}`",
            f"- 主失败边界：`{diagnosis['primary_boundary']}`",
            f"- 受影响层：{', '.join(diagnosis['affected_boundaries'])}",
            f"- 失败评分层：{wrong_layers}",
            f"- scorer 分类：{', '.join(diagnosis['scorer_failure_categories'])}",
            f"- SQL 摘要：`{sql_preview}`",
            "",
        ])
    lines.extend([
        "## 解释边界",
        "",
        "- 代表案例是事后诊断样本，不是二次调参集。",
        "- 相同题目跨版本的差异可能来自检索、提示布局、规划、状态机、安全门或确定性计算；只有 direct_sql 与 retrieval_sql 的对照接近检索上下文消融，仍混有 token 长度与提示布局差异。",
        "- 完整 Agent 主运行没有触发 repair 调用，因此不能估计修复机制的实际增益。",
        "- 业务答案没有独立金标准，不能从 SQL 结果正确推导业务结论正确。",
    ])
    (root / "docs/DAY15_REAL_FAILURE_ANALYSIS.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    build_live_delivery(Path(__file__).resolve().parents[2])
