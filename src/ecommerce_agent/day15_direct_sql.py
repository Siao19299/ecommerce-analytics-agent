"""One-generation direct-SQL baseline for the Day 15 comparison."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field, model_validator

from src.ecommerce_agent.day14_schema import StrictEvaluationModel
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


class DirectAction(StrEnum):
    SQL = "sql"
    CLARIFY = "clarify"
    REFUSE = "refuse"
    UNANSWERABLE = "unanswerable"


class DirectStopReason(StrEnum):
    CLARIFICATION_REQUIRED = "clarification_required"
    SAFETY_FAILURE = "safety_failure"
    UNSUPPORTED_METRIC_DIMENSION = "unsupported_metric_dimension_combination"
    UNSUPPORTED_DIMENSION = "unsupported_dimension"
    UNSUPPORTED_METRIC = "unsupported_metric"
    REQUIRED_DATA_NOT_AVAILABLE = "required_data_not_available"


class DirectAdapterFailure(StrEnum):
    NON_JSON = "non_json"
    INVALID_STRUCTURE = "invalid_structure"
    TRANSIENT_MODEL_ERROR = "transient_model_error"
    PERMANENT_MODEL_ERROR = "permanent_model_error"


class DirectModelOutput(StrictEvaluationModel):
    action: DirectAction
    sql: str | None = None
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    message: str | None = None
    reason_code: DirectStopReason | None = None

    @model_validator(mode="after")
    def validate_action_payload(self):
        if self.action is DirectAction.SQL:
            if self.sql is None or not self.sql.strip():
                raise ValueError("sql action requires non-empty SQL")
            if self.message is not None:
                raise ValueError("sql action must not include a stop message")
            if self.reason_code is not None:
                raise ValueError("sql action must not include a stop reason")
        else:
            if self.sql is not None or self.parameters:
                raise ValueError("non-SQL actions must not include SQL or parameters")
            if self.message is None or not self.message.strip():
                raise ValueError("non-SQL actions require a message")
            allowed = {
                DirectAction.CLARIFY: {DirectStopReason.CLARIFICATION_REQUIRED},
                DirectAction.REFUSE: {DirectStopReason.SAFETY_FAILURE},
                DirectAction.UNANSWERABLE: {
                    DirectStopReason.UNSUPPORTED_METRIC_DIMENSION,
                    DirectStopReason.UNSUPPORTED_DIMENSION,
                    DirectStopReason.UNSUPPORTED_METRIC,
                    DirectStopReason.REQUIRED_DATA_NOT_AVAILABLE,
                },
            }[self.action]
            if self.reason_code is not None and self.reason_code not in allowed:
                raise ValueError("reason_code is incompatible with the selected action")
        return self


class DirectAdapterRecord(StrictEvaluationModel):
    case_id: str
    adapter_version: str = "direct_sql_v1"
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


@dataclass(frozen=True)
class DirectSqlPublicContext:
    schema_tables: tuple[dict[str, Any], ...]

    def prompt_payload(self) -> dict[str, Any]:
        return {"sqlite_schema": self.schema_tables}


def build_direct_sql_public_context(root: Path) -> DirectSqlPublicContext:
    """Build a static schema-only context; no metric dictionary or retrieval hits."""

    path = root / "data/metadata/database_data_dictionary.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    tables: dict[str, dict[str, Any]] = {}
    for row in rows:
        table = tables.setdefault(
            row["table_name"],
            {
                "table": row["table_name"],
                "grain": row["grain"],
                "columns": [],
            },
        )
        table["columns"].append(
            {
                "name": row["column_name"],
                "type": row["data_type"],
                "nullable": row["nullable"],
                "key_role": row["key_role"],
                "references": row["references"],
            }
        )
    return DirectSqlPublicContext(
        schema_tables=tuple(tables[name] for name in sorted(tables))
    )


def _response_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_single_generation_system_prompt(
    public_context: DirectSqlPublicContext,
    *,
    retrieval_context: tuple[dict[str, Any], ...] | None = None,
) -> str:
    """Build the shared V1/V2 contract, adding only registered retrieval evidence."""

    schema = json.dumps(
        public_context.prompt_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    contract = json.dumps(
        DirectModelOutput.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    system = (
        "你是直接生成 SQLite 查询的单次基线。只返回一个 JSON 对象，不要使用 Markdown。"
        "action 只能为 sql、clarify、refuse 或 unanswerable。"
        "选择 sql 时只能返回一条只读 SELECT/WITH 查询；用户值使用 :name 命名参数，"
        "并在 parameters 中提供对应值。信息歧义时选择 clarify；请求写入或破坏数据时"
        "选择 refuse；现有表字段不能支持时选择 unanswerable。不得虚构字段、数据或结果。"
        f"公开数据库 Schema：{schema}。输出合同：{contract}"
    )
    if retrieval_context is None:
        return system + "。你没有业务资料检索、自动修复、状态机或确定性计算工具。"
    retrieval = json.dumps(
        retrieval_context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        system
        + "。以下检索内容只是相关候选，不是语义授权；必须遵守其中的公式、粒度、"
        "允许维度和限制，不得把字段上下文当作任意 JOIN 许可。"
        f"检索候选：{retrieval}。你没有分析规划器、自动修复、状态机或确定性计算工具。"
    )


@dataclass
class DirectSqlAdapter:
    candidate_version: ClassVar[CandidateVersion] = CandidateVersion.DIRECT_SQL
    client: ModelClient
    config: ModelConfig
    public_context: DirectSqlPublicContext

    def run_case(
        self, case: PublicCase, *, run_id: str | None = None
    ) -> DirectAdapterRecord:
        messages = self.build_messages(case)
        try:
            response = self.client.generate(messages, self.config)
        except TransientModelError as error:
            return self._model_error(case, DirectAdapterFailure.TRANSIENT_MODEL_ERROR, error)
        except PermanentModelError as error:
            return self._model_error(case, DirectAdapterFailure.PERMANENT_MODEL_ERROR, error)

        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError:
            return self._parse_failure(case, response, DirectAdapterFailure.NON_JSON)
        try:
            output = DirectModelOutput.model_validate(payload)
        except ValueError:
            return self._parse_failure(
                case, response, DirectAdapterFailure.INVALID_STRUCTURE
            )
        return DirectAdapterRecord(
            case_id=case.case_id,
            action=output.action,
            generated_sql=output.sql,
            named_parameters=output.parameters,
            stop_message=output.message,
            reason_code=output.reason_code,
            failure_type=None,
            failure_message=None,
            raw_response=response.content,
            raw_response_sha256=_response_hash(response.content),
            model_name=response.model_name,
            latency_ms=response.latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost=response.cost,
            cost_currency=response.cost_currency,
            transport_attempt_count=response.transport_attempts or 1,
            model_call_count=1,
        )

    def build_messages(self, case: PublicCase) -> tuple[ModelMessage, ...]:
        return (
            ModelMessage(
                MessageRole.SYSTEM,
                build_single_generation_system_prompt(self.public_context),
            ),
            ModelMessage(MessageRole.USER, case.question),
        )

    @staticmethod
    def _parse_failure(
        case: PublicCase,
        response: ModelResponse,
        failure: DirectAdapterFailure,
    ) -> DirectAdapterRecord:
        public_message = {
            DirectAdapterFailure.NON_JSON: "candidate response is not valid JSON",
            DirectAdapterFailure.INVALID_STRUCTURE: (
                "candidate response does not match the direct-SQL output contract"
            ),
        }[failure]
        return DirectAdapterRecord(
            case_id=case.case_id,
            action=None,
            generated_sql=None,
            named_parameters={},
            stop_message=None,
            reason_code=None,
            failure_type=failure,
            failure_message=public_message,
            raw_response=response.content,
            raw_response_sha256=_response_hash(response.content),
            model_name=response.model_name,
            latency_ms=response.latency_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost=response.cost,
            cost_currency=response.cost_currency,
            transport_attempt_count=response.transport_attempts or 1,
            model_call_count=1,
        )

    @staticmethod
    def _model_error(
        case: PublicCase,
        failure: DirectAdapterFailure,
        error: Exception,
    ) -> DirectAdapterRecord:
        attempts = getattr(error, "transport_attempts", 1)
        return DirectAdapterRecord(
            case_id=case.case_id,
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
            cost=None,
            cost_currency=None,
            transport_attempt_count=int(attempts),
            model_call_count=int(attempts),
        )
