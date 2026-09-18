-- case_id: D14_AJ_007
-- metric_id: terminal_cancellation_rate
-- dimension: customer_state
SELECT c.customer_state,
       ROUND(
           SUM(CASE WHEN o.order_status = 'canceled' THEN 1.0 ELSE 0 END)
           / NULLIF(COUNT(DISTINCT o.order_id), 0),
           6
       ) AS terminal_cancellation_rate
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status IN ('delivered', 'canceled', 'unavailable')
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY c.customer_state
ORDER BY terminal_cancellation_rate DESC, c.customer_state ASC;
