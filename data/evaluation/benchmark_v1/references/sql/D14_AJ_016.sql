-- case_id: D14_AJ_016
-- metric_id: delivered_payment_amount
-- dimension: purchase_month
SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       ROUND(SUM(p.payment_value), 2) AS delivered_payment_amount
FROM fact_orders AS o
JOIN fact_payments AS p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month ASC;
