-- case_id: D14_AJ_004
-- metric_id: delivered_gmv
-- dimension: seller
SELECT i.seller_id AS seller,
       ROUND(SUM(i.price), 2) AS delivered_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY i.seller_id
ORDER BY delivered_gmv DESC, i.seller_id ASC
LIMIT 10;
