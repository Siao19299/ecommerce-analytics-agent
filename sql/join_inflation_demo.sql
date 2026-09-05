-- 错误示例：明细和支付都是订单的一对多子表。
-- 同时直接连接会形成 item_count * payment_count 行。
SELECT
    COUNT(o.order_id) AS joined_order_rows,
    SUM(i.price) AS inflated_gmv,
    SUM(p.payment_value) AS inflated_payment_amount
FROM fact_orders AS o
JOIN fact_order_items AS i
    ON o.order_id = i.order_id
JOIN fact_payments AS p
    ON o.order_id = p.order_id
WHERE o.order_id = 'o001';

-- 正确示例：先在各自事实表中聚合到订单粒度，再连接。
WITH item_totals AS (
    SELECT
        order_id,
        SUM(price) AS gmv
    FROM fact_order_items
    GROUP BY order_id
),
payment_totals AS (
    SELECT
        order_id,
        SUM(payment_value) AS payment_amount
    FROM fact_payments
    GROUP BY order_id
)
SELECT
    COUNT(o.order_id) AS order_count,
    i.gmv,
    p.payment_amount
FROM fact_orders AS o
LEFT JOIN item_totals AS i
    ON o.order_id = i.order_id
LEFT JOIN payment_totals AS p
    ON o.order_id = p.order_id
WHERE o.order_id = 'o001'
GROUP BY o.order_id, i.gmv, p.payment_amount;
