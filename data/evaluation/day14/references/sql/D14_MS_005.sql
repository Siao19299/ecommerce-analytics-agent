-- case_id: D14_MS_005
-- metric_id: delivered_monthly_gmv
SELECT date(o.order_purchase_timestamp, 'start of month') AS purchase_month,
       SUM(i.price) AS metric_value
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND c.customer_state = :customer_state
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY date(o.order_purchase_timestamp, 'start of month')
ORDER BY purchase_month ASC;
