-- case_id: D14_SM_017
-- metric_id: historical_returning_customer_rate
WITH current_customers AS (
    SELECT DISTINCT c.customer_unique_id
    FROM fact_orders AS o
    JOIN dim_customers AS c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
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
    ON h.customer_unique_id = current.customer_unique_id;
