-- case_id: D14_AJ_014
-- metric_id: delivered_payment_amount
-- dimension: customer_city
SELECT c.customer_city,
       ROUND(SUM(p.payment_value), 2) AS delivered_payment_amount
FROM fact_orders AS o
JOIN fact_payments AS p ON p.order_id = o.order_id
JOIN dim_customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY c.customer_city
ORDER BY delivered_payment_amount DESC, c.customer_city ASC
LIMIT 10;
