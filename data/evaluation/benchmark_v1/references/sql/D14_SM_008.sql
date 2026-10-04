-- case_id: D14_SM_008
-- metric_id: terminal_order_count
SELECT COUNT(DISTINCT o.order_id) AS terminal_order_count
FROM fact_orders AS o
WHERE o.order_status IN ('delivered', 'canceled', 'unavailable')
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
