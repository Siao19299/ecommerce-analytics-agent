-- case_id: D14_SM_011
-- metric_id: terminal_cancellation_rate
SELECT ROUND(
    SUM(CASE WHEN o.order_status = 'canceled' THEN 1.0 ELSE 0 END)
    / NULLIF(COUNT(DISTINCT o.order_id), 0),
    6
) AS terminal_cancellation_rate
FROM fact_orders AS o
WHERE o.order_status IN ('delivered', 'canceled', 'unavailable')
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
