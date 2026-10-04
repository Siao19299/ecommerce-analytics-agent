-- case_id: D14_SM_005
-- metric_id: delivered_average_order_value
WITH order_gmv AS (
    SELECT order_id, SUM(price) AS gmv
    FROM fact_order_items
    GROUP BY order_id
)
SELECT ROUND(
    1.0 * SUM(COALESCE(g.gmv, 0)) / NULLIF(COUNT(DISTINCT o.order_id), 0),
    2
) AS delivered_average_order_value
FROM fact_orders AS o
LEFT JOIN order_gmv AS g ON g.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= :start_date
  AND o.order_purchase_timestamp < :end_date_exclusive;
