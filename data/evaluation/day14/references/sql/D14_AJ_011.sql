-- case_id: D14_AJ_011
-- metric_id: delivered_freight_to_gmv_rate
-- dimension: product_category
SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
       ROUND(
           SUM(i.freight_value) / NULLIF(SUM(i.price), 0),
           6
       ) AS delivered_freight_to_gmv_rate
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
LEFT JOIN dim_products AS p ON p.product_id = i.product_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY COALESCE(p.product_category_name, 'unknown')
ORDER BY delivered_freight_to_gmv_rate DESC, product_category ASC
LIMIT 10;
