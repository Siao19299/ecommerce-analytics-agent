-- case_id: D14_MS_010
-- metric_id: canceled_order_count
WITH months AS (
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
ORDER BY m.purchase_month ASC;
