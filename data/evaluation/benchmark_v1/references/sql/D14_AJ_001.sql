-- case_id: D14_AJ_001
-- metric_id: delivered_order_count
-- dimension: purchase_month
SELECT substr(o.order_purchase_timestamp, 1, 7) || '-01' AS purchase_month,
       COUNT(DISTINCT o.order_id) AS delivered_order_count
FROM fact_orders AS o
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY substr(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month ASC;
