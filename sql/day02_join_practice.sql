-- Day 2 脱离 AI SQL 练习

-- 练习 1：查询订单编号、订单状态和客户州代码。
-- 使用 fact_orders 和 dim_customers，通过 customer_id 关联。
SELECT fo.order_id, fo.order_status, dc.customer_state
FROM fact_orders fo
JOIN dim_customers dc
    ON fo.customer_id = dc.customer_id;


-- 练习 2：按商品品类汇总 GMV，并按 GMV 从高到低排序。
-- 使用 fact_order_items 和 dim_products，通过 product_id 关联。
-- GMV 口径：SUM(fact_order_items.price)，不包含运费。
SELECT dp.product_category_name, SUM(foi.price) gmv
FROM fact_order_items foi
JOIN dim_products dp
    ON foi.product_id = dp.product_id
GROUP BY dp.product_category_name
ORDER BY gmv DESC;