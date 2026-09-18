-- case_id: D14_SM_019
-- metric_id: average_order_delivery_days
SELECT ROUND(
    AVG(julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp)),
    6
) AS average_order_delivery_days
FROM fact_orders AS o
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL
  AND o.order_delivered_customer_date >= o.order_purchase_timestamp
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
