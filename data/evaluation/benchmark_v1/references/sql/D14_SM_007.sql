-- case_id: D14_SM_007
-- metric_id: delivered_freight_amount
SELECT ROUND(SUM(i.freight_value), 2) AS delivered_freight_amount
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
