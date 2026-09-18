-- case_id: D14_AJ_012
-- metric_id: average_items_per_delivered_order
-- dimension: customer_state
SELECT c.customer_state,
       ROUND(
           1.0 * COUNT(i.order_item_id) / NULLIF(COUNT(DISTINCT o.order_id), 0),
           6
       ) AS average_items_per_delivered_order
FROM fact_orders AS o
JOIN dim_customers AS c ON c.customer_id = o.customer_id
LEFT JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY c.customer_state
ORDER BY average_items_per_delivered_order DESC, c.customer_state ASC;
