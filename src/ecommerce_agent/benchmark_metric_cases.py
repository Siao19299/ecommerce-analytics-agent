"""Build and verify the 20 fixed Frozen benchmark single-metric evaluation cases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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
    NumericTolerance,
    PeriodCompletenessExpectation,
    Provenance,
    ReferenceSource,
    ReferenceSourceType,
    ResultReference,
    ResultReferenceKind,
    RowComparison,
    SafetyDecision,
    SafetyExpectation,
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
from src.ecommerce_agent.sql_generation import execute_read_only_query
from src.ecommerce_agent.sql_safety import build_global_sql_policy


BUILD_DATE = date(2026, 9, 17)
DATABASE_RELATIVE_PATH = Path("data/processed/olist.sqlite3")


@dataclass(frozen=True)
class SingleMetricSpec:
    case_id: str
    question: str
    metric_id: str
    difficulty: str
    analysis_type: str
    start_date: str
    end_date_exclusive: str
    completeness: PeriodCompletenessExpectation
    sql: str
    result_column: str
    tolerance: NumericTolerance
    notes: tuple[str, ...] = ()
    boundary_conditions: tuple[str, ...] = ()
    derived_from: tuple[str, ...] = ()

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "start_date": self.start_date,
            "end_date_exclusive": self.end_date_exclusive,
        }


def _window_where(alias: str = "o") -> str:
    return (
        f"{alias}.order_purchase_timestamp >= :start_date\n"
        f"  AND {alias}.order_purchase_timestamp < :end_date_exclusive"
    )


SPECS = (
    SingleMetricSpec(
        "D14_SM_001",
        "2018 年 6 月按下单时间统计，状态为 delivered 的唯一订单数是多少？",
        "delivered_order_count",
        "easy",
        "scalar_count",
        "2018-06-01",
        "2018-07-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT COUNT(DISTINCT o.order_id) AS delivered_order_count
FROM fact_orders AS o
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_order_count",
        NumericTolerance(absolute=0, relative=0),
        derived_from=("sql/standard_metrics.sql#standard_delivered_order_count",),
    ),
    SingleMetricSpec(
        "D14_SM_002",
        "2017 全年下单且已送达的真实客户有多少？真实客户必须按 customer_unique_id 去重。",
        "delivered_customer_count",
        "medium",
        "distinct_entity_count",
        "2017-01-01",
        "2018-01-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT COUNT(DISTINCT c.customer_unique_id) AS delivered_customer_count
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_customer_count",
        NumericTolerance(absolute=0, relative=0),
        boundary_conditions=("customer_id must not be used as the real-customer key",),
        derived_from=("sql/standard_metrics.sql#standard_delivered_customer_count",),
    ),
    SingleMetricSpec(
        "D14_SM_003",
        "2018 年第一季度按下单时间统计的已送达订单商品 GMV 是多少？只汇总 price，不含运费。",
        "delivered_gmv",
        "easy",
        "scalar_amount",
        "2018-01-01",
        "2018-04-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(SUM(i.price), 2) AS delivered_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_gmv",
        NumericTolerance(absolute=0.01, relative=1e-9),
        derived_from=("sql/standard_metrics.sql#standard_delivered_gmv",),
    ),
    SingleMetricSpec(
        "D14_SM_004",
        "2017 年下半年下单并已送达的订单，其全部支付记录金额合计是多少？",
        "delivered_payment_amount",
        "medium",
        "scalar_amount",
        "2017-07-01",
        "2018-01-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(SUM(p.payment_value), 2) AS delivered_payment_amount
FROM fact_orders AS o
JOIN fact_payments AS p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_payment_amount",
        NumericTolerance(absolute=0.01, relative=1e-9),
        boundary_conditions=("payment rows must not be joined directly to item rows",),
        derived_from=("sql/standard_metrics.sql#standard_delivered_payment_amount",),
    ),
    SingleMetricSpec(
        "D14_SM_005",
        "2018 年 6 月已送达订单的客单价是多少？分子为不含运费 GMV，分母为唯一订单数。",
        "delivered_average_order_value",
        "hard",
        "scalar_average",
        "2018-06-01",
        "2018-07-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""WITH order_gmv AS (
    SELECT order_id, SUM(price) AS gmv
    FROM fact_order_items
    GROUP BY order_id
)
SELECT ROUND(
    1.0 * SUM(COALESCE(g.gmv, 0)) / NULLIF(COUNT(DISTINCT o.order_id), 0),
    2
) AS delivered_average_order_value
FROM fact_orders AS o
LEFT JOIN order_gmv AS g ON g.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_average_order_value",
        NumericTolerance(absolute=0.01, relative=1e-9),
        boundary_conditions=("denominator is distinct delivered orders",),
        derived_from=("sql/standard_metrics.sql#standard_delivered_average_order_value",),
    ),
    SingleMetricSpec(
        "D14_SM_006",
        "2017 年 8 月下单的已送达订单，商品金额与明细运费之和是多少？不要把它称为支付金额。",
        "delivered_gmv_including_freight",
        "easy",
        "scalar_amount",
        "2017-08-01",
        "2017-09-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(SUM(i.price + i.freight_value), 2) AS delivered_gmv_including_freight
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_gmv_including_freight",
        NumericTolerance(absolute=0.01, relative=1e-9),
        derived_from=("sql/standard_metrics.sql#standard_delivered_gmv_including_freight",),
    ),
    SingleMetricSpec(
        "D14_SM_007",
        "2018 年 5 月下单且已送达的订单，明细表 freight_value 的总运费是多少？",
        "delivered_freight_amount",
        "easy",
        "scalar_amount",
        "2018-05-01",
        "2018-06-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(SUM(i.freight_value), 2) AS delivered_freight_amount
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_freight_amount",
        NumericTolerance(absolute=0.01, relative=1e-9),
        derived_from=("sql/standard_metrics.sql#standard_delivered_freight_amount",),
    ),
    SingleMetricSpec(
        "D14_SM_008",
        "2017 年第四季度下单的订单中，状态属于 delivered、canceled 或 unavailable 的终态订单共有多少笔？",
        "terminal_order_count",
        "easy",
        "scalar_count",
        "2017-10-01",
        "2018-01-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT COUNT(DISTINCT o.order_id) AS terminal_order_count
FROM fact_orders AS o
WHERE o.order_status IN ('delivered', 'canceled', 'unavailable')
  AND {_window_where()}""",
        "terminal_order_count",
        NumericTolerance(absolute=0, relative=0),
        boundary_conditions=("non-terminal statuses are excluded",),
    ),
    SingleMetricSpec(
        "D14_SM_009",
        "2017 年按下单时间统计，状态明确为 canceled 的唯一订单数是多少？",
        "canceled_order_count",
        "easy",
        "scalar_count",
        "2017-01-01",
        "2018-01-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT COUNT(DISTINCT o.order_id) AS canceled_order_count
FROM fact_orders AS o
WHERE o.order_status = 'canceled'
  AND {_window_where()}""",
        "canceled_order_count",
        NumericTolerance(absolute=0, relative=0),
    ),
    SingleMetricSpec(
        "D14_SM_010",
        "2018 年上半年下单且状态为 unavailable 的唯一订单有多少笔？不要与取消订单合并。",
        "unavailable_order_count",
        "easy",
        "scalar_count",
        "2018-01-01",
        "2018-07-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT COUNT(DISTINCT o.order_id) AS unavailable_order_count
FROM fact_orders AS o
WHERE o.order_status = 'unavailable'
  AND {_window_where()}""",
        "unavailable_order_count",
        NumericTolerance(absolute=0, relative=0),
        boundary_conditions=("unavailable is distinct from canceled",),
    ),
    SingleMetricSpec(
        "D14_SM_011",
        "2018 年第二季度下单的终态订单取消率是多少？分子只含 canceled，分母含 delivered、canceled、unavailable。",
        "terminal_cancellation_rate",
        "medium",
        "scalar_rate",
        "2018-04-01",
        "2018-07-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(
    SUM(CASE WHEN o.order_status = 'canceled' THEN 1.0 ELSE 0 END)
    / NULLIF(COUNT(DISTINCT o.order_id), 0),
    6
) AS terminal_cancellation_rate
FROM fact_orders AS o
WHERE o.order_status IN ('delivered', 'canceled', 'unavailable')
  AND {_window_where()}""",
        "terminal_cancellation_rate",
        NumericTolerance(absolute=1e-6, relative=1e-9),
        boundary_conditions=("unavailable is in the denominator only",),
        derived_from=("sql/standard_metrics.sql#standard_terminal_cancellation_rate",),
    ),
    SingleMetricSpec(
        "D14_SM_012",
        "2018 年 2 月下单并已送达的订单包含多少条商品明细？这是明细行数，不是订单数或商品件数。",
        "delivered_item_count",
        "easy",
        "scalar_count",
        "2018-02-01",
        "2018-03-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT COUNT(i.order_item_id) AS delivered_item_count
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_item_count",
        NumericTolerance(absolute=0, relative=0),
        boundary_conditions=("order_item_id is counted, not summed",),
    ),
    SingleMetricSpec(
        "D14_SM_013",
        "2017 年第四季度下单且已送达的订单，平均每笔订单有多少条商品明细？",
        "average_items_per_delivered_order",
        "medium",
        "scalar_average",
        "2017-10-01",
        "2018-01-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(
    1.0 * COUNT(i.order_item_id) / NULLIF(COUNT(DISTINCT o.order_id), 0),
    6
) AS average_items_per_delivered_order
FROM fact_orders AS o
LEFT JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "average_items_per_delivered_order",
        NumericTolerance(absolute=1e-6, relative=1e-9),
        boundary_conditions=("item rows divided by distinct delivered orders",),
    ),
    SingleMetricSpec(
        "D14_SM_014",
        "2018 年 7 月下单且已送达订单的运费率是多少？运费率定义为 freight_value 总额除以不含运费 GMV。",
        "delivered_freight_to_gmv_rate",
        "medium",
        "scalar_rate",
        "2018-07-01",
        "2018-08-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(
    SUM(i.freight_value) / NULLIF(SUM(i.price), 0),
    6
) AS delivered_freight_to_gmv_rate
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}""",
        "delivered_freight_to_gmv_rate",
        NumericTolerance(absolute=1e-6, relative=1e-9),
        boundary_conditions=("numerator and denominator use the same scope",),
    ),
    SingleMetricSpec(
        "D14_SM_015",
        "2018 年第一季度内至少有两笔已送达订单的真实客户有多少？客户按 customer_unique_id 识别。",
        "period_repeat_customer_count",
        "hard",
        "repeat_customer_count",
        "2018-01-01",
        "2018-04-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""WITH customer_orders AS (
    SELECT c.customer_unique_id,
           COUNT(DISTINCT o.order_id) AS delivered_order_count
    FROM fact_orders AS o
    JOIN dim_customers AS c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
      AND {_window_where()}
    GROUP BY c.customer_unique_id
)
SELECT COUNT(*) AS period_repeat_customer_count
FROM customer_orders
WHERE delivered_order_count >= 2""",
        "period_repeat_customer_count",
        NumericTolerance(absolute=0, relative=0),
        boundary_conditions=("repeat behavior is evaluated only inside the period",),
    ),
    SingleMetricSpec(
        "D14_SM_016",
        "2018 年第二季度的期间复购率是多少？分子为期内至少两笔已送达订单的真实客户，分母为期内已送达真实客户。",
        "period_repeat_customer_rate",
        "hard",
        "repeat_customer_rate",
        "2018-04-01",
        "2018-07-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""WITH customer_orders AS (
    SELECT c.customer_unique_id,
           COUNT(DISTINCT o.order_id) AS delivered_order_count
    FROM fact_orders AS o
    JOIN dim_customers AS c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
      AND {_window_where()}
    GROUP BY c.customer_unique_id
)
SELECT ROUND(
    1.0 * SUM(CASE WHEN delivered_order_count >= 2 THEN 1 ELSE 0 END)
    / NULLIF(COUNT(*), 0),
    6
) AS period_repeat_customer_rate
FROM customer_orders""",
        "period_repeat_customer_rate",
        NumericTolerance(absolute=1e-6, relative=1e-9),
        boundary_conditions=("not the historical returning-customer rate",),
    ),
    SingleMetricSpec(
        "D14_SM_017",
        "2018 年 7 月已送达客户中，在 7 月 1 日之前已有已送达订单的真实客户占比是多少？",
        "historical_returning_customer_rate",
        "hard",
        "historical_returning_rate",
        "2018-07-01",
        "2018-08-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""WITH current_customers AS (
    SELECT DISTINCT c.customer_unique_id
    FROM fact_orders AS o
    JOIN dim_customers AS c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
      AND {_window_where()}
),
historical_customers AS (
    SELECT DISTINCT c.customer_unique_id
    FROM fact_orders AS o
    JOIN dim_customers AS c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp < :start_date
)
SELECT ROUND(
    1.0 * SUM(CASE WHEN h.customer_unique_id IS NOT NULL THEN 1 ELSE 0 END)
    / NULLIF(COUNT(*), 0),
    6
) AS historical_returning_customer_rate
FROM current_customers AS current
LEFT JOIN historical_customers AS h
    ON h.customer_unique_id = current.customer_unique_id""",
        "historical_returning_customer_rate",
        NumericTolerance(absolute=1e-6, relative=1e-9),
        boundary_conditions=(
            "history is before the period start",
            "dataset-start truncation remains a limitation",
        ),
    ),
    SingleMetricSpec(
        "D14_SM_018",
        "2017 年下单的已送达订单中，实际送达日期不晚于预计送达日期的订单占比是多少？只使用两日期均非空的订单。",
        "on_time_delivery_rate",
        "medium",
        "delivery_quality_rate",
        "2017-01-01",
        "2018-01-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(
    SUM(CASE WHEN o.order_delivered_customer_date <= o.order_estimated_delivery_date THEN 1.0 ELSE 0 END)
    / NULLIF(COUNT(DISTINCT o.order_id), 0),
    6
) AS on_time_delivery_rate
FROM fact_orders AS o
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_estimated_delivery_date IS NOT NULL
  AND {_window_where()}""",
        "on_time_delivery_rate",
        NumericTolerance(absolute=1e-6, relative=1e-9),
        derived_from=("sql/standard_metrics.sql#standard_on_time_delivery_rate",),
    ),
    SingleMetricSpec(
        "D14_SM_019",
        "2018 年 4 月下单的已送达订单，从下单到实际送达客户的平均自然日时长是多少？排除送达早于下单的记录。",
        "average_order_delivery_days",
        "medium",
        "duration_average",
        "2018-04-01",
        "2018-05-01",
        PeriodCompletenessExpectation.COMPLETE,
        f"""SELECT ROUND(
    AVG(julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp)),
    6
) AS average_order_delivery_days
FROM fact_orders AS o
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_customer_date >= o.order_purchase_timestamp
  AND {_window_where()}""",
        "average_order_delivery_days",
        NumericTolerance(absolute=1e-6, relative=1e-9),
        boundary_conditions=("invalid negative durations are excluded, not repaired",),
        derived_from=("sql/standard_metrics.sql#standard_average_order_delivery_days",),
    ),
    SingleMetricSpec(
        "D14_SM_020",
        "在 2016-09-01 至 2018-10-01 的下单记录中，送达客户时间早于交给承运商时间的唯一订单有多少？",
        "invalid_carrier_delivery_sequence_count",
        "medium",
        "data_quality_count",
        "2016-09-01",
        "2018-10-01",
        PeriodCompletenessExpectation.MIXED,
        f"""SELECT COUNT(DISTINCT o.order_id) AS invalid_carrier_delivery_sequence_count
FROM fact_orders AS o
WHERE o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_carrier_date IS NOT NULL
  AND o.order_delivered_customer_date < o.order_delivered_carrier_date
  AND {_window_where()}""",
        "invalid_carrier_delivery_sequence_count",
        NumericTolerance(absolute=0, relative=0),
        notes=("The range spans the recorded dataset and includes boundary months.",),
        boundary_conditions=("source anomalies are counted without modifying raw values",),
    ),
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raw_hashes(root: Path) -> dict[str, str]:
    return {
        path.name: _sha256_file(path)
        for path in sorted((root / "data/raw").iterdir())
        if path.is_file() and path.name != ".gitkeep"
    }


def _reference_sources(
    root: Path,
    spec: SingleMetricSpec,
    sql_relative_path: Path,
    sql_sha256: str,
) -> tuple[ReferenceSource, ...]:
    metric_dictionary = root / "data/metadata/metric_dictionary.csv"
    sources = [
        ReferenceSource(
            source_type=ReferenceSourceType.METRIC_DICTIONARY,
            locator=(
                "data/metadata/metric_dictionary.csv#" + spec.metric_id
            ),
            sha256=_sha256_file(metric_dictionary),
        ),
        ReferenceSource(
            source_type=(
                ReferenceSourceType.ASSISTANT_AUTHORED_SQLITE_VERIFIED_SQL
            ),
            locator=sql_relative_path.as_posix(),
            sha256=sql_sha256,
        ),
    ]
    if spec.derived_from:
        sources.append(
            ReferenceSource(
                source_type=ReferenceSourceType.STANDARD_SQL,
                locator="|".join(spec.derived_from),
                sha256=_sha256_file(root / "sql/standard_metrics.sql"),
                notes="Derived and parameterized; not copied as the sole gold standard.",
            )
        )
    return tuple(sources)


def _build_case(
    root: Path,
    spec: SingleMetricSpec,
    sql_relative_path: Path,
    sql_sha256: str,
    result_relative_path: Path,
    result_sha256: str,
) -> EvaluationCase:
    return EvaluationCase(
        case_id=spec.case_id,
        question=spec.question,
        category=DatasetCategory.SINGLE_METRIC,
        difficulty=spec.difficulty,
        analysis_type=spec.analysis_type,
        expected_workflow_status=WorkflowStatus.SUCCEEDED,
        allowed_stop_reasons=("completed", "first_attempt_succeeded", "repair_succeeded"),
        expected_calculation_status=CalculationStatus.NOT_APPLICABLE,
        metric_ids=(spec.metric_id,),
        dimensions=(),
        time_scope=TimeScope(
            mode=TimeScopeMode.BOUNDED,
            start_date=date.fromisoformat(spec.start_date),
            end_date_exclusive=date.fromisoformat(spec.end_date_exclusive),
            time_field="order_purchase_timestamp",
            completeness=spec.completeness,
            notes="Half-open interval; attribution uses purchase timestamp.",
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
            summary=f"One scalar row with column {spec.result_column}.",
        ),
        comparison_rules=ComparisonRules(
            expected_columns=(spec.result_column,),
            row_comparison=RowComparison.SINGLE_ROW,
            numeric_tolerance=spec.tolerance,
        ),
        should_enter_sqlite=True,
        allows_repair=True,
        safety_expectation=SafetyExpectation(
            decision=SafetyDecision.ALLOW_READ_ONLY_EXECUTION,
            expected_execution_started=True,
            maximum_allowed_repair_attempts=2,
            reason="A valid scalar SELECT should pass the existing safety gate and execute read-only.",
        ),
        provenance=Provenance(
            case_authorship=Authorship.ASSISTANT_MECHANICAL,
            reference_authorship=Authorship.ASSISTANT_MECHANICAL,
            user_review_status=UserReviewStatus.NOT_REVIEWED,
            sqlite_verification_status=SqliteVerificationStatus.VERIFIED_REAL_SQLITE,
            business_reference_status=BusinessReferenceStatus.NOT_INDEPENDENTLY_EVALUATED,
            reference_sources=_reference_sources(
                root, spec, sql_relative_path, sql_sha256
            ),
            derived_from=spec.derived_from,
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
                reason="Frozen benchmark module 3 assistant-authored fixed single-metric case.",
            ),
        ),
    )


def build_single_metric_cases(root: Path) -> tuple[EvaluationCase, ...]:
    _, draft_path = write_schema_artifacts(root)
    dataset = load_dataset(draft_path)
    database = root / DATABASE_RELATIVE_PATH
    database_hash_before = _sha256_file(database)
    raw_hashes_before = _raw_hashes(root)
    sql_directory = root / "data/evaluation/benchmark_v1/references/sql"
    result_directory = root / "data/evaluation/benchmark_v1/references/results"
    sql_directory.mkdir(parents=True, exist_ok=True)
    result_directory.mkdir(parents=True, exist_ok=True)
    policy = build_global_sql_policy(root, max_rows=10)
    cases: list[EvaluationCase] = []
    report_rows = []

    if len(SPECS) != 20 or len({spec.metric_id for spec in SPECS}) != 20:
        raise ValueError("单指标模块必须恰好包含 20 题且使用 20 个不同指标")

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
                f"{spec.case_id} reference SQL failed: "
                f"{execution.error_type} {execution.error_message}"
            )
        if not execution.execution_started or execution.rows_truncated:
            raise RuntimeError(f"{spec.case_id} reference execution was incomplete")
        if execution.columns != (spec.result_column,) or len(execution.rows) != 1:
            raise RuntimeError(f"{spec.case_id} did not return one expected scalar row")

        result_payload = {
            "case_id": spec.case_id,
            "reference_type": "assistant_authored_sql_verified_on_real_sqlite",
            "business_reference_status": "not_independently_evaluated",
            "database_sha256": database_hash_before,
            "sql_sha256": sql_sha256,
            "parameters": spec.parameters,
            "columns": list(execution.columns),
            "rows": list(execution.rows),
            "safety_gate_accepted": execution.safety_trace.accepted,
            "execution_started": execution.execution_started,
            "rows_truncated": execution.rows_truncated,
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
        case = _build_case(
            root,
            spec,
            sql_relative_path,
            sql_sha256,
            result_relative_path,
            result_sha256,
        )
        cases.append(case)
        report_rows.append(
            {
                "case_id": spec.case_id,
                "metric_id": spec.metric_id,
                "difficulty": spec.difficulty,
                "analysis_type": spec.analysis_type,
                "reference_value": execution.rows[0][spec.result_column],
                "sql_sha256": sql_sha256,
                "result_sha256": result_sha256,
                "safety_gate_accepted": execution.safety_trace.accepted,
                "execution_started": execution.execution_started,
                "business_reference_status": "not_independently_evaluated",
            }
        )

    retained_cases = tuple(
        case
        for case in dataset.cases
        if case.category is not DatasetCategory.SINGLE_METRIC
    )
    history_reason = "Added 20 assistant-authored, real-SQLite-verified single-metric cases."
    history = tuple(
        item for item in dataset.provenance.change_history if item.reason != history_reason
    ) + (
        ChangeRecord(
            changed_on=BUILD_DATE,
            changed_by=ChangeActor.ASSISTANT,
            change_type="modified",
            reason=history_reason,
        ),
    )
    updated_provenance = dataset.provenance.model_copy(
        update={
            "updated_on": BUILD_DATE,
            "authorship_disclosure": (
                "Dataset schema and the first 20 single-metric cases were mechanically "
                "authored by the assistant. The cases have real SQLite verification "
                "but no user review or independent business reference yet."
            ),
            "change_history": history,
        }
    )
    updated_dataset = dataset.model_copy(
        update={
            "dataset_version": "0.2.0",
            "provenance": updated_provenance,
            "cases": tuple(cases) + retained_cases,
        }
    )
    updated_dataset = type(dataset).model_validate(updated_dataset.model_dump(mode="python"))
    draft_path.write_text(
        updated_dataset.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    database_hash_after = _sha256_file(database)
    raw_hashes_after = _raw_hashes(root)
    report = {
        "measurement_scope": (
            "assistant_authored_fixed_single_metric_cases; standard_sql_passed_existing_"
            "safety_gate_and_executed_real_local_sqlite; no_candidate_model_run; "
            "no_independent_business_accuracy"
        ),
        "external_api_calls": 0,
        "model_generated_numeric_results": 0,
        "case_count": len(cases),
        "unique_metric_count": len({case.metric_ids[0] for case in cases}),
        "category": DatasetCategory.SINGLE_METRIC.value,
        "difficulty_counts": {
            level: sum(case.difficulty.value == level for case in cases)
            for level in ("easy", "medium", "hard")
        },
        "named_parameter_contract_pass_count": len(cases),
        "safety_gate_pass_count": sum(row["safety_gate_accepted"] for row in report_rows),
        "real_sqlite_execution_count": sum(row["execution_started"] for row in report_rows),
        "user_reviewed_case_count": 0,
        "independent_business_reference_case_count": 0,
        "database_sha256_before": database_hash_before,
        "database_sha256_after": database_hash_after,
        "database_unchanged": database_hash_before == database_hash_after,
        "raw_file_hashes_before": raw_hashes_before,
        "raw_file_hashes_after": raw_hashes_after,
        "raw_files_unchanged": raw_hashes_before == raw_hashes_after,
        "cases": report_rows,
    }
    report_path = root / "docs/reports/benchmark_metric_cases.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return tuple(cases)


def main() -> None:
    root = Path(__file__).parents[2]
    cases = build_single_metric_cases(root)
    print(f"Frozen benchmark single-metric cases: {len(cases)}/20")
    print("All reference SQL passed safety and executed real SQLite.")
    print("External API calls: 0")


if __name__ == "__main__":
    main()
