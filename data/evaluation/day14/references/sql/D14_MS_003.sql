-- case_id: D14_MS_003
-- metric_id: delivered_category_gmv_contribution
SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
       SUM(i.price) AS metric_value
FROM fact_orders AS o
JOIN fact_order_items AS i ON i.order_id = o.order_id
LEFT JOIN dim_products AS p ON p.product_id = i.product_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
GROUP BY COALESCE(p.product_category_name, 'unknown')
ORDER BY product_category ASC;
