-- case_id: D14_SM_018
-- metric_id: on_time_delivery_rate
SELECT ROUND(
    SUM(CASE WHEN o.order_delivered_customer_date <= o.order_estimated_delivery_date THEN 1.0 ELSE 0 END)
    / NULLIF(COUNT(DISTINCT o.order_id), 0),
    6
) AS on_time_delivery_rate
FROM fact_orders AS o
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_estimated_delivery_date IS NOT NULL
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
