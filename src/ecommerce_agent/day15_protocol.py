"""Day 15 experiment definitions and fairness invariants.

This module defines the experiment before any candidate adapter or model run exists.
It contains no prompt text, gold reference, model client, or execution side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


FROZEN_DATASET_VERSION = "1.0.0"
FROZEN_DATASET_CONTENT_SHA256 = (
    "4ab5fa8c54ef830982bcb03828adb2133d20c68eda343d76ca059d5577d0c591"
)
FROZEN_DATASET_FILE_SHA256 = (
    "cd77ed6d98d37be7c87aa773f37820ee8c8ec21117b875f2c7b02a253d8e7093"
)
PUBLIC_MANIFEST_SHA256 = (
    "d44caf279d5fb15972845787e1664f066bd4333248dfda131efdc3c671a1dc6e"
)
DATABASE_SHA256 = "ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c"


class CandidateVersion(StrEnum):
    DIRECT_SQL = "direct_sql"
    RETRIEVAL_SQL = "retrieval_sql"
    FULL_AGENT = "full_agent"


class RunPurpose(StrEnum):
    SMOKE = "smoke"
    DEVELOPMENT = "development"
    MAIN_EXPERIMENT = "main_experiment"
    ABLATION = "ablation"
    DETERMINISTIC_REPLAY = "deterministic_replay"


class ResultProvenance(StrEnum):
    FAKE_MODEL = "fake_model"
    DETERMINISTIC_REPLAY = "deterministic_replay"
    REAL_MODEL = "real_model"


@dataclass(frozen=True)
class VersionDefinition:
    version: CandidateVersion
    receives_public_question: bool
    receives_public_schema_contract: bool
    retrieves_business_documents: bool
    uses_analysis_planner: bool
    uses_state_machine: bool
    uses_candidate_safety_gate: bool
    allows_bounded_repair: bool
    uses_deterministic_calculation: bool
    maximum_generation_stages: int


VERSION_DEFINITIONS = {
    CandidateVersion.DIRECT_SQL: VersionDefinition(
        version=CandidateVersion.DIRECT_SQL,
        receives_public_question=True,
        receives_public_schema_contract=True,
        retrieves_business_documents=False,
        uses_analysis_planner=False,
        uses_state_machine=False,
        uses_candidate_safety_gate=False,
        allows_bounded_repair=False,
        uses_deterministic_calculation=False,
        maximum_generation_stages=1,
    ),
    CandidateVersion.RETRIEVAL_SQL: VersionDefinition(
        version=CandidateVersion.RETRIEVAL_SQL,
        receives_public_question=True,
        receives_public_schema_contract=True,
        retrieves_business_documents=True,
        uses_analysis_planner=False,
        uses_state_machine=False,
        uses_candidate_safety_gate=False,
        allows_bounded_repair=False,
        uses_deterministic_calculation=False,
        maximum_generation_stages=1,
    ),
    CandidateVersion.FULL_AGENT: VersionDefinition(
        version=CandidateVersion.FULL_AGENT,
        receives_public_question=True,
        receives_public_schema_contract=True,
        retrieves_business_documents=True,
        uses_analysis_planner=True,
        uses_state_machine=True,
        uses_candidate_safety_gate=True,
        allows_bounded_repair=True,
        uses_deterministic_calculation=True,
        maximum_generation_stages=3,
    ),
}


SHARED_EVALUATION_CONTROLS = frozenset(
    {
        "same_public_manifest",
        "same_case_order",
        "same_frozen_gold_loaded_only_after_sealing",
        "same_read_only_sqlite_snapshot",
        "same_scoring_rules",
        "same_model_identity_for_equivalent_generation_stage",
        "same_sampling_parameters_for_equivalent_generation_stage",
        "same_per_call_timeout_for_equivalent_generation_stage",
        "same_output_token_limit_for_equivalent_generation_stage",
        "external_read_only_safety_sandbox",
        "separate_candidate_and_sandbox_safety_decisions",
    }
)


REQUIRED_LAYERED_MEASURES = frozenset(
    {
        "candidate_output_generated",
        "sql_generated",
        "candidate_safety_decision",
        "sandbox_safety_decision",
        "sqlite_entry_correct",
        "sql_execution_success",
        "result_correct",
        "workflow_status_correct",
        "stop_reason_correct",
        "calculation_status_correct",
        "safety_behavior_correct",
        "disallowed_repair_occurred",
        "sql_attempt_count",
        "repair_attempt_count",
        "generation_transport_attempt_count",
        "repair_transport_attempt_count",
        "business_reference_status",
        "business_correct",
        "latency_ms",
        "model_call_count",
        "input_tokens",
        "output_tokens",
        "cost",
        "failure_category",
    }
)


def validate_protocol() -> None:
    """Fail if the three definitions no longer express the intended ablation ladder."""

    if set(VERSION_DEFINITIONS) != set(CandidateVersion):
        raise ValueError("every candidate version must have exactly one definition")

    direct = VERSION_DEFINITIONS[CandidateVersion.DIRECT_SQL]
    retrieval = VERSION_DEFINITIONS[CandidateVersion.RETRIEVAL_SQL]
    agent = VERSION_DEFINITIONS[CandidateVersion.FULL_AGENT]

    if direct.retrieves_business_documents:
        raise ValueError("direct SQL must not receive retrieved business documents")
    if not retrieval.retrieves_business_documents:
        raise ValueError("retrieval + SQL must receive retrieved business documents")
    if any(
        (
            retrieval.uses_analysis_planner,
            retrieval.uses_state_machine,
            retrieval.uses_candidate_safety_gate,
            retrieval.allows_bounded_repair,
            retrieval.uses_deterministic_calculation,
        )
    ):
        raise ValueError("retrieval + SQL must remain a single-generation ablation")
    if not all(
        (
            agent.retrieves_business_documents,
            agent.uses_analysis_planner,
            agent.uses_state_machine,
            agent.uses_candidate_safety_gate,
            agent.allows_bounded_repair,
            agent.uses_deterministic_calculation,
        )
    ):
        raise ValueError("full Agent must preserve the production control-flow components")
    if direct.maximum_generation_stages != retrieval.maximum_generation_stages:
        raise ValueError("the two single-call baselines must have the same generation depth")
    if "separate_candidate_and_sandbox_safety_decisions" not in SHARED_EVALUATION_CONTROLS:
        raise ValueError("sandbox protection must not be credited as candidate safety behavior")


validate_protocol()
