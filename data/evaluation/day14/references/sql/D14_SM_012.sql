-- case_id: D14_SM_012
-- metric_id: delivered_item_count
SELECT COUNT(i.order_item_id) AS delivered_item_count
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
