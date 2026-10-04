-- case_id: D14_AJ_003
-- metric_id: delivered_monthly_category_gmv_rank
-- dimension: product_category
WITH category_gmv AS (
    SELECT COALESCE(p.product_category_name, 'unknown') AS product_category,
           SUM(i.price) AS category_gmv
    FROM fact_orders AS o
    JOIN fact_order_items AS i ON i.order_id = o.order_id
    LEFT JOIN dim_products AS p ON p.product_id = i.product_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive
    GROUP BY COALESCE(p.product_category_name, 'unknown')
),
ranked AS (
    SELECT product_category,
           category_gmv,
           DENSE_RANK() OVER (ORDER BY category_gmv DESC) AS category_rank
    FROM category_gmv
)
SELECT product_category,
       ROUND(category_gmv, 2) AS category_gmv,
       category_rank
FROM ranked
WHERE category_rank <= 3
ORDER BY category_rank ASC, product_category ASC;
