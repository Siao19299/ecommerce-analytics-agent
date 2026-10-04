"""Build ten SQL-plus-deterministic-Python Frozen benchmark multi-step cases."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.ecommerce_agent.analysis_adapter import (
    load_metric_provenance,
    monthly_observations_from_query_result,
)
from src.ecommerce_agent.anomaly_detection import (
    AnomalyRequest,
    detect_monthly_anomaly,
)
from src.ecommerce_agent.period_comparison import (
    ComparisonRequest,
    ComparisonType,
    calculate_period_comparison,
)
from src.ecommerce_agent.contribution_analysis import (
    ContributionComponent,
    ContributionRequest,
    ContributionScope,
    calculate_contribution,
)
from src.ecommerce_agent.analysis_models import (
    CalculationLineage,
    PeriodCompleteness,
)
from src.ecommerce_agent.analysis_presentation import (
    present_anomaly,
    present_comparison,
    present_contribution,
)
from src.ecommerce_agent.calculation_trace import build_calculation_trace
from src.ecommerce_agent.workflow_state import WorkflowStatus
from src.ecommerce_agent.evaluation_schema import (
    Authorship,
    BusinessReferenceStatus,
    CalculationStatus,
    ChangeActor,
    ChangeRecord,
    ComparisonRules,
    DatasetCategory,
    EvaluationCase,
    NullOrdering,
    NumericTolerance,
    OrderKey,
    PeriodCompletenessExpectation,
    Provenance,
    ReferenceSource,
    ReferenceSourceType,
    ResultReference,
    ResultReferenceKind,
    RowComparison,
    SafetyDecision,
    SafetyExpectation,
    SortDirection,
    SqlReference,
    SqlReferenceKind,
    SqliteVerificationStatus,
    TimeScope,
    TimeScopeMode,
    UserReviewStatus,
    load_dataset,
    validate_named_parameter_contract,
    write_schema_artifacts,
)
from src.ecommerce_agent.benchmark_metric_cases import (
    BUILD_DATE,
    DATABASE_RELATIVE_PATH,
    _raw_hashes,
    _sha256_file,
)
from src.ecommerce_agent.sql_generation import QueryExecutionResult, execute_read_only_query
from src.ecommerce_agent.sql_safety import build_global_sql_policy


FIXED_TRACE_TIME = datetime(2026, 9, 17, tzinfo=timezone.utc)
ZERO_TOLERANCE = NumericTolerance(absolute=0, relative=0)
VALUE_TOLERANCE = NumericTolerance(absolute=1e-6, relative=1e-9)


@dataclass(frozen=True)
class MultiStepSpec:
    case_id: str
    question: str
    metric_id: str
    difficulty: str
    analysis_type: str
    calculation_kind: str
    expected_status: CalculationStatus
    start_date: str
    end_date_exclusive: str
    target_period: str
    sql: str
    source_columns: tuple[str, ...]
    parameters: dict[str, str]
    dimensions: tuple[str, ...] = ()
    filters: dict[str, str] = field(default_factory=dict)
    incomplete_periods: dict[str, str] = field(default_factory=dict)
    history_window: int = 5
    minimum_history: int = 5
    threshold: float = 3.5
    notes: tuple[str, ...] = ()
    boundary_conditions: tuple[str, ...] = ()


MONTHLY_GMV_SQL = """SELECT date(o.order_purchase_timestamp, 'start of month') AS purchase_month,
       SUM(i.price) AS metric_value
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY date(o.order_purchase_timestamp, 'start of month')
ORDER BY purchase_month ASC"""


STATE_MONTHLY_GMV_SQL = """SELECT date(o.order_purchase_timestamp, 'start of month') AS purchase_month,
       SUM(i.price) AS metric_value
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND c.customer_state = :customer_state
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY date(o.order_purchase_timestamp, 'start of month')
ORDER BY purchase_month ASC"""


SPECS = (
    MultiStepSpec(
        "D14_MS_001",
        "计算 2018 年 7 月已送达月度 GMV 相对 2018 年 6 月的标准环比，并返回两期原始值、绝对变化和相对变化。",
        "delivered_monthly_gmv",
        "medium",
        "month_over_month",
        "comparison_mom",
        CalculationStatus.COMPUTED,
        "2018-06-01",
        "2018-08-01",
        "2018-07-01",
        MONTHLY_GMV_SQL,
        ("purchase_month", "metric_value"),
        {"start_date": "2018-06-01", "end_date_exclusive": "2018-08-01"},
        boundary_conditions=("comparison must use the previous calendar month",),
    ),
    MultiStepSpec(
        "D14_MS_002",
        "计算 2018 年 7 月已送达月度 GMV 相对 2017 年 7 月的同比，不得用前 12 行代替上年同月。",
        "delivered_monthly_gmv",
        "medium",
        "year_over_year",
        "comparison_yoy",
        CalculationStatus.COMPUTED,
        "2017-07-01",
        "2018-08-01",
        "2018-07-01",
        MONTHLY_GMV_SQL,
        ("purchase_month", "metric_value"),
        {"start_date": "2017-07-01", "end_date_exclusive": "2018-08-01"},
        boundary_conditions=("comparison must match the same calendar month one year earlier",),
    ),
    MultiStepSpec(
        "D14_MS_003",
        "计算 2018 年 7 月各商品品类对已送达、不含运费 GMV 的贡献度，保留 unknown，并验证未提前舍入的贡献度之和。",
        "delivered_category_gmv_contribution",
        "hard",
        "category_contribution",
        "contribution",
        CalculationStatus.COMPUTED,
        "2018-07-01",
        "2018-08-01",
        "2018-07-01",
        """SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
       SUM(i.price) AS metric_value
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
LEFT JOIN dim_products AS p ON p.product_id = i.product_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY COALESCE(p.product_category_name, 'unknown')
ORDER BY product_category ASC""",
        ("product_category", "metric_value"),
        {"start_date": "2018-07-01", "end_date_exclusive": "2018-08-01"},
        dimensions=("product_category",),
        boundary_conditions=("numerator and denominator use one identical scope",),
    ),
    MultiStepSpec(
        "D14_MS_004",
        "使用目标月之前连续 5 个完整自然月的 trailing median/MAD，检测 2017 年 11 月已送达月度 GMV 是否异常；异常不等于已知原因。",
        "delivered_monthly_gmv",
        "hard",
        "anomaly_detection",
        "anomaly",
        CalculationStatus.DETECTED,
        "2017-06-01",
        "2017-12-01",
        "2017-11-01",
        MONTHLY_GMV_SQL,
        ("purchase_month", "metric_value"),
        {"start_date": "2017-06-01", "end_date_exclusive": "2017-12-01"},
        boundary_conditions=("current period is excluded from the baseline",),
    ),
    MultiStepSpec(
        "D14_MS_005",
        "计算 AM 州 2017 年 2 月已送达 GMV 的环比；若精确的 2017 年 1 月没有观察值，必须返回缺失比较期而不是补零。",
        "delivered_monthly_gmv",
        "hard",
        "mom_missing_comparison",
        "comparison_mom",
        CalculationStatus.MISSING_COMPARISON_PERIOD,
        "2017-01-01",
        "2017-03-01",
        "2017-02-01",
        STATE_MONTHLY_GMV_SQL,
        ("purchase_month", "metric_value"),
        {
            "customer_state": "AM",
            "start_date": "2017-01-01",
            "end_date_exclusive": "2017-03-01",
        },
        dimensions=("customer_state",),
        filters={"customer_state": "AM"},
        boundary_conditions=("absence of a comparison row is not a numeric zero",),
    ),
    MultiStepSpec(
        "D14_MS_006",
        "RJ 州 2017 年 1 月取消订单数明确为 0、2 月为正数；计算 2 月环比时保留绝对变化，并返回零基期状态而不是无穷增长率。",
        "canceled_order_count",
        "hard",
        "mom_zero_baseline",
        "comparison_mom",
        CalculationStatus.ZERO_BASELINE,
        "2017-01-01",
        "2017-03-01",
        "2017-02-01",
        """SELECT date(o.order_purchase_timestamp, 'start of month') AS purchase_month,
       SUM(CASE WHEN o.order_status = 'canceled' THEN 1 ELSE 0 END) AS metric_value
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE c.customer_state = :customer_state
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY date(o.order_purchase_timestamp, 'start of month')
ORDER BY purchase_month ASC""",
        ("purchase_month", "metric_value"),
        {
            "customer_state": "RJ",
            "start_date": "2017-01-01",
            "end_date_exclusive": "2017-03-01",
        },
        dimensions=("customer_state",),
        filters={"customer_state": "RJ"},
        boundary_conditions=("zero is observed through conditional aggregation over existing orders",),
    ),
    MultiStepSpec(
        "D14_MS_007",
        "计算 2018 年 8 月已送达月度 GMV 相对 7 月的机械环比；8 月是末端不完整观察月，必须阻止标准环比解释。",
        "delivered_monthly_gmv",
        "hard",
        "mom_incomplete_current_period",
        "comparison_mom",
        CalculationStatus.COMPUTED,
        "2018-07-01",
        "2018-09-01",
        "2018-08-01",
        MONTHLY_GMV_SQL,
        ("purchase_month", "metric_value"),
        {"start_date": "2018-07-01", "end_date_exclusive": "2018-09-01"},
        incomplete_periods={"2018-08-01": "last_metric_observed_boundary_month"},
        boundary_conditions=(
            "arithmetic is retained but comparability must be not_comparable",
            "the partial month must not be called a standard MoM result",
        ),
    ),
    MultiStepSpec(
        "D14_MS_008",
        "使用 AP 州目标月前最近 5 个已送达 GMV 观察值检测 2017 年 8 月异常；历史自然月不连续时必须停止为 non_contiguous_history。",
        "delivered_monthly_gmv",
        "hard",
        "anomaly_non_contiguous_history",
        "anomaly",
        CalculationStatus.NON_CONTIGUOUS_HISTORY,
        "2017-02-01",
        "2017-09-01",
        "2017-08-01",
        STATE_MONTHLY_GMV_SQL,
        ("purchase_month", "metric_value"),
        {
            "customer_state": "AP",
            "start_date": "2017-02-01",
            "end_date_exclusive": "2017-09-01",
        },
        dimensions=("customer_state",),
        filters={"customer_state": "AP"},
        boundary_conditions=("recent rows cannot substitute for consecutive calendar months",),
    ),
    MultiStepSpec(
        "D14_MS_009",
        "检测 2016 年 11 月已送达月度 GMV 是否异常；目标月没有观察行时必须返回 missing_current_period，不得把缺失值当作 0 或表述为未发现异常。",
        "delivered_monthly_gmv",
        "medium",
        "anomaly_missing_current_period",
        "anomaly",
        CalculationStatus.MISSING_CURRENT_PERIOD,
        "2016-09-01",
        "2016-12-01",
        "2016-11-01",
        MONTHLY_GMV_SQL,
        ("purchase_month", "metric_value"),
        {"start_date": "2016-09-01", "end_date_exclusive": "2016-12-01"},
        boundary_conditions=(
            "a missing current observation is not a numeric zero",
            "insufficient evidence is not equivalent to not_detected",
        ),
    ),
    MultiStepSpec(
        "D14_MS_010",
        "DF 州 2017 年 11 月前五个完整月的取消订单数都明确为 0；用 median/MAD 检测异常时应返回 zero_dispersion，不得伪造 z 分数。",
        "canceled_order_count",
        "hard",
        "anomaly_zero_dispersion",
        "anomaly",
        CalculationStatus.ZERO_DISPERSION,
        "2017-06-01",
        "2017-12-01",
        "2017-11-01",
        """WITH months AS (
    SELECT date(:start_date) AS purchase_month
    UNION ALL SELECT date(:start_date, '+1 month')
    UNION ALL SELECT date(:start_date, '+2 month')
    UNION ALL SELECT date(:start_date, '+3 month')
    UNION ALL SELECT date(:start_date, '+4 month')
    UNION ALL SELECT date(:start_date, '+5 month')
),
state_canceled AS (
    SELECT date(o.order_purchase_timestamp, 'start of month') AS purchase_month,
           COUNT(DISTINCT o.order_id) AS canceled_count
    FROM fact_orders AS o
    JOIN dim_customers AS c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'canceled'
      AND c.customer_state = :customer_state
      AND o.order_purchase_timestamp >= :start_date
      AND o.order_purchase_timestamp < :end_date_exclusive
    GROUP BY date(o.order_purchase_timestamp, 'start of month')
)
SELECT m.purchase_month,
       COALESCE(s.canceled_count, 0) AS metric_value
FROM months AS m
LEFT JOIN state_canceled AS s ON s.purchase_month = m.purchase_month
ORDER BY m.purchase_month ASC""",
        ("purchase_month", "metric_value"),
        {
            "customer_state": "DF",
            "start_date": "2017-06-01",
            "end_date_exclusive": "2017-12-01",
        },
        dimensions=("customer_state",),
        filters={"customer_state": "DF"},
        boundary_conditions=(
            "calendar scaffold represents complete observed zero counts",
            "MAD zero requires an explicit separate rule",
        ),
    ),
)


def _lineage(case_id: str) -> CalculationLineage:
    return CalculationLineage(
        parent_run_id=f"{case_id}-reference-sql",
        source_sql_attempt=1,
        input_reference=f"{case_id}.reference_sql.rows",
    )


def _calculate(
    root: Path,
    spec: MultiStepSpec,
    execution: QueryExecutionResult,
) -> tuple[Any, Any, Any]:
    metric = load_metric_provenance(root, spec.metric_id, filters=spec.filters)
    lineage = _lineage(spec.case_id)
    target = date.fromisoformat(spec.target_period)
    if spec.calculation_kind == "contribution":
        components = tuple(
            ContributionComponent(
                group=str(row["product_category"]),
                value=row["metric_value"],
                is_unknown_group=row["product_category"] == "unknown",
            )
            for row in execution.rows
        )
        result = calculate_contribution(
            ContributionRequest(
                metric=metric,
                dimension="product_category",
                scope=ContributionScope(
                    metric_id=spec.metric_id,
                    period=target,
                    filters=spec.filters,
                    status_scope="order_status=delivered",
                    amount_basis="fact_order_items.price_excluding_freight",
                    completeness=PeriodCompleteness.COMPLETE,
                ),
                components=components,
                lineage=lineage,
            )
        )
        presentation = present_contribution(result)
        step = "deterministic_contribution"
    else:
        observations = monthly_observations_from_query_result(
            execution,
            period_column="purchase_month",
            value_column="metric_value",
            incomplete_period_reasons={
                date.fromisoformat(period): reason
                for period, reason in spec.incomplete_periods.items()
            },
        )
        if spec.calculation_kind in {"comparison_mom", "comparison_yoy"}:
            result = calculate_period_comparison(
                ComparisonRequest(
                    analysis_type=(
                        ComparisonType.MOM
                        if spec.calculation_kind == "comparison_mom"
                        else ComparisonType.YOY
                    ),
                    target_period=target,
                    metric=metric,
                    observations=observations,
                    lineage=lineage,
                )
            )
            presentation = present_comparison(result)
            step = "deterministic_comparison"
        else:
            result = detect_monthly_anomaly(
                AnomalyRequest(
                    metric=metric,
                    target_period=target,
                    observations=observations,
                    lineage=lineage,
                    history_window=spec.history_window,
                    minimum_history=spec.minimum_history,
                    threshold=spec.threshold,
                )
            )
            presentation = present_anomaly(result)
            step = "deterministic_anomaly_detection"
    trace = build_calculation_trace(
        calculation_id=f"{spec.case_id}-reference-calculation",
        step=step,
        result=result,
        created_at=FIXED_TRACE_TIME,
    )
    return result, presentation, trace


def _schema_status(value: str) -> CalculationStatus:
    if value == "insufficient_history":
        return CalculationStatus.INSUFFICIENT_EVIDENCE
    return CalculationStatus(value)


def _sources(
    root: Path,
    spec: MultiStepSpec,
    sql_relative_path: Path,
    sql_sha256: str,
) -> tuple[ReferenceSource, ...]:
    deterministic_module = {
        "contribution": "src/ecommerce_agent/contribution_analysis.py",
        "anomaly": "src/ecommerce_agent/anomaly_detection.py",
    }.get(spec.calculation_kind, "src/ecommerce_agent/period_comparison.py")
    return (
        ReferenceSource(
            source_type=ReferenceSourceType.METRIC_DICTIONARY,
            locator=f"data/metadata/metric_dictionary.csv#{spec.metric_id}",
            sha256=_sha256_file(root / "data/metadata/metric_dictionary.csv"),
        ),
        ReferenceSource(
            source_type=ReferenceSourceType.ASSISTANT_AUTHORED_SQLITE_VERIFIED_SQL,
            locator=sql_relative_path.as_posix(),
            sha256=sql_sha256,
        ),
        ReferenceSource(
            source_type=ReferenceSourceType.DETERMINISTIC_PYTHON,
            locator=deterministic_module,
            sha256=_sha256_file(root / deterministic_module),
        ),
    )


def _case(
    root: Path,
    spec: MultiStepSpec,
    sql_relative_path: Path,
    sql_sha256: str,
    result_relative_path: Path,
    result_sha256: str,
    presentation: Any,
) -> EvaluationCase:
    columns = tuple(presentation.table.columns)
    if spec.calculation_kind == "contribution":
        order_keys = (
            OrderKey(column="value", direction=SortDirection.DESCENDING),
            OrderKey(column="group", direction=SortDirection.ASCENDING),
        )
        tolerance_columns = {
            "value": VALUE_TOLERANCE,
            "contribution": VALUE_TOLERANCE,
        }
    elif spec.calculation_kind == "anomaly":
        order_keys = (
            OrderKey(column="period", direction=SortDirection.ASCENDING),
        )
        tolerance_columns = {
            "value": VALUE_TOLERANCE,
            "baseline_median": VALUE_TOLERANCE,
        }
    else:
        order_keys = (
            OrderKey(column="role", direction=SortDirection.ASCENDING),
        )
        tolerance_columns = {"value": VALUE_TOLERANCE}
    return EvaluationCase(
        case_id=spec.case_id,
        question=spec.question,
        category=DatasetCategory.MULTI_STEP,
        difficulty=spec.difficulty,
        analysis_type=spec.analysis_type,
        expected_workflow_status=WorkflowStatus.SUCCEEDED,
        allowed_stop_reasons=("completed", "first_attempt_succeeded", "repair_succeeded"),
        expected_calculation_status=spec.expected_status,
        metric_ids=(spec.metric_id,),
        dimensions=spec.dimensions,
        time_scope=TimeScope(
            mode=TimeScopeMode.BOUNDED,
            start_date=date.fromisoformat(spec.start_date),
            end_date_exclusive=date.fromisoformat(spec.end_date_exclusive),
            time_field="order_purchase_timestamp",
            completeness=(
                PeriodCompletenessExpectation.MIXED
                if spec.incomplete_periods
                else PeriodCompletenessExpectation.COMPLETE
            ),
            notes="SQL source range for deterministic Python analysis.",
        ),
        sql_reference=SqlReference(
            kind=SqlReferenceKind.STANDARD_SQL_FILE,
            path=sql_relative_path.as_posix(),
            sha256=sql_sha256,
            named_parameters=spec.parameters,
            reference_name=spec.case_id,
        ),
        result_reference=ResultReference(
            kind=ResultReferenceKind.EXACT_ROWS_FILE,
            path=result_relative_path.as_posix(),
            sha256=result_sha256,
            summary=(
                f"SQL source plus deterministic {spec.analysis_type} result with "
                f"calculation status {spec.expected_status.value}."
            ),
        ),
        comparison_rules=ComparisonRules(
            expected_columns=columns,
            row_comparison=RowComparison.ORDERED,
            order_keys=order_keys,
            numeric_tolerance=ZERO_TOLERANCE,
            numeric_tolerance_by_column=tolerance_columns,
        ),
        should_enter_sqlite=True,
        allows_repair=True,
        safety_expectation=SafetyExpectation(
            decision=SafetyDecision.ALLOW_READ_ONLY_EXECUTION,
            expected_execution_started=True,
            maximum_allowed_repair_attempts=2,
            reason="Source SQL executes read-only before deterministic Python analysis.",
        ),
        provenance=Provenance(
            case_authorship=Authorship.ASSISTANT_MECHANICAL,
            reference_authorship=Authorship.ASSISTANT_MECHANICAL,
            user_review_status=UserReviewStatus.NOT_REVIEWED,
            sqlite_verification_status=SqliteVerificationStatus.VERIFIED_REAL_SQLITE,
            business_reference_status=BusinessReferenceStatus.NOT_INDEPENDENTLY_EVALUATED,
            reference_sources=_sources(root, spec, sql_relative_path, sql_sha256),
        ),
        notes=spec.notes,
        boundary_conditions=spec.boundary_conditions,
        created_on=BUILD_DATE,
        updated_on=BUILD_DATE,
        change_history=(
            ChangeRecord(
                changed_on=BUILD_DATE,
                changed_by=ChangeActor.ASSISTANT,
                change_type="created",
                reason="Frozen benchmark module 5 assistant-authored SQL plus deterministic-Python case.",
            ),
        ),
    )


def build_multi_step_cases(root: Path) -> tuple[EvaluationCase, ...]:
    _, draft_path = write_schema_artifacts(root)
    dataset = load_dataset(draft_path)
    database = root / DATABASE_RELATIVE_PATH
    database_before = _sha256_file(database)
    raw_before = _raw_hashes(root)
    policy = build_global_sql_policy(root, max_rows=1000)
    sql_directory = root / "data/evaluation/benchmark_v1/references/sql"
    result_directory = root / "data/evaluation/benchmark_v1/references/results"
    sql_directory.mkdir(parents=True, exist_ok=True)
    result_directory.mkdir(parents=True, exist_ok=True)
    cases = []
    report_rows = []
    if len(SPECS) != 10 or len({spec.case_id for spec in SPECS}) != 10:
        raise ValueError("多步骤模块必须恰好包含 10 个唯一 case_id")

    for spec in SPECS:
        sql_text = (
            f"-- case_id: {spec.case_id}\n"
            f"-- metric_id: {spec.metric_id}\n"
            + spec.sql.strip()
            + ";\n"
        )
        validate_named_parameter_contract(sql_text, spec.parameters)
        sql_relative_path = Path(
            f"data/evaluation/benchmark_v1/references/sql/{spec.case_id}.sql"
        )
        sql_path = root / sql_relative_path
        sql_path.write_text(sql_text, encoding="utf-8")
        sql_sha256 = _sha256_file(sql_path)
        execution = execute_read_only_query(
            database,
            sql_text,
            spec.parameters,
            safety_policy=policy,
        )
        if not execution.is_success:
            raise RuntimeError(
                f"{spec.case_id} source SQL failed: "
                f"{execution.error_type} {execution.error_message}"
            )
        if not execution.execution_started or execution.rows_truncated:
            raise RuntimeError(f"{spec.case_id} source execution was incomplete")
        if execution.columns != spec.source_columns or not execution.rows:
            raise RuntimeError(f"{spec.case_id} source SQL returned an invalid shape")

        result, presentation, trace = _calculate(root, spec, execution)
        observed_status = _schema_status(result.calculation_status.value)
        if observed_status is not spec.expected_status:
            raise RuntimeError(
                f"{spec.case_id} expected {spec.expected_status.value}, "
                f"observed {observed_status.value}"
            )
        if spec.case_id == "D14_MS_007" and result.comparability.value != "not_comparable":
            raise RuntimeError("Incomplete current month must block standard comparability")

        result_payload = {
            "case_id": spec.case_id,
            "reference_type": "real_sqlite_plus_deterministic_python",
            "business_reference_status": "not_independently_evaluated",
            "database_sha256": database_before,
            "sql_sha256": sql_sha256,
            "parameters": spec.parameters,
            "source_sql": {
                "columns": list(execution.columns),
                "rows": list(execution.rows),
                "row_count": len(execution.rows),
                "safety_gate_accepted": execution.safety_trace.accepted,
                "execution_started": execution.execution_started,
                "rows_truncated": execution.rows_truncated,
            },
            "calculation": result.model_dump(mode="json"),
            "calculation_trace": trace.model_dump(mode="json"),
            "presentation": presentation.model_dump(mode="json"),
            "columns": list(presentation.table.columns),
            "rows": list(presentation.table.rows),
        }
        result_relative_path = Path(
            f"data/evaluation/benchmark_v1/references/results/{spec.case_id}.json"
        )
        result_path = root / result_relative_path
        result_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result_sha256 = _sha256_file(result_path)
        case = _case(
            root,
            spec,
            sql_relative_path,
            sql_sha256,
            result_relative_path,
            result_sha256,
            presentation,
        )
        cases.append(case)
        report_rows.append(
            {
                "case_id": spec.case_id,
                "metric_id": spec.metric_id,
                "analysis_type": spec.analysis_type,
                "expected_calculation_status": spec.expected_status.value,
                "observed_calculation_status": observed_status.value,
                "source_row_count": len(execution.rows),
                "safety_gate_accepted": execution.safety_trace.accepted,
                "execution_started": execution.execution_started,
                "deterministic_python_completed": True,
                "business_reference_status": "not_independently_evaluated",
            }
        )

    retained = tuple(
        case for case in dataset.cases if case.category is not DatasetCategory.MULTI_STEP
    )
    reason = "Added 10 real-SQLite plus deterministic-Python multi-step cases."
    history = tuple(
        item for item in dataset.provenance.change_history if item.reason != reason
    ) + (
        ChangeRecord(
            changed_on=BUILD_DATE,
            changed_by=ChangeActor.ASSISTANT,
            change_type="modified",
            reason=reason,
        ),
    )
    provenance = dataset.provenance.model_copy(
        update={
            "updated_on": BUILD_DATE,
            "authorship_disclosure": (
                "The first 50 cases were mechanically authored by the assistant. "
                "All executable references were verified on real SQLite; the 10 "
                "multi-step cases additionally use deterministic Deterministic analysis Python tools. "
                "No cases have user review or independent business references yet."
            ),
            "change_history": history,
        }
    )
    updated = dataset.model_copy(
        update={
            "dataset_version": "0.4.0",
            "provenance": provenance,
            "cases": retained + tuple(cases),
        }
    )
    updated = type(dataset).model_validate(updated.model_dump(mode="python"))
    draft_path.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")

    database_after = _sha256_file(database)
    raw_after = _raw_hashes(root)
    report = {
        "measurement_scope": (
            "assistant_authored_fixed_multi_step_cases; real_local_sqlite_source_rows; "
            "deterministic_day10_python_calculations; no_candidate_model_run; "
            "no_independent_business_accuracy"
        ),
        "external_api_calls": 0,
        "model_generated_numeric_results": 0,
        "case_count": len(cases),
        "category": DatasetCategory.MULTI_STEP.value,
        "analysis_type_counts": {
            kind: sum(case.analysis_type == kind for case in cases)
            for kind in sorted({case.analysis_type for case in cases})
        },
        "calculation_status_counts": {
            status: sum(
                case.expected_calculation_status.value == status for case in cases
            )
            for status in sorted(
                {case.expected_calculation_status.value for case in cases}
            )
        },
        "named_parameter_contract_pass_count": len(cases),
        "safety_gate_pass_count": sum(row["safety_gate_accepted"] for row in report_rows),
        "real_sqlite_execution_count": sum(row["execution_started"] for row in report_rows),
        "deterministic_python_calculation_count": sum(
            row["deterministic_python_completed"] for row in report_rows
        ),
        "user_reviewed_case_count": 0,
        "independent_business_reference_case_count": 0,
        "database_sha256_before": database_before,
        "database_sha256_after": database_after,
        "database_unchanged": database_before == database_after,
        "raw_file_hashes_before": raw_before,
        "raw_file_hashes_after": raw_after,
        "raw_files_unchanged": raw_before == raw_after,
        "cases": report_rows,
    }
    (root / "docs/reports/benchmark_analysis_cases.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return tuple(cases)


def main() -> None:
    root = Path(__file__).parents[2]
    cases = build_multi_step_cases(root)
    print(f"Frozen benchmark multi-step cases: {len(cases)}/10")
    print("All source SQL passed safety, executed real SQLite, and completed deterministic Python evaluation.")
    print("External API calls: 0; model-generated numeric results: 0")


if __name__ == "__main__":
    main()
