-- case_id: D14_SM_004
-- metric_id: delivered_payment_amount
SELECT ROUND(SUM(p.payment_value), 2) AS delivered_payment_amount
FROM fact_orders AS o
JOIN fact_payments AS p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
