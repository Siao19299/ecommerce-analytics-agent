-- case_id: D14_AJ_008
-- metric_id: on_time_delivery_rate
-- dimension: customer_state
SELECT c.customer_state,
       ROUND(
           SUM(CASE WHEN o.order_delivered_customer_date <= o.order_estimated_delivery_date THEN 1.0 ELSE 0 END)
           / NULLIF(COUNT(DISTINCT o.order_id), 0),
           6
       ) AS on_time_delivery_rate
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_estimated_delivery_date IS NOT NULL
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY c.customer_state
HAVING COUNT(DISTINCT o.order_id) >= 100
ORDER BY on_time_delivery_rate DESC, c.customer_state ASC
LIMIT 10;
