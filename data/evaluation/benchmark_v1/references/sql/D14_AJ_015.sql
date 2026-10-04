-- case_id: D14_AJ_015
-- metric_id: delivered_monthly_gmv
-- dimension: purchase_month
SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       ROUND(SUM(i.price), 2) AS delivered_monthly_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month ASC;
