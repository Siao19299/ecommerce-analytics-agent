-- case_id: D14_AJ_006
-- metric_id: delivered_customer_count
-- dimension: customer_state
SELECT c.customer_state,
       COUNT(DISTINCT c.customer_unique_id) AS delivered_customer_count
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY c.customer_state
ORDER BY delivered_customer_count DESC, c.customer_state ASC;
