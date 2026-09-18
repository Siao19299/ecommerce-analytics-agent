-- case_id: D14_SM_010
-- metric_id: unavailable_order_count
SELECT COUNT(DISTINCT o.order_id) AS unavailable_order_count
FROM fact_orders AS o
WHERE o.order_status = 'unavailable'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
