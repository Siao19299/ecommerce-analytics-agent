-- case_id: D14_SM_020
-- metric_id: invalid_carrier_delivery_sequence_count
SELECT COUNT(DISTINCT o.order_id) AS invalid_carrier_delivery_sequence_count
FROM fact_orders AS o
WHERE o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_carrier_date IS NOT NULL
  AND o.order_delivered_customer_date < o.order_delivered_carrier_date
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
