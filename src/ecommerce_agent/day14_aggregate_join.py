"""Build and verify the 20 Day 14 aggregate/filter/join evaluation cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.ecommerce_agent.day11_state import WorkflowStatus
from src.ecommerce_agent.day14_schema import (
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
from src.ecommerce_agent.day14_single_metric import (
    BUILD_DATE,
    DATABASE_RELATIVE_PATH,
    _raw_hashes,
    _sha256_file,
    _window_where,
)
from src.ecommerce_agent.metric_catalog import MetricCatalog
from src.ecommerce_agent.sql_generation import execute_read_only_query
from src.ecommerce_agent.sql_safety import build_global_sql_policy


ZERO_TOLERANCE = NumericTolerance(absolute=0, relative=0)
AMOUNT_TOLERANCE = NumericTolerance(absolute=0.01, relative=1e-9)
RATE_TOLERANCE = NumericTolerance(absolute=1e-6, relative=1e-9)


@dataclass(frozen=True)
class AggregateJoinSpec:
    case_id: str
    question: str
    metric_id: str
    dimension: str
    difficulty: str
    analysis_type: str
    start_date: str
    end_date_exclusive: str
    sql: str
    columns: tuple[str, ...]
    order: tuple[tuple[str, SortDirection], ...]
    tolerances: dict[str, NumericTolerance]
    notes: tuple[str, ...] = ()
    boundary_conditions: tuple[str, ...] = ()
    derived_from: tuple[str, ...] = ()

    @property
    def parameters(self) -> dict[str, str]:
        return {
            "start_date": self.start_date,
            "end_date_exclusive": self.end_date_exclusive,
        }


SPECS = (
    AggregateJoinSpec(
        "D14_AJ_001",
        "按下单自然月列出 2018 年上半年每月的已送达唯一订单数，月份升序且缺月不补零。",
        "delivered_order_count",
        "purchase_month",
        "easy",
        "monthly_grouped_count",
        "2018-01-01",
        "2018-07-01",
        f"""SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       COUNT(DISTINCT o.order_id) AS delivered_order_count
FROM fact_orders AS o
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month ASC""",
        ("purchase_month", "delivered_order_count"),
        (("purchase_month", SortDirection.ASCENDING),),
        {"delivered_order_count": ZERO_TOLERANCE},
        boundary_conditions=("missing calendar months are not synthesized",),
    ),
    AggregateJoinSpec(
        "D14_AJ_002",
        "2018 年第二季度按客户州汇总已送达订单的不含运费 GMV，返回 GMV 最高的 5 个州；金额降序、同额州代码升序。",
        "delivered_gmv",
        "customer_state",
        "medium",
        "top_n_grouped_amount",
        "2018-04-01",
        "2018-07-01",
        f"""SELECT c.customer_state,
       ROUND(SUM(i.price), 2) AS delivered_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY c.customer_state
ORDER BY delivered_gmv DESC, c.customer_state ASC
LIMIT 5""",
        ("customer_state", "delivered_gmv"),
        (
            ("delivered_gmv", SortDirection.DESCENDING),
            ("customer_state", SortDirection.ASCENDING),
        ),
        {"delivered_gmv": AMOUNT_TOLERANCE},
    ),
    AggregateJoinSpec(
        "D14_AJ_003",
        "对 2018 年 6 月已送达订单按商品品类计算不含运费 GMV 的稠密排名，保留排名前 3 的品类并保留 unknown。",
        "delivered_monthly_category_gmv_rank",
        "product_category",
        "hard",
        "dense_rank_top_n",
        "2018-06-01",
        "2018-07-01",
        f"""WITH category_gmv AS (
    SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
           SUM(i.price) AS category_gmv
    FROM fact_orders AS o
    JOIN fact_order_items AS i ON i.order_id = o.order_id
    LEFT JOIN dim_products AS p ON p.product_id = i.product_id
    WHERE o.order_status = 'delivered'
      AND {_window_where()}
    GROUP BY COALESCE(p.product_category_name, 'unknown')
),
ranked AS (
    SELECT product_category,
           category_gmv,
           DENSE_RANK() OVER (ORDER BY category_gmv DESC) AS category_rank
    FROM category_gmv
)
SELECT product_category,
       ROUND(category_gmv, 2) AS category_gmv,
       category_rank
FROM ranked
WHERE category_rank <= 3
ORDER BY category_rank ASC, product_category ASC""",
        ("product_category", "category_gmv", "category_rank"),
        (
            ("category_rank", SortDirection.ASCENDING),
            ("product_category", SortDirection.ASCENDING),
        ),
        {
            "category_gmv": AMOUNT_TOLERANCE,
            "category_rank": ZERO_TOLERANCE,
        },
        boundary_conditions=(
            "dense rank may return more than three rows when the third rank ties",
            "missing categories remain unknown",
        ),
        derived_from=("sql/day04_advanced_metrics.sql#monthly_category_top3",),
    ),
    AggregateJoinSpec(
        "D14_AJ_004",
        "2018 年 7 月已送达订单中，按 seller_id 汇总不含运费 GMV，返回前 10 位卖家并稳定处理并列金额。",
        "delivered_gmv",
        "seller",
        "medium",
        "seller_top_n",
        "2018-07-01",
        "2018-08-01",
        f"""SELECT i.seller_id AS seller,
       ROUND(SUM(i.price), 2) AS delivered_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY i.seller_id
ORDER BY delivered_gmv DESC, i.seller_id ASC
LIMIT 10""",
        ("seller", "delivered_gmv"),
        (
            ("delivered_gmv", SortDirection.DESCENDING),
            ("seller", SortDirection.ASCENDING),
        ),
        {"delivered_gmv": AMOUNT_TOLERANCE},
    ),
    AggregateJoinSpec(
        "D14_AJ_005",
        "2018 年第一季度下单且已送达的订单，按支付方式汇总支付金额；金额降序、支付方式升序。",
        "delivered_payment_amount",
        "payment_type",
        "easy",
        "grouped_payment_amount",
        "2018-01-01",
        "2018-04-01",
        f"""SELECT p.payment_type,
       ROUND(SUM(p.payment_value), 2) AS delivered_payment_amount
FROM fact_orders AS o
JOIN fact_payments AS p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY p.payment_type
ORDER BY delivered_payment_amount DESC, p.payment_type ASC""",
        ("payment_type", "delivered_payment_amount"),
        (
            ("delivered_payment_amount", SortDirection.DESCENDING),
            ("payment_type", SortDirection.ASCENDING),
        ),
        {"delivered_payment_amount": AMOUNT_TOLERANCE},
        boundary_conditions=("payment records are not joined to item rows",),
    ),
    AggregateJoinSpec(
        "D14_AJ_006",
        "按客户州统计 2017 年至少有一笔已送达订单的真实客户数；每个州内按 customer_unique_id 去重，并按客户数降序。",
        "delivered_customer_count",
        "customer_state",
        "medium",
        "grouped_distinct_customer_count",
        "2017-01-01",
        "2018-01-01",
        f"""SELECT c.customer_state,
       COUNT(DISTINCT c.customer_unique_id) AS delivered_customer_count
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY c.customer_state
ORDER BY delivered_customer_count DESC, c.customer_state ASC""",
        ("customer_state", "delivered_customer_count"),
        (
            ("delivered_customer_count", SortDirection.DESCENDING),
            ("customer_state", SortDirection.ASCENDING),
        ),
        {"delivered_customer_count": ZERO_TOLERANCE},
        boundary_conditions=("state-level unique customers are not additive across states",),
    ),
    AggregateJoinSpec(
        "D14_AJ_007",
        "2018 年上半年按客户州计算终态订单取消率，分母只含 delivered、canceled、unavailable；取消率降序。",
        "terminal_cancellation_rate",
        "customer_state",
        "hard",
        "grouped_conditional_rate",
        "2018-01-01",
        "2018-07-01",
        f"""SELECT c.customer_state,
       ROUND(
           SUM(CASE WHEN o.order_status = 'canceled' THEN 1.0 ELSE 0 END)
           / NULLIF(COUNT(DISTINCT o.order_id), 0),
           6
       ) AS terminal_cancellation_rate
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status IN ('delivered', 'canceled', 'unavailable')
  AND {_window_where()}
GROUP BY c.customer_state
ORDER BY terminal_cancellation_rate DESC, c.customer_state ASC""",
        ("customer_state", "terminal_cancellation_rate"),
        (
            ("terminal_cancellation_rate", SortDirection.DESCENDING),
            ("customer_state", SortDirection.ASCENDING),
        ),
        {"terminal_cancellation_rate": RATE_TOLERANCE},
        boundary_conditions=("unavailable is in the denominator but not the numerator",),
    ),
    AggregateJoinSpec(
        "D14_AJ_008",
        "在 2017 年至少有 100 笔可比较已送达订单的客户州中，计算按时送达率并返回最高的 10 个州。",
        "on_time_delivery_rate",
        "customer_state",
        "hard",
        "filtered_grouped_rate_top_n",
        "2017-01-01",
        "2018-01-01",
        f"""SELECT c.customer_state,
       ROUND(
           SUM(CASE WHEN o.order_delivered_customer_date <= o.order_estimated_delivery_date THEN 1.0 ELSE 0 END)
           / NULLIF(COUNT(DISTINCT o.order_id), 0),
           6
       ) AS on_time_delivery_rate
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_estimated_delivery_date IS NOT NULL
  AND {_window_where()}
GROUP BY c.customer_state
HAVING COUNT(DISTINCT o.order_id) >= 100
ORDER BY on_time_delivery_rate DESC, c.customer_state ASC
LIMIT 10""",
        ("customer_state", "on_time_delivery_rate"),
        (
            ("on_time_delivery_rate", SortDirection.DESCENDING),
            ("customer_state", SortDirection.ASCENDING),
        ),
        {"on_time_delivery_rate": RATE_TOLERANCE},
        notes=("HAVING applies to valid comparable orders, not all created orders.",),
    ),
    AggregateJoinSpec(
        "D14_AJ_009",
        "2018 年第一季度按客户州计算已送达订单从下单到实际送达的平均自然日时长，返回最慢的 5 个州。",
        "average_order_delivery_days",
        "customer_state",
        "medium",
        "grouped_duration_top_n",
        "2018-01-01",
        "2018-04-01",
        f"""SELECT c.customer_state,
       ROUND(
           AVG(julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp)),
           6
       ) AS average_order_delivery_days
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_customer_date >= o.order_purchase_timestamp
  AND {_window_where()}
GROUP BY c.customer_state
ORDER BY average_order_delivery_days DESC, c.customer_state ASC
LIMIT 5""",
        ("customer_state", "average_order_delivery_days"),
        (
            ("average_order_delivery_days", SortDirection.DESCENDING),
            ("customer_state", SortDirection.ASCENDING),
        ),
        {"average_order_delivery_days": RATE_TOLERANCE},
        boundary_conditions=("negative delivery durations are excluded",),
    ),
    AggregateJoinSpec(
        "D14_AJ_010",
        "2017 年 12 月已送达订单中，按商品品类统计明细行数，返回明细数最多的 10 个品类并保留 unknown。",
        "delivered_item_count",
        "product_category",
        "easy",
        "category_top_n_count",
        "2017-12-01",
        "2018-01-01",
        f"""SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
       COUNT(i.order_item_id) AS delivered_item_count
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
LEFT JOIN dim_products AS p ON p.product_id = i.product_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY COALESCE(p.product_category_name, 'unknown')
ORDER BY delivered_item_count DESC, product_category ASC
LIMIT 10""",
        ("product_category", "delivered_item_count"),
        (
            ("delivered_item_count", SortDirection.DESCENDING),
            ("product_category", SortDirection.ASCENDING),
        ),
        {"delivered_item_count": ZERO_TOLERANCE},
        boundary_conditions=("item rows are counted, not item sequence values",),
    ),
    AggregateJoinSpec(
        "D14_AJ_011",
        "2018 年 5 月按商品品类计算已送达运费率，返回运费率最高的 10 个品类；每组分子分母使用同一范围。",
        "delivered_freight_to_gmv_rate",
        "product_category",
        "medium",
        "category_ratio_top_n",
        "2018-05-01",
        "2018-06-01",
        f"""SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
       ROUND(
           SUM(i.freight_value) / NULLIF(SUM(i.price), 0),
           6
       ) AS delivered_freight_to_gmv_rate
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
LEFT JOIN dim_products AS p ON p.product_id = i.product_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY COALESCE(p.product_category_name, 'unknown')
ORDER BY delivered_freight_to_gmv_rate DESC, product_category ASC
LIMIT 10""",
        ("product_category", "delivered_freight_to_gmv_rate"),
        (
            ("delivered_freight_to_gmv_rate", SortDirection.DESCENDING),
            ("product_category", SortDirection.ASCENDING),
        ),
        {"delivered_freight_to_gmv_rate": RATE_TOLERANCE},
    ),
    AggregateJoinSpec(
        "D14_AJ_012",
        "2018 年第二季度按客户州计算已送达订单平均明细数，分母为州内唯一已送达订单，并按结果降序。",
        "average_items_per_delivered_order",
        "customer_state",
        "medium",
        "grouped_items_per_order",
        "2018-04-01",
        "2018-07-01",
        f"""SELECT c.customer_state,
       ROUND(
           1.0 * COUNT(i.order_item_id) / NULLIF(COUNT(DISTINCT o.order_id), 0),
           6
       ) AS average_items_per_delivered_order
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
LEFT JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY c.customer_state
ORDER BY average_items_per_delivered_order DESC, c.customer_state ASC""",
        ("customer_state", "average_items_per_delivered_order"),
        (
            ("average_items_per_delivered_order", SortDirection.DESCENDING),
            ("customer_state", SortDirection.ASCENDING),
        ),
        {"average_items_per_delivered_order": RATE_TOLERANCE},
    ),
    AggregateJoinSpec(
        "D14_AJ_013",
        "按客户州计算 2017 年期间复购率：州内至少两笔已送达订单的真实客户数除以州内已送达真实客户数。",
        "period_repeat_customer_rate",
        "customer_state",
        "hard",
        "grouped_repeat_rate",
        "2017-01-01",
        "2018-01-01",
        f"""WITH state_customer_orders AS (
    SELECT c.customer_state,
           c.customer_unique_id,
           COUNT(DISTINCT o.order_id) AS delivered_order_count
    FROM fact_orders AS o
    JOIN dim_customers AS c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
      AND {_window_where()}
    GROUP BY c.customer_state, c.customer_unique_id
)
SELECT customer_state,
       ROUND(
           1.0 * SUM(CASE WHEN delivered_order_count >= 2 THEN 1 ELSE 0 END)
           / NULLIF(COUNT(*), 0),
           6
       ) AS period_repeat_customer_rate
FROM state_customer_orders
GROUP BY customer_state
ORDER BY period_repeat_customer_rate DESC, customer_state ASC""",
        ("customer_state", "period_repeat_customer_rate"),
        (
            ("period_repeat_customer_rate", SortDirection.DESCENDING),
            ("customer_state", SortDirection.ASCENDING),
        ),
        {"period_repeat_customer_rate": RATE_TOLERANCE},
        boundary_conditions=("state-level customer groups cannot be summed across states",),
    ),
    AggregateJoinSpec(
        "D14_AJ_014",
        "2018 年 6 月下单且已送达的订单，按客户城市汇总支付金额，返回金额最高的 10 个城市。",
        "delivered_payment_amount",
        "customer_city",
        "medium",
        "city_payment_top_n",
        "2018-06-01",
        "2018-07-01",
        f"""SELECT c.customer_city,
       ROUND(SUM(p.payment_value), 2) AS delivered_payment_amount
FROM fact_orders AS o
JOIN fact_payments AS p ON p.order_id = o.order_id
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY c.customer_city
ORDER BY delivered_payment_amount DESC, c.customer_city ASC
LIMIT 10""",
        ("customer_city", "delivered_payment_amount"),
        (
            ("delivered_payment_amount", SortDirection.DESCENDING),
            ("customer_city", SortDirection.ASCENDING),
        ),
        {"delivered_payment_amount": AMOUNT_TOLERANCE},
        boundary_conditions=("payment rows join to customers through orders, not items",),
    ),
    AggregateJoinSpec(
        "D14_AJ_015",
        "列出 2017 年下半年每个下单月的已送达 GMV，不含运费，按自然月升序且不补缺失月份。",
        "delivered_monthly_gmv",
        "purchase_month",
        "easy",
        "monthly_amount_trend",
        "2017-07-01",
        "2018-01-01",
        f"""SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       ROUND(SUM(i.price), 2) AS delivered_monthly_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month ASC""",
        ("purchase_month", "delivered_monthly_gmv"),
        (("purchase_month", SortDirection.ASCENDING),),
        {"delivered_monthly_gmv": AMOUNT_TOLERANCE},
    ),
    AggregateJoinSpec(
        "D14_AJ_016",
        "按下单自然月汇总 2018 年上半年已送达订单的支付金额，月份升序；不得与订单明细直接联表。",
        "delivered_payment_amount",
        "purchase_month",
        "medium",
        "monthly_payment_trend",
        "2018-01-01",
        "2018-07-01",
        f"""SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       ROUND(SUM(p.payment_value), 2) AS delivered_payment_amount
FROM fact_orders AS o
JOIN fact_payments AS p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month ASC""",
        ("purchase_month", "delivered_payment_amount"),
        (("purchase_month", SortDirection.ASCENDING),),
        {"delivered_payment_amount": AMOUNT_TOLERANCE},
        boundary_conditions=("payment facts remain separate from item facts",),
    ),
    AggregateJoinSpec(
        "D14_AJ_017",
        "2018 年第一季度已送达订单中，按 seller_id 汇总明细运费，返回运费最高的 10 位卖家。",
        "delivered_freight_amount",
        "seller",
        "medium",
        "seller_freight_top_n",
        "2018-01-01",
        "2018-04-01",
        f"""SELECT i.seller_id AS seller,
       ROUND(SUM(i.freight_value), 2) AS delivered_freight_amount
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY i.seller_id
ORDER BY delivered_freight_amount DESC, i.seller_id ASC
LIMIT 10""",
        ("seller", "delivered_freight_amount"),
        (
            ("delivered_freight_amount", SortDirection.DESCENDING),
            ("seller", SortDirection.ASCENDING),
        ),
        {"delivered_freight_amount": AMOUNT_TOLERANCE},
    ),
    AggregateJoinSpec(
        "D14_AJ_018",
        "2018 年第一季度按商品品类汇总已送达订单的商品金额加运费，返回最高的 10 个品类并保留 unknown。",
        "delivered_gmv_including_freight",
        "product_category",
        "medium",
        "category_gmv_freight_top_n",
        "2018-01-01",
        "2018-04-01",
        f"""SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
       ROUND(SUM(i.price + i.freight_value), 2) AS delivered_gmv_including_freight
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
LEFT JOIN dim_products AS p ON p.product_id = i.product_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY COALESCE(p.product_category_name, 'unknown')
ORDER BY delivered_gmv_including_freight DESC, product_category ASC
LIMIT 10""",
        ("product_category", "delivered_gmv_including_freight"),
        (
            ("delivered_gmv_including_freight", SortDirection.DESCENDING),
            ("product_category", SortDirection.ASCENDING),
        ),
        {"delivered_gmv_including_freight": AMOUNT_TOLERANCE},
    ),
    AggregateJoinSpec(
        "D14_AJ_019",
        "按下单月对账 2018 年上半年已送达订单：分别先按 order_id 聚合明细和支付，再列出 GMV、运费、支付金额及支付对账差额。",
        "delivered_payment_reconciliation_difference",
        "purchase_month",
        "hard",
        "preaggregated_fact_reconciliation",
        "2018-01-01",
        "2018-07-01",
        f"""WITH item_totals AS (
    SELECT order_id,
           SUM(price) AS order_gmv,
           SUM(freight_value) AS order_freight
    FROM fact_order_items
    GROUP BY order_id
),
payment_totals AS (
    SELECT order_id,
           SUM(payment_value) AS order_payment
    FROM fact_payments
    GROUP BY order_id
)
SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       ROUND(SUM(COALESCE(i.order_gmv, 0)), 2) AS delivered_gmv,
       ROUND(SUM(COALESCE(i.order_freight, 0)), 2) AS delivered_freight_amount,
       ROUND(SUM(COALESCE(p.order_payment, 0)), 2) AS delivered_payment_amount,
       ROUND(
           SUM(COALESCE(p.order_payment, 0))
           - SUM(COALESCE(i.order_gmv, 0))
           - SUM(COALESCE(i.order_freight, 0)),
           2
       ) AS delivered_payment_reconciliation_difference
FROM fact_orders AS o
LEFT JOIN item_totals AS i ON i.order_id = o.order_id
LEFT JOIN payment_totals AS p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND {_window_where()}
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month ASC""",
        (
            "purchase_month",
            "delivered_gmv",
            "delivered_freight_amount",
            "delivered_payment_amount",
            "delivered_payment_reconciliation_difference",
        ),
        (("purchase_month", SortDirection.ASCENDING),),
        {
            "delivered_gmv": AMOUNT_TOLERANCE,
            "delivered_freight_amount": AMOUNT_TOLERANCE,
            "delivered_payment_amount": AMOUNT_TOLERANCE,
            "delivered_payment_reconciliation_difference": AMOUNT_TOLERANCE,
        },
        boundary_conditions=(
            "item and payment facts are independently preaggregated to order_id",
            "payment minus GMV alone is not the reconciliation difference",
        ),
        derived_from=("sql/day04_advanced_metrics.sql#monthly_gmv_payment_reconciliation",),
    ),
    AggregateJoinSpec(
        "D14_AJ_020",
        "在 2016-09-01 至 2018-10-01 的下单记录中，按客户州统计送达客户时间早于交承运商时间的唯一订单数，按异常数降序。",
        "invalid_carrier_delivery_sequence_count",
        "customer_state",
        "medium",
        "grouped_data_quality_count",
        "2016-09-01",
        "2018-10-01",
        f"""SELECT c.customer_state,
       COUNT(DISTINCT o.order_id) AS invalid_carrier_delivery_sequence_count
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_carrier_date IS NOT NULL
  AND o.order_delivered_customer_date < o.order_delivered_carrier_date
  AND {_window_where()}
GROUP BY c.customer_state
ORDER BY invalid_carrier_delivery_sequence_count DESC, c.customer_state ASC""",
        ("customer_state", "invalid_carrier_delivery_sequence_count"),
        (
            ("invalid_carrier_delivery_sequence_count", SortDirection.DESCENDING),
            ("customer_state", SortDirection.ASCENDING),
        ),
        {"invalid_carrier_delivery_sequence_count": ZERO_TOLERANCE},
        boundary_conditions=("source anomalies remain unchanged",),
    ),
)


def _sources(
    root: Path,
    spec: AggregateJoinSpec,
    sql_relative_path: Path,
    sql_sha256: str,
) -> tuple[ReferenceSource, ...]:
    sources = [
        ReferenceSource(
            source_type=ReferenceSourceType.METRIC_DICTIONARY,
            locator=f"data/metadata/metric_dictionary.csv#{spec.metric_id}",
            sha256=_sha256_file(root / "data/metadata/metric_dictionary.csv"),
        ),
        ReferenceSource(
            source_type=ReferenceSourceType.DIMENSION_DICTIONARY,
            locator=f"data/metadata/dimension_dictionary.csv#{spec.dimension}",
            sha256=_sha256_file(root / "data/metadata/dimension_dictionary.csv"),
        ),
        ReferenceSource(
            source_type=ReferenceSourceType.ASSISTANT_AUTHORED_SQLITE_VERIFIED_SQL,
            locator=sql_relative_path.as_posix(),
            sha256=sql_sha256,
        ),
    ]
    if spec.derived_from:
        sources.append(
            ReferenceSource(
                source_type=ReferenceSourceType.DAY04_ADVANCED_SQL,
                locator="|".join(spec.derived_from),
                sha256=_sha256_file(root / "sql/day04_advanced_metrics.sql"),
                notes="Parameterized derivative revalidated on the fixed database.",
            )
        )
    return tuple(sources)


def _case(
    root: Path,
    spec: AggregateJoinSpec,
    sql_relative_path: Path,
    sql_sha256: str,
    result_relative_path: Path,
    result_sha256: str,
) -> EvaluationCase:
    return EvaluationCase(
        case_id=spec.case_id,
        question=spec.question,
        category=DatasetCategory.AGGREGATE_FILTER_JOIN,
        difficulty=spec.difficulty,
        analysis_type=spec.analysis_type,
        expected_workflow_status=WorkflowStatus.SUCCEEDED,
        allowed_stop_reasons=("completed", "first_attempt_succeeded", "repair_succeeded"),
        expected_calculation_status=CalculationStatus.NOT_APPLICABLE,
        metric_ids=(spec.metric_id,),
        dimensions=(spec.dimension,),
        time_scope=TimeScope(
            mode=TimeScopeMode.BOUNDED,
            start_date=date.fromisoformat(spec.start_date),
            end_date_exclusive=date.fromisoformat(spec.end_date_exclusive),
            time_field="order_purchase_timestamp",
            completeness=(
                PeriodCompletenessExpectation.MIXED
                if spec.case_id == "D14_AJ_020"
                else PeriodCompletenessExpectation.COMPLETE
            ),
            notes="Half-open purchase-time interval.",
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
                f"Grouped result with columns {', '.join(spec.columns)} and stable order."
            ),
        ),
        comparison_rules=ComparisonRules(
            expected_columns=spec.columns,
            row_comparison=RowComparison.ORDERED,
            order_keys=tuple(
                OrderKey(
                    column=column,
                    direction=direction,
                    nulls=NullOrdering.NATIVE,
                )
                for column, direction in spec.order
            ),
            numeric_tolerance=ZERO_TOLERANCE,
            numeric_tolerance_by_column=spec.tolerances,
        ),
        should_enter_sqlite=True,
        allows_repair=True,
        safety_expectation=SafetyExpectation(
            decision=SafetyDecision.ALLOW_READ_ONLY_EXECUTION,
            expected_execution_started=True,
            maximum_allowed_repair_attempts=2,
            reason="Grouped read-only reference SQL must pass the existing safety gate.",
        ),
        provenance=Provenance(
            case_authorship=Authorship.ASSISTANT_MECHANICAL,
            reference_authorship=Authorship.ASSISTANT_MECHANICAL,
            user_review_status=UserReviewStatus.NOT_REVIEWED,
            sqlite_verification_status=SqliteVerificationStatus.VERIFIED_REAL_SQLITE,
            business_reference_status=BusinessReferenceStatus.NOT_INDEPENDENTLY_EVALUATED,
            reference_sources=_sources(root, spec, sql_relative_path, sql_sha256),
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
                reason="Day 14 module 4 assistant-authored aggregate/filter/join case.",
            ),
        ),
    )


def build_aggregate_join_cases(root: Path) -> tuple[EvaluationCase, ...]:
    _, draft_path = write_schema_artifacts(root)
    dataset = load_dataset(draft_path)
    database = root / DATABASE_RELATIVE_PATH
    database_hash_before = _sha256_file(database)
    raw_before = _raw_hashes(root)
    catalog = MetricCatalog.from_csv(
        root / "data/metadata/metric_dictionary.csv",
        root / "data/metadata/dimension_dictionary.csv",
    )
    policy = build_global_sql_policy(root, max_rows=1000)
    sql_directory = root / "data/evaluation/day14/references/sql"
    result_directory = root / "data/evaluation/day14/references/results"
    sql_directory.mkdir(parents=True, exist_ok=True)
    result_directory.mkdir(parents=True, exist_ok=True)
    cases = []
    report_rows = []

    if len(SPECS) != 20 or len({spec.case_id for spec in SPECS}) != 20:
        raise ValueError("聚合/筛选/多表模块必须恰好包含 20 个唯一 case_id")

    for spec in SPECS:
        metric = catalog.metrics.get(spec.metric_id)
        if metric is None or spec.dimension not in metric.available_dimensions:
            raise ValueError(
                f"{spec.case_id} 使用了指标字典不允许的维度："
                f"{spec.metric_id} x {spec.dimension}"
            )
        sql_text = (
            f"-- case_id: {spec.case_id}\n"
            f"-- metric_id: {spec.metric_id}\n"
            f"-- dimension: {spec.dimension}\n"
            + spec.sql.strip()
            + ";\n"
        )
        validate_named_parameter_contract(sql_text, spec.parameters)
        sql_relative_path = Path(
            f"data/evaluation/day14/references/sql/{spec.case_id}.sql"
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
        if execution.columns != spec.columns or not execution.rows:
            raise RuntimeError(f"{spec.case_id} returned an invalid grouped shape")

        result_payload = {
            "case_id": spec.case_id,
            "reference_type": "assistant_authored_sql_verified_on_real_sqlite",
            "business_reference_status": "not_independently_evaluated",
            "database_sha256": database_hash_before,
            "sql_sha256": sql_sha256,
            "parameters": spec.parameters,
            "columns": list(execution.columns),
            "rows": list(execution.rows),
            "row_count": len(execution.rows),
            "safety_gate_accepted": execution.safety_trace.accepted,
            "execution_started": execution.execution_started,
            "rows_truncated": execution.rows_truncated,
        }
        result_relative_path = Path(
            f"data/evaluation/day14/references/results/{spec.case_id}.json"
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
        )
        cases.append(case)
        report_rows.append(
            {
                "case_id": spec.case_id,
                "metric_id": spec.metric_id,
                "dimension": spec.dimension,
                "difficulty": spec.difficulty,
                "analysis_type": spec.analysis_type,
                "row_count": len(execution.rows),
                "sql_sha256": sql_sha256,
                "result_sha256": result_sha256,
                "safety_gate_accepted": execution.safety_trace.accepted,
                "execution_started": execution.execution_started,
                "business_reference_status": "not_independently_evaluated",
            }
        )

    retained = tuple(
        case
        for case in dataset.cases
        if case.category is not DatasetCategory.AGGREGATE_FILTER_JOIN
    )
    reason = "Added 20 assistant-authored, real-SQLite-verified aggregate/filter/join cases."
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
                "The first 40 cases (20 single-metric and 20 aggregate/filter/join) "
                "were mechanically authored by the assistant and verified on real "
                "SQLite. They have no user review or independent business reference yet."
            ),
            "change_history": history,
        }
    )
    updated = dataset.model_copy(
        update={
            "schema_version": "1.1.0",
            "dataset_version": "0.3.0",
            "provenance": provenance,
            "cases": retained + tuple(cases),
        }
    )
    updated = type(dataset).model_validate(updated.model_dump(mode="python"))
    draft_path.write_text(
        updated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    database_hash_after = _sha256_file(database)
    raw_after = _raw_hashes(root)
    report = {
        "measurement_scope": (
            "assistant_authored_fixed_aggregate_filter_join_cases; metric_dimension_"
            "compatibility_validated; standard_sql_passed_existing_safety_gate_and_"
            "executed_real_local_sqlite; no_candidate_model_run; no_independent_business_accuracy"
        ),
        "external_api_calls": 0,
        "model_generated_numeric_results": 0,
        "case_count": len(cases),
        "category": DatasetCategory.AGGREGATE_FILTER_JOIN.value,
        "difficulty_counts": {
            level: sum(case.difficulty.value == level for case in cases)
            for level in ("easy", "medium", "hard")
        },
        "metric_count": len({case.metric_ids[0] for case in cases}),
        "dimension_counts": {
            dimension: sum(case.dimensions == (dimension,) for case in cases)
            for dimension in sorted({case.dimensions[0] for case in cases})
        },
        "metric_dimension_compatibility_pass_count": len(cases),
        "named_parameter_contract_pass_count": len(cases),
        "stable_order_contract_count": len(cases),
        "safety_gate_pass_count": sum(row["safety_gate_accepted"] for row in report_rows),
        "real_sqlite_execution_count": sum(row["execution_started"] for row in report_rows),
        "user_reviewed_case_count": 0,
        "independent_business_reference_case_count": 0,
        "database_sha256_before": database_hash_before,
        "database_sha256_after": database_hash_after,
        "database_unchanged": database_hash_before == database_hash_after,
        "raw_file_hashes_before": raw_before,
        "raw_file_hashes_after": raw_after,
        "raw_files_unchanged": raw_before == raw_after,
        "cases": report_rows,
    }
    (root / "docs/DAY14_AGGREGATE_JOIN_RESULTS.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return tuple(cases)


def main() -> None:
    root = Path(__file__).parents[2]
    cases = build_aggregate_join_cases(root)
    print(f"Day 14 aggregate/filter/join cases: {len(cases)}/20")
    print("All metric-dimension pairs, named parameters, safety checks, and real SQLite executions passed.")
    print("External API calls: 0")


if __name__ == "__main__":
    main()
