-- case_id: D14_AJ_020
-- metric_id: invalid_carrier_delivery_sequence_count
-- dimension: customer_state
SELECT c.customer_state,
       COUNT(DISTINCT o.order_id) AS invalid_carrier_delivery_sequence_count
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_carrier_date IS NOT NULL
  AND o.order_delivered_customer_date < o.order_delivered_carrier_date
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY c.customer_state
ORDER BY invalid_carrier_delivery_sequence_count DESC, c.customer_state ASC;
