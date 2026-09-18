"""Frozen Day 14 evaluation layers and Day 14/Day 15 ownership boundary."""

from __future__ import annotations

from enum import StrEnum


class EvaluationLayer(StrEnum):
    """Independent conclusions that a per-case evaluator may report."""

    SQL_GENERATION = "sql_generation"
    SQL_SAFETY = "sql_safety"
    SQL_EXECUTION = "sql_execution"
    WORKFLOW_STATUS = "workflow_status"
    CALCULATION_STATUS = "calculation_status"
    RESULT = "result"
    BUSINESS_SEMANTICS = "business_semantics"


DAY14_DATASET_QUALITY_METRICS = frozenset(
    {
        "case_schema_validity",
        "category_count_validity",
        "reference_provenance_completeness",
        "reference_sql_safety_pass_rate",
        "reference_sql_execution_verification_rate",
        "coverage_matrix_completeness",
        "template_similarity_review_count",
    }
)


DAY15_SYSTEM_PERFORMANCE_METRICS = frozenset(
    {
        "sql_generation_success_rate",
        "sql_execution_success_rate",
        "result_accuracy",
        "workflow_status_accuracy",
        "safety_behavior_accuracy",
        "business_accuracy",
        "repair_success_rate",
        "latency",
        "token_usage",
        "api_cost",
    }
)


DAY14_FORBIDDEN_EXPERIMENTS = frozenset(
    {
        "three_version_full_comparison",
        "real_model_batch_evaluation",
        "ablation_statistics",
        "model_failure_rate_claims",
        "docker_packaging",
    }
)


def validate_phase_boundary() -> None:
    """Reject an accidental overlap between dataset and system metrics."""

    overlap = DAY14_DATASET_QUALITY_METRICS & DAY15_SYSTEM_PERFORMANCE_METRICS
    if overlap:
        raise ValueError(f"Day 14/15 metric ownership overlaps: {sorted(overlap)}")
