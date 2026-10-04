from src.ecommerce_agent.experiment_protocol import (
    CandidateVersion,
    DATABASE_SHA256,
    FROZEN_DATASET_CONTENT_SHA256,
    FROZEN_DATASET_FILE_SHA256,
    PUBLIC_MANIFEST_SHA256,
    REQUIRED_LAYERED_MEASURES,
    SHARED_EVALUATION_CONTROLS,
    VERSION_DEFINITIONS,
    validate_protocol,
)


def test_frozen_inputs_are_pinned_to_acceptance():
    assert FROZEN_DATASET_CONTENT_SHA256 == (
        "4ab5fa8c54ef830982bcb03828adb2133d20c68eda343d76ca059d5577d0c591"
    )
    assert FROZEN_DATASET_FILE_SHA256 == (
        "cd77ed6d98d37be7c87aa773f37820ee8c8ec21117b875f2c7b02a253d8e7093"
    )
    assert PUBLIC_MANIFEST_SHA256 == (
        "d44caf279d5fb15972845787e1664f066bd4333248dfda131efdc3c671a1dc6e"
    )
    assert DATABASE_SHA256 == (
        "ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c"
    )


def test_three_versions_form_the_intended_component_ladder():
    validate_protocol()
    direct = VERSION_DEFINITIONS[CandidateVersion.DIRECT_SQL]
    retrieval = VERSION_DEFINITIONS[CandidateVersion.RETRIEVAL_SQL]
    agent = VERSION_DEFINITIONS[CandidateVersion.FULL_AGENT]

    assert not direct.retrieves_business_documents
    assert retrieval.retrieves_business_documents
    assert direct.maximum_generation_stages == retrieval.maximum_generation_stages == 1
    assert not retrieval.uses_state_machine
    assert not retrieval.uses_candidate_safety_gate
    assert agent.uses_state_machine
    assert agent.uses_candidate_safety_gate
    assert agent.allows_bounded_repair
    assert agent.uses_deterministic_calculation


def test_sandbox_protection_is_not_credited_to_candidate():
    assert "external_read_only_safety_sandbox" in SHARED_EVALUATION_CONTROLS
    assert "separate_candidate_and_sandbox_safety_decisions" in SHARED_EVALUATION_CONTROLS


def test_protocol_keeps_scoring_and_cost_layers_separate():
    required = {
        "sql_execution_success",
        "result_correct",
        "workflow_status_correct",
        "calculation_status_correct",
        "safety_behavior_correct",
        "business_correct",
        "sql_attempt_count",
        "repair_attempt_count",
        "generation_transport_attempt_count",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cost",
    }
    assert required <= REQUIRED_LAYERED_MEASURES
