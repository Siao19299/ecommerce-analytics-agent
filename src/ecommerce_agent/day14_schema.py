"""Strict, versioned contracts for the fixed Day 14 evaluation dataset."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.ecommerce_agent.day11_state import WorkflowStatus


SHA256_PATTERN = r"^[0-9a-f]{64}$"
SEMVER_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+$"
CASE_ID_PATTERN = r"^D14_(SM|AJ|MS|RU)_[0-9]{3}$"


class StrictEvaluationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class DatasetCategory(StrEnum):
    SINGLE_METRIC = "single_metric"
    AGGREGATE_FILTER_JOIN = "aggregate_filter_join"
    MULTI_STEP = "multi_step"
    RISK_AMBIGUOUS_UNANSWERABLE = "risk_ambiguous_unanswerable"


CATEGORY_CASE_PREFIX = {
    DatasetCategory.SINGLE_METRIC: "SM",
    DatasetCategory.AGGREGATE_FILTER_JOIN: "AJ",
    DatasetCategory.MULTI_STEP: "MS",
    DatasetCategory.RISK_AMBIGUOUS_UNANSWERABLE: "RU",
}

REQUIRED_FROZEN_CATEGORY_COUNTS = {
    DatasetCategory.SINGLE_METRIC: 20,
    DatasetCategory.AGGREGATE_FILTER_JOIN: 20,
    DatasetCategory.MULTI_STEP: 10,
    DatasetCategory.RISK_AMBIGUOUS_UNANSWERABLE: 10,
}


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class DatasetState(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"


class DatasetSplit(StrEnum):
    DEVELOPMENT = "development"
    FINAL_EVALUATION = "final_evaluation"


class TimeScopeMode(StrEnum):
    ALL_DATA = "all_data"
    BOUNDED = "bounded"
    NOT_APPLICABLE = "not_applicable"


class PeriodCompletenessExpectation(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    MIXED = "mixed"
    NOT_APPLICABLE = "not_applicable"


class TimeScope(StrictEvaluationModel):
    mode: TimeScopeMode
    start_date: date | None = None
    end_date_exclusive: date | None = None
    time_field: str | None = None
    completeness: PeriodCompletenessExpectation
    notes: str = ""

    @model_validator(mode="after")
    def validate_scope(self):
        bounded_values = (self.start_date, self.end_date_exclusive)
        if self.mode is TimeScopeMode.BOUNDED:
            if any(value is None for value in bounded_values):
                raise ValueError("bounded time scope 必须提供半开区间起止日期")
            if self.start_date >= self.end_date_exclusive:
                raise ValueError("start_date 必须早于 end_date_exclusive")
            if not self.time_field:
                raise ValueError("bounded time scope 必须声明 time_field")
        elif any(value is not None for value in bounded_values):
            raise ValueError("非 bounded time scope 不得携带起止日期")
        if (
            self.mode is TimeScopeMode.NOT_APPLICABLE
            and self.completeness
            is not PeriodCompletenessExpectation.NOT_APPLICABLE
        ):
            raise ValueError("不适用时间范围时 completeness 也必须不适用")
        return self


class CalculationStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    COMPUTED = "computed"
    MISSING_CURRENT_PERIOD = "missing_current_period"
    MISSING_COMPARISON_PERIOD = "missing_comparison_period"
    ZERO_BASELINE = "zero_baseline"
    NEGATIVE_BASELINE = "negative_baseline"
    INCOMPLETE_PERIOD = "incomplete_period"
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NON_CONTIGUOUS_HISTORY = "non_contiguous_history"
    ZERO_DISPERSION = "zero_dispersion"


class SqlReferenceKind(StrEnum):
    STANDARD_SQL_FILE = "standard_sql_file"
    NO_SQL_EXPECTED = "no_sql_expected"


JsonScalar = str | int | float | bool | None


class SqlReference(StrictEvaluationModel):
    kind: SqlReferenceKind
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    named_parameters: dict[str, JsonScalar] = Field(default_factory=dict)
    reference_name: str | None = None

    @model_validator(mode="after")
    def validate_reference(self):
        if self.kind is SqlReferenceKind.STANDARD_SQL_FILE:
            if not self.path or not self.sha256 or not self.reference_name:
                raise ValueError("标准 SQL 引用必须包含路径、哈希和稳定名称")
        elif any((self.path, self.sha256, self.reference_name)):
            raise ValueError("不期望 SQL 时不得携带标准 SQL 资产")
        if self.kind is SqlReferenceKind.NO_SQL_EXPECTED and self.named_parameters:
            raise ValueError("不期望 SQL 时不得携带命名参数")
        return self


class ResultReferenceKind(StrEnum):
    EXACT_ROWS_FILE = "exact_rows_file"
    RESULT_SUMMARY = "result_summary"
    STATUS_ONLY = "status_only"


class ResultReference(StrictEvaluationModel):
    kind: ResultReferenceKind
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_result_asset(self):
        if self.kind is ResultReferenceKind.EXACT_ROWS_FILE:
            if not self.path or not self.sha256:
                raise ValueError("精确结果引用必须包含路径和 SHA-256")
        elif self.path is not None or self.sha256 is not None:
            raise ValueError("摘要或状态题不得伪装成精确结果文件")
        return self


class RowComparison(StrEnum):
    SINGLE_ROW = "single_row"
    ORDERED = "ordered"
    UNORDERED_MULTISET = "unordered_multiset"
    STATUS_ONLY = "status_only"


class NullPolicy(StrEnum):
    STRICT_JSON_NULL = "strict_json_null"


class SortDirection(StrEnum):
    ASCENDING = "asc"
    DESCENDING = "desc"


class NullOrdering(StrEnum):
    NATIVE = "native"
    FIRST = "first"
    LAST = "last"


class OrderKey(StrictEvaluationModel):
    column: str = Field(min_length=1)
    direction: SortDirection
    nulls: NullOrdering = NullOrdering.NATIVE


class NumericTolerance(StrictEvaluationModel):
    absolute: float = Field(ge=0)
    relative: float = Field(ge=0)

    @field_validator("absolute", "relative", mode="before")
    @classmethod
    def reject_boolean(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("数值容差不能使用布尔值")
        return value


class ComparisonRules(StrictEvaluationModel):
    expected_columns: tuple[str, ...]
    allow_extra_columns: bool = False
    row_comparison: RowComparison
    order_keys: tuple[OrderKey, ...] = ()
    numeric_tolerance: NumericTolerance
    numeric_tolerance_by_column: dict[str, NumericTolerance] = Field(
        default_factory=dict
    )
    null_policy: NullPolicy = NullPolicy.STRICT_JSON_NULL
    date_format: Literal["iso_8601"] = "iso_8601"
    preserve_duplicate_rows: bool = True

    @model_validator(mode="after")
    def validate_order_contract(self):
        if len(self.expected_columns) != len(set(self.expected_columns)):
            raise ValueError("expected_columns 不得重复")
        order_columns = [item.column for item in self.order_keys]
        if len(order_columns) != len(set(order_columns)):
            raise ValueError("order_keys 不得重复")
        unknown = set(order_columns) - set(self.expected_columns)
        if unknown:
            raise ValueError(f"排序键不在预期列中：{sorted(unknown)}")
        unknown_tolerance_columns = set(
            self.numeric_tolerance_by_column
        ) - set(self.expected_columns)
        if unknown_tolerance_columns:
            raise ValueError(
                "逐列容差字段不在预期列中："
                f"{sorted(unknown_tolerance_columns)}"
            )
        if self.row_comparison is RowComparison.ORDERED and not self.order_keys:
            raise ValueError("有序结果必须声明稳定 order_keys")
        if (
            self.row_comparison is not RowComparison.ORDERED
            and self.order_keys
        ):
            raise ValueError("非有序结果不得声明 order_keys")
        return self


class SafetyDecision(StrEnum):
    ALLOW_READ_ONLY_EXECUTION = "allow_read_only_execution"
    REJECT_BEFORE_SQLITE = "reject_before_sqlite"
    NOT_APPLICABLE = "not_applicable"


class SafetyExpectation(StrictEvaluationModel):
    decision: SafetyDecision
    expected_execution_started: bool
    maximum_allowed_repair_attempts: int = Field(ge=0)
    database_must_remain_unchanged: bool = True
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision(self):
        if (
            self.decision is SafetyDecision.REJECT_BEFORE_SQLITE
            and self.expected_execution_started
        ):
            raise ValueError("安全拒绝不得进入 SQLite")
        if (
            self.decision is SafetyDecision.REJECT_BEFORE_SQLITE
            and self.maximum_allowed_repair_attempts != 0
        ):
            raise ValueError("安全拒绝不得进入修复")
        return self


class Authorship(StrEnum):
    ASSISTANT_MECHANICAL = "assistant_mechanical"
    USER_INDEPENDENT = "user_independent"
    USER_AND_ASSISTANT_COLLABORATIVE = "user_and_assistant_collaborative"
    EXISTING_CANONICAL_ASSET = "existing_canonical_asset"
    DETERMINISTIC_POLICY = "deterministic_policy"


class UserReviewStatus(StrEnum):
    NOT_REVIEWED = "not_reviewed"
    REVIEWED = "reviewed"
    USER_AUTHORED = "user_authored"


class SqliteVerificationStatus(StrEnum):
    PENDING = "pending"
    VERIFIED_REAL_SQLITE = "verified_real_sqlite"
    NOT_APPLICABLE = "not_applicable"


class BusinessReferenceStatus(StrEnum):
    INDEPENDENTLY_VERIFIED = "independently_verified"
    NOT_INDEPENDENTLY_EVALUATED = "not_independently_evaluated"
    NOT_APPLICABLE_FIXED_CONTRACT = "not_applicable_fixed_contract"


class ReferenceSourceType(StrEnum):
    METRIC_DICTIONARY = "metric_dictionary"
    DIMENSION_DICTIONARY = "dimension_dictionary"
    DATABASE_DICTIONARY = "database_dictionary"
    OLIST_SCHEMA = "olist_schema"
    DAY04_STANDARD_SQL = "day04_standard_sql"
    DAY04_ADVANCED_SQL = "day04_advanced_sql"
    ASSISTANT_AUTHORED_SQLITE_VERIFIED_SQL = (
        "assistant_authored_sqlite_verified_sql"
    )
    DETERMINISTIC_PYTHON = "deterministic_python"
    SAFETY_POLICY = "safety_policy"
    CLARIFICATION_CONTRACT = "clarification_contract"
    USER_INDEPENDENT_REFERENCE = "user_independent_reference"


class ReferenceSource(StrictEvaluationModel):
    source_type: ReferenceSourceType
    locator: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    notes: str = ""


class Provenance(StrictEvaluationModel):
    case_authorship: Authorship
    reference_authorship: Authorship
    user_review_status: UserReviewStatus
    sqlite_verification_status: SqliteVerificationStatus
    business_reference_status: BusinessReferenceStatus
    reference_sources: tuple[ReferenceSource, ...] = Field(min_length=1)
    derived_from: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_claims(self):
        if (
            self.case_authorship is Authorship.USER_INDEPENDENT
            and self.user_review_status is not UserReviewStatus.USER_AUTHORED
        ):
            raise ValueError("用户独立编写必须记录为 user_authored")
        if (
            self.business_reference_status
            is BusinessReferenceStatus.INDEPENDENTLY_VERIFIED
            and self.user_review_status is UserReviewStatus.NOT_REVIEWED
        ):
            raise ValueError("独立业务参考不能同时标记为未经用户审核")
        return self


class ChangeActor(StrEnum):
    ASSISTANT = "assistant"
    USER = "user"
    USER_AND_ASSISTANT = "user_and_assistant"


class ChangeRecord(StrictEvaluationModel):
    changed_on: date
    changed_by: ChangeActor
    change_type: Literal["created", "modified", "reviewed", "frozen"]
    reason: str = Field(min_length=1)


class EvaluationCase(StrictEvaluationModel):
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    question: str = Field(min_length=1, max_length=2000)
    category: DatasetCategory
    difficulty: Difficulty
    analysis_type: str = Field(min_length=1)
    expected_workflow_status: WorkflowStatus
    allowed_stop_reasons: tuple[str, ...] = Field(min_length=1)
    expected_calculation_status: CalculationStatus
    metric_ids: tuple[str, ...]
    dimensions: tuple[str, ...]
    time_scope: TimeScope
    sql_reference: SqlReference
    result_reference: ResultReference
    comparison_rules: ComparisonRules
    should_enter_sqlite: bool
    allows_repair: bool
    safety_expectation: SafetyExpectation
    provenance: Provenance
    notes: tuple[str, ...] = ()
    boundary_conditions: tuple[str, ...] = ()
    created_on: date
    updated_on: date
    change_history: tuple[ChangeRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_contract(self):
        expected_prefix = CATEGORY_CASE_PREFIX[self.category]
        actual_prefix = self.case_id.split("_")[1]
        if actual_prefix != expected_prefix:
            raise ValueError("case_id 前缀必须与 category 一致")
        if self.expected_workflow_status is WorkflowStatus.RUNNING:
            raise ValueError("评测预期状态必须是终态")
        if len(self.metric_ids) != len(set(self.metric_ids)):
            raise ValueError("metric_ids 不得重复")
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("dimensions 不得重复")
        if len(self.allowed_stop_reasons) != len(set(self.allowed_stop_reasons)):
            raise ValueError("allowed_stop_reasons 不得重复")
        if any(not reason.strip() for reason in self.allowed_stop_reasons):
            raise ValueError("停止原因不得为空")
        if self.updated_on < self.created_on:
            raise ValueError("updated_on 不得早于 created_on")
        if not any(item.change_type == "created" for item in self.change_history):
            raise ValueError("修改历史必须包含 created 记录")
        if self.should_enter_sqlite:
            if self.sql_reference.kind is not SqlReferenceKind.STANDARD_SQL_FILE:
                raise ValueError("需要进入 SQLite 的题必须具有标准 SQL 引用")
            if (
                self.safety_expectation.decision
                is not SafetyDecision.ALLOW_READ_ONLY_EXECUTION
            ):
                raise ValueError("需要进入 SQLite 的题必须期望只读执行")
        if (
            self.expected_workflow_status is WorkflowStatus.NEEDS_CLARIFICATION
            or self.expected_workflow_status is WorkflowStatus.SAFETY_REJECTED
        ):
            if self.sql_reference.kind is not SqlReferenceKind.NO_SQL_EXPECTED:
                raise ValueError("澄清或安全拒绝题不得具有标准 SQL")
            if self.should_enter_sqlite or self.allows_repair:
                raise ValueError("澄清或安全拒绝题不得执行或修复")
        if self.expected_workflow_status is WorkflowStatus.SAFETY_REJECTED:
            if (
                self.safety_expectation.decision
                is not SafetyDecision.REJECT_BEFORE_SQLITE
            ):
                raise ValueError("安全拒绝题必须声明在 SQLite 前拒绝")
        if (
            not self.allows_repair
            and self.safety_expectation.maximum_allowed_repair_attempts
        ):
            raise ValueError("不允许修复时修复次数上限必须为 0")
        return self


class DatasetProvenance(StrictEvaluationModel):
    created_on: date
    updated_on: date
    authorship_disclosure: str = Field(min_length=1)
    reference_isolation_policy: str = Field(min_length=1)
    source_repository_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    database_sha256: str = Field(pattern=SHA256_PATTERN)
    change_history: tuple[ChangeRecord, ...] = Field(min_length=1)


class EvaluationDataset(StrictEvaluationModel):
    dataset_id: Literal["ecommerce_agent_day14_fixed_evaluation"]
    schema_version: str = Field(pattern=SEMVER_PATTERN)
    dataset_version: str = Field(pattern=SEMVER_PATTERN)
    split: DatasetSplit
    state: DatasetState
    expected_case_count: Literal[60] = 60
    content_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    provenance: DatasetProvenance
    cases: tuple[EvaluationCase, ...]

    @model_validator(mode="after")
    def validate_dataset(self):
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id 必须全局唯一")
        if self.state is DatasetState.FROZEN:
            if len(self.cases) != self.expected_case_count:
                raise ValueError("冻结评测集必须恰好包含 60 题")
            counts = {
                category: sum(case.category is category for case in self.cases)
                for category in DatasetCategory
            }
            if counts != REQUIRED_FROZEN_CATEGORY_COUNTS:
                raise ValueError("冻结评测集分类必须严格为 20/20/10/10")
            if self.content_sha256 is None:
                raise ValueError("冻结评测集必须记录内容哈希")
        elif self.content_sha256 is not None:
            raise ValueError("草稿评测集不得伪装成已冻结内容")
        return self


def build_empty_draft() -> EvaluationDataset:
    """Create a transparent module-2 draft without pretending cases exist."""

    return EvaluationDataset(
        dataset_id="ecommerce_agent_day14_fixed_evaluation",
        schema_version="1.1.0",
        dataset_version="0.1.0",
        split=DatasetSplit.FINAL_EVALUATION,
        state=DatasetState.DRAFT,
        provenance=DatasetProvenance(
            created_on=date(2026, 9, 17),
            updated_on=date(2026, 9, 17),
            authorship_disclosure=(
                "Schema and empty draft mechanically authored by the assistant; "
                "no Day 14 evaluation cases have been authored or reviewed yet."
            ),
            reference_isolation_policy=(
                "Candidate systems receive questions only; frozen reference SQL, "
                "results, and scoring rules are loaded by the evaluator after the "
                "candidate run and are never generated or modified by it."
            ),
            source_repository_commit="63b03cd",
            database_sha256=(
                "ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c"
            ),
            change_history=(
                ChangeRecord(
                    changed_on=date(2026, 9, 17),
                    changed_by=ChangeActor.ASSISTANT,
                    change_type="created",
                    reason="Module 2 strict schema and transparent empty draft.",
                ),
            ),
        ),
        cases=(),
    )


def write_schema_artifacts(root: Path) -> tuple[Path, Path]:
    """Export JSON Schema and initialize, but never overwrite, the draft."""

    output = root / "data/evaluation/day14"
    output.mkdir(parents=True, exist_ok=True)
    schema_path = output / "dataset.schema.json"
    draft_path = output / "dataset.v1.draft.json"
    schema_path.write_text(
        json.dumps(EvaluationDataset.model_json_schema(), indent=2)
        + "\n",
        encoding="utf-8",
    )
    if not draft_path.exists():
        draft_path.write_text(
            build_empty_draft().model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    return schema_path, draft_path


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


def canonical_dataset_path(root: Path) -> Path:
    """Prefer the immutable release asset once Day 14 has been frozen."""
    frozen = root / "data/evaluation/day14/dataset.v1.json"
    if frozen.is_file():
        return frozen
    return root / "data/evaluation/day14/dataset.v1.draft.json"


def compute_dataset_content_sha256(payload: EvaluationDataset | dict[str, Any]) -> str:
    """Hash canonical dataset content with the self-referential field cleared."""
    if isinstance(payload, EvaluationDataset):
        content = payload.model_dump(mode="json")
    else:
        content = json.loads(json.dumps(payload, ensure_ascii=False))
    content["content_sha256"] = None
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_dataset_content_sha256(dataset: EvaluationDataset) -> bool:
    return (
        dataset.content_sha256 is not None
        and dataset.content_sha256 == compute_dataset_content_sha256(dataset)
    )


def named_parameter_tokens(sql: str) -> frozenset[str]:
    """Extract SQLite named parameter tokens for reference-contract validation."""

    return frozenset(re.findall(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", sql))


def validate_named_parameter_contract(
    sql: str,
    parameters: dict[str, Any],
) -> None:
    tokens = named_parameter_tokens(sql)
    keys = frozenset(parameters)
    if tokens != keys:
        raise ValueError(
            "标准 SQL 命名参数不匹配："
            f"SQL={sorted(tokens)}, parameters={sorted(keys)}"
        )


def main() -> None:
    root = Path(__file__).parents[2]
    schema_path, draft_path = write_schema_artifacts(root)
    load_dataset(draft_path)
    print(f"JSON Schema: {schema_path.relative_to(root)}")
    print(f"Dataset draft: {draft_path.relative_to(root)}")
    print("Day 14 cases in draft: 0 (expected until sample modules begin)")


if __name__ == "__main__":
    main()
