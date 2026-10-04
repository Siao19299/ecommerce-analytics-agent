-- case_id: D14_AJ_002
-- metric_id: delivered_gmv
-- dimension: customer_state
SELECT c.customer_state,
       ROUND(SUM(i.price), 2) AS delivered_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY c.customer_state
ORDER BY delivered_gmv DESC, c.customer_state ASC
LIMIT 5;
