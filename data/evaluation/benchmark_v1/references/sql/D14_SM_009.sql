-- case_id: D14_SM_009
-- metric_id: canceled_order_count
SELECT COUNT(DISTINCT o.order_id) AS canceled_order_count
FROM fact_orders AS o
WHERE o.order_status = 'canceled'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
