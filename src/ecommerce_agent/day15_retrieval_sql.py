"""Retrieval-plus-SQL single-generation baseline for Day 15."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import ClassVar

from pydantic import Field

from src.ecommerce_agent.day14_schema import StrictEvaluationModel
from src.ecommerce_agent.day15_direct_sql import (
    DirectAction,
    DirectAdapterFailure,
    DirectModelOutput,
    DirectSqlPublicContext,
    DirectStopReason,
    build_single_generation_system_prompt,
)
from src.ecommerce_agent.day15_reproducibility import PublicCase
from src.ecommerce_agent.day15_protocol import CandidateVersion
from src.ecommerce_agent.model_client import (
    MessageRole,
    ModelClient,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    PermanentModelError,
    TransientModelError,
)
from src.ecommerce_agent.retrieval import RetrievalFilter, RetrievalHit, Retriever


class RetrievalEvidence(StrictEvaluationModel):
    document_id: str
    document_type: str
    rank_within_type: int = Field(ge=1)
    score: float = Field(ge=0)
    retriever_version: str


class RetrievalSqlAdapterRecord(StrictEvaluationModel):
    case_id: str
    adapter_version: str = "retrieval_sql_v1"
    retriever_version: str
    metric_top_k: int = Field(ge=1)
    schema_top_k: int = Field(ge=1)
    retrieval_evidence: tuple[RetrievalEvidence, ...]
    action: DirectAction | None
    generated_sql: str | None
    named_parameters: dict[str, str | int | float | bool | None]
    stop_message: str | None
    reason_code: DirectStopReason | None
    failure_type: DirectAdapterFailure | None
    failure_message: str | None
    raw_response: str | None
    raw_response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_name: str | None
    latency_ms: float | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    cost_currency: str | None = None
    transport_attempt_count: int
    model_call_count: int


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _hit_prompt_payload(hit: RetrievalHit) -> dict[str, object]:
    document = hit.document
    return {
        "document_id": document.document_id,
        "document_type": document.document_type,
        "rank": hit.rank,
        "score": hit.score,
        "identifier": document.identifier,
        "chinese_name": document.chinese_name,
        "definition": document.description,
        "formula": document.formula,
        "grain": document.grain,
        "tables": document.tables,
        "fields": document.fields,
        "default_time_field": document.default_time_field,
        "available_dimensions": document.available_dimensions,
        "constraints": document.constraints,
    }


@dataclass
class RetrievalSqlAdapter:
    candidate_version: ClassVar[CandidateVersion] = CandidateVersion.RETRIEVAL_SQL
    client: ModelClient
    config: ModelConfig
    public_context: DirectSqlPublicContext
    retriever: Retriever
    retriever_version: str
    metric_top_k: int = 5
    schema_top_k: int = 8

    def __post_init__(self) -> None:
        for name, value in (
            ("metric_top_k", self.metric_top_k),
            ("schema_top_k", self.schema_top_k),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not self.retriever_version.strip():
            raise ValueError("retriever_version must not be blank")

    def run_case(
        self, case: PublicCase, *, run_id: str | None = None
    ) -> RetrievalSqlAdapterRecord:
        metric_hits, schema_hits = self.retrieve(case)
        evidence = self._evidence(metric_hits + schema_hits)
        messages = self.build_messages(case, metric_hits, schema_hits)
        try:
            response = self.client.generate(messages, self.config)
        except TransientModelError as error:
            return self._model_error(
                case, evidence, DirectAdapterFailure.TRANSIENT_MODEL_ERROR, error
            )
        except PermanentModelError as error:
            return self._model_error(
                case, evidence, DirectAdapterFailure.PERMANENT_MODEL_ERROR, error
            )

        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError:
            return self._parse_failure(
                case, evidence, response, DirectAdapterFailure.NON_JSON
            )
        try:
            output = DirectModelOutput.model_validate(payload)
        except ValueError:
            return self._parse_failure(
                case, evidence, response, DirectAdapterFailure.INVALID_STRUCTURE
            )
        return self._record(
            case,
            evidence,
            response=response,
            action=output.action,
            sql=output.sql,
            parameters=output.parameters,
            message=output.message,
            reason_code=output.reason_code,
        )

    def retrieve(
        self, case: PublicCase
    ) -> tuple[tuple[RetrievalHit, ...], tuple[RetrievalHit, ...]]:
        metric_hits = self.retriever.search(
            case.question,
            top_k=self.metric_top_k,
            filters=RetrievalFilter(document_types=frozenset({"metric"})),
        )
        schema_hits = self.retriever.search(
            case.question,
            top_k=self.schema_top_k,
            filters=RetrievalFilter(document_types=frozenset({"schema"})),
        )
        return metric_hits, schema_hits

    def build_messages(
        self,
        case: PublicCase,
        metric_hits: tuple[RetrievalHit, ...],
        schema_hits: tuple[RetrievalHit, ...],
    ) -> tuple[ModelMessage, ...]:
        retrieval_context = tuple(
            _hit_prompt_payload(hit) for hit in metric_hits + schema_hits
        )
        return (
            ModelMessage(
                MessageRole.SYSTEM,
                build_single_generation_system_prompt(
                    self.public_context, retrieval_context=retrieval_context
                ),
            ),
            ModelMessage(MessageRole.USER, case.question),
        )

    def _evidence(
        self, hits: tuple[RetrievalHit, ...]
    ) -> tuple[RetrievalEvidence, ...]:
        return tuple(
            RetrievalEvidence(
                document_id=hit.document.document_id,
                document_type=hit.document.document_type,
                rank_within_type=hit.rank,
                score=hit.score,
                retriever_version=self.retriever_version,
            )
            for hit in hits
        )

    def _record(
        self,
        case: PublicCase,
        evidence: tuple[RetrievalEvidence, ...],
        *,
        response: ModelResponse,
        action: DirectAction | None = None,
        sql: str | None = None,
        parameters: dict[str, str | int | float | bool | None] | None = None,
        message: str | None = None,
        reason_code: DirectStopReason | None = None,
        failure: DirectAdapterFailure | None = None,
        failure_message: str | None = None,
    ) -> RetrievalSqlAdapterRecord:
        return RetrievalSqlAdapterRecord(
            case_id=case.case_id,
            retriever_version=self.retriever_version,
            metric_top_k=self.metric_top_k,
            schema_top_k=self.schema_top_k,
            retrieval_evidence=evidence,
            action=action,
            generated_sql=sql,
            named_parameters=parameters or {},
            stop_message=message,
            reason_code=reason_code,
            failure_type=failure,
            failure_message=failure_message,
            raw_response=response.content,
            raw_response_sha256=_hash(response.content),
            model_name=response.model_name,
            latency_ms=response.latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost=response.cost,
            cost_currency=response.cost_currency,
            transport_attempt_count=response.transport_attempts or 1,
            model_call_count=1,
        )

    def _parse_failure(
        self,
        case: PublicCase,
        evidence: tuple[RetrievalEvidence, ...],
        response: ModelResponse,
        failure: DirectAdapterFailure,
    ) -> RetrievalSqlAdapterRecord:
        message = (
            "candidate response is not valid JSON"
            if failure is DirectAdapterFailure.NON_JSON
            else "candidate response does not match the retrieval-SQL output contract"
        )
        return self._record(
            case, evidence, response=response, failure=failure, failure_message=message
        )

    def _model_error(
        self,
        case: PublicCase,
        evidence: tuple[RetrievalEvidence, ...],
        failure: DirectAdapterFailure,
        error: Exception,
    ) -> RetrievalSqlAdapterRecord:
        attempts = int(getattr(error, "transport_attempts", 1))
        return RetrievalSqlAdapterRecord(
            case_id=case.case_id,
            retriever_version=self.retriever_version,
            metric_top_k=self.metric_top_k,
            schema_top_k=self.schema_top_k,
            retrieval_evidence=evidence,
            action=None,
            generated_sql=None,
            named_parameters={},
            stop_message=None,
            reason_code=None,
            failure_type=failure,
            failure_message=(
                "candidate model transport failed"
                if failure is DirectAdapterFailure.TRANSIENT_MODEL_ERROR
                else "candidate model configuration or credentials failed"
            ),
            raw_response=None,
            raw_response_sha256=None,
            model_name=None,
            latency_ms=None,
            prompt_tokens=None,
            completion_tokens=None,
            transport_attempt_count=attempts,
            model_call_count=attempts,
        )
