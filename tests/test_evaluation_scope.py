from src.ecommerce_agent.evaluation_scope import (
    DATASET_QUALITY_METRICS,
    DATASET_PHASE_FORBIDDEN_EXPERIMENTS,
    SYSTEM_PERFORMANCE_METRICS,
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


def test_dataset_quality_and_system_metrics_do_not_overlap():
    validate_phase_boundary()
    assert DATASET_QUALITY_METRICS.isdisjoint(
        SYSTEM_PERFORMANCE_METRICS
    )
    assert "reference_sql_execution_verification_rate" in (
        DATASET_QUALITY_METRICS
    )
    assert "result_accuracy" in SYSTEM_PERFORMANCE_METRICS


def test_does_not_authorize_experiments():
    assert {
        "three_version_full_comparison",
        "real_model_batch_evaluation",
        "docker_packaging",
    } <= DATASET_PHASE_FORBIDDEN_EXPERIMENTS
