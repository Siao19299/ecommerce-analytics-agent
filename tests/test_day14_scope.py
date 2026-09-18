from src.ecommerce_agent.day14_scope import (
    DAY14_DATASET_QUALITY_METRICS,
    DAY14_FORBIDDEN_EXPERIMENTS,
    DAY15_SYSTEM_PERFORMANCE_METRICS,
    EvaluationLayer,
    validate_phase_boundary,
)


def test_all_required_evaluation_layers_are_explicit_and_distinct():
    assert {layer.value for layer in EvaluationLayer} == {
        "sql_generation",
        "sql_safety",
        "sql_execution",
        "workflow_status",
        "calculation_status",
        "result",
        "business_semantics",
    }


def test_day14_dataset_quality_and_day15_system_metrics_do_not_overlap():
    validate_phase_boundary()
    assert DAY14_DATASET_QUALITY_METRICS.isdisjoint(
        DAY15_SYSTEM_PERFORMANCE_METRICS
    )
    assert "reference_sql_execution_verification_rate" in (
        DAY14_DATASET_QUALITY_METRICS
    )
    assert "result_accuracy" in DAY15_SYSTEM_PERFORMANCE_METRICS


def test_day14_does_not_authorize_day15_experiments():
    assert {
        "three_version_full_comparison",
        "real_model_batch_evaluation",
        "docker_packaging",
    } <= DAY14_FORBIDDEN_EXPERIMENTS
