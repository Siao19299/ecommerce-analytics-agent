-- case_id: D14_RU_010
SELECT COUNT(DISTINCT o.order_id) AS delivered_order_count
FROM fact_orders AS o
WHERE o.order_status = :order_status
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
