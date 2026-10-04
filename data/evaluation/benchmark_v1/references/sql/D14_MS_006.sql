-- case_id: D14_MS_006
-- metric_id: canceled_order_count
SELECT date(o.order_purchase_timestamp, 'start of month') AS purchase_month,
       SUM(CASE WHEN o.order_status = 'canceled' THEN 1 ELSE 0 END) AS metric_value
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE c.customer_state = :customer_state
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY date(o.order_purchase_timestamp, 'start of month')
ORDER BY purchase_month ASC;
