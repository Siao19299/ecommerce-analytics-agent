-- case_id: D14_SM_013
-- metric_id: average_items_per_delivered_order
SELECT ROUND(
    1.0 * COUNT(i.order_item_id) / NULLIF(COUNT(DISTINCT o.order_id), 0),
    6
) AS average_items_per_delivered_order
FROM fact_orders AS o
LEFT JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
