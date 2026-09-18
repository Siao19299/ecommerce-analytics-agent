-- case_id: D14_AJ_009
-- metric_id: average_order_delivery_days
-- dimension: customer_state
SELECT c.customer_state,
       ROUND(
           AVG(julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp)),
           6
       ) AS average_order_delivery_days
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_customer_date >= o.order_purchase_timestamp
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY c.customer_state
ORDER BY average_order_delivery_days DESC, c.customer_state ASC
LIMIT 5;
