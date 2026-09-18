-- case_id: D14_AJ_018
-- metric_id: delivered_gmv_including_freight
-- dimension: product_category
SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
       ROUND(SUM(i.price + i.freight_value), 2) AS delivered_gmv_including_freight
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
LEFT JOIN dim_products AS p ON p.product_id = i.product_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY COALESCE(p.product_category_name, 'unknown')
ORDER BY delivered_gmv_including_freight DESC, product_category ASC
LIMIT 10;
