-- case_id: D14_AJ_019
-- metric_id: delivered_payment_reconciliation_difference
-- dimension: purchase_month
WITH item_totals AS (
    SELECT order_id,
           SUM(price) AS order_gmv,
           SUM(freight_value) AS order_freight
    FROM fact_order_items
    GROUP BY order_id
),
payment_totals AS (
    SELECT order_id,
           SUM(payment_value) AS order_payment
    FROM fact_payments
    GROUP BY order_id
)
SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       ROUND(SUM(COALESCE(i.order_gmv, 0)), 2) AS delivered_gmv,
       ROUND(SUM(COALESCE(i.order_freight, 0)), 2) AS delivered_freight_amount,
       ROUND(SUM(COALESCE(p.order_payment, 0)), 2) AS delivered_payment_amount,
       ROUND(
           SUM(COALESCE(p.order_payment, 0))
           - SUM(COALESCE(i.order_gmv, 0))
           - SUM(COALESCE(i.order_freight, 0)),
           2
       ) AS delivered_payment_reconciliation_difference
FROM fact_orders AS o
LEFT JOIN item_totals AS i ON i.order_id = o.order_id
LEFT JOIN payment_totals AS p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month ASC;
