-- Data pipeline 基础经营 SQL

-- name: delivered_order_count
-- 指标口径：订单量按唯一 order_id 计算，仅包含已送达订单。
SELECT COUNT(DISTINCT o.order_id) AS delivered_order_count
FROM fact_orders AS o
WHERE o.order_status = 'delivered';


-- name: delivered_gmv
-- 指标口径：GMV 为已送达订单明细的 price 之和，不包含运费。
SELECT ROUND(SUM(i.price), 2) AS delivered_gmv
FROM fact_order_items AS i
JOIN fact_orders AS o
    ON i.order_id = o.order_id
WHERE o.order_status = 'delivered';


-- name: delivered_average_order_value
-- 指标口径：客单价 = 已送达订单 GMV / 已送达唯一订单数。
-- 先将明细聚合到订单粒度，避免一笔多明细订单扩大订单数。
WITH delivered_orders AS (
    SELECT order_id
    FROM fact_orders
    WHERE order_status = 'delivered'
),
order_gmv AS (
    SELECT order_id, SUM(price) AS gmv
    FROM fact_order_items
    GROUP BY order_id
)
SELECT ROUND(
    SUM(COALESCE(order_gmv.gmv, 0)) / COUNT(delivered_orders.order_id),
    2
) AS delivered_average_order_value
FROM delivered_orders
LEFT JOIN order_gmv
    ON delivered_orders.order_id = order_gmv.order_id;


-- name: delivered_customer_count
-- 指标口径：客户数按 customer_unique_id 去重，仅包含已送达订单。
-- customer_id 是订单级客户记录，不用于识别跨订单的同一真实客户。
SELECT COUNT(DISTINCT c.customer_unique_id) AS delivered_customer_count
FROM fact_orders AS o
JOIN dim_customers AS c
    ON o.customer_id = c.customer_id
WHERE o.order_status = 'delivered';


-- name: delivered_category_gmv
-- 指标口径：品类 GMV 为已送达订单明细 price 之和，不包含运费。
-- 商品品类缺失时保留为 unknown，不静默丢弃对应 GMV。
SELECT
    CASE
        WHEN p.product_category_name IS NULL THEN 'unknown'
        ELSE p.product_category_name
    END AS product_category_name,
    ROUND(SUM(i.price), 2) AS delivered_gmv
FROM fact_order_items AS i
JOIN fact_orders AS o
    ON i.order_id = o.order_id
LEFT JOIN dim_products AS p
    ON i.product_id = p.product_id
WHERE o.order_status = 'delivered'
GROUP BY
    CASE
        WHEN p.product_category_name IS NULL THEN 'unknown'
        ELSE p.product_category_name
    END
HAVING SUM(i.price) > 0
ORDER BY delivered_gmv DESC;
