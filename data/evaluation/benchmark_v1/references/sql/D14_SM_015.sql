-- case_id: D14_SM_015
-- metric_id: period_repeat_customer_count
WITH customer_orders AS (
    SELECT c.customer_unique_id,
           COUNT(DISTINCT o.order_id) AS delivered_order_count
    FROM fact_orders AS o
    JOIN dim_customers AS c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
    GROUP BY c.customer_unique_id
)
SELECT COUNT(*) AS period_repeat_customer_count
FROM customer_orders
WHERE delivered_order_count >= 2;
