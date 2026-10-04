-- Business metrics 核心指标标准 SQL

-- name: standard_delivered_order_count
SELECT COUNT(DISTINCT order_id) AS delivered_order_count
FROM fact_orders
WHERE order_status = 'delivered';


-- name: standard_delivered_gmv
SELECT ROUND(SUM(i.price), 2) AS delivered_gmv
FROM fact_orders AS o
JOIN fact_order_items AS i
    ON o.order_id = i.order_id
WHERE o.order_status = 'delivered';


-- name: standard_delivered_gmv_including_freight
SELECT ROUND(SUM(i.price + i.freight_value), 2)
    AS delivered_gmv_including_freight
FROM fact_orders AS o
JOIN fact_order_items AS i
    ON o.order_id = i.order_id
WHERE o.order_status = 'delivered';


-- name: standard_delivered_payment_amount
SELECT ROUND(SUM(p.payment_value), 2) AS delivered_payment_amount
FROM fact_orders AS o
JOIN fact_payments AS p
    ON o.order_id = p.order_id
WHERE o.order_status = 'delivered';


-- name: standard_delivered_customer_count
SELECT COUNT(DISTINCT c.customer_unique_id) AS delivered_customer_count
FROM fact_orders AS o
JOIN dim_customers AS c
    ON o.customer_id = c.customer_id
WHERE o.order_status = 'delivered';


-- name: standard_delivered_average_order_value
WITH order_gmv AS (
    SELECT order_id, SUM(price) AS gmv
    FROM fact_order_items
    GROUP BY order_id
)
SELECT ROUND(
    1.0 * SUM(COALESCE(g.gmv, 0)) / NULLIF(COUNT(o.order_id), 0),
    2
) AS delivered_average_order_value
FROM fact_orders AS o
LEFT JOIN order_gmv AS g
    ON o.order_id = g.order_id
WHERE o.order_status = 'delivered';


-- name: standard_delivered_freight_amount
SELECT ROUND(SUM(i.freight_value), 2) AS delivered_freight_amount
FROM fact_orders AS o
JOIN fact_order_items AS i
    ON o.order_id = i.order_id
WHERE o.order_status = 'delivered';


-- name: standard_terminal_cancellation_rate
SELECT ROUND(
    SUM(CASE WHEN order_status = 'canceled' THEN 1.0 ELSE 0 END)
    / NULLIF(COUNT(order_id), 0),
    6
) AS terminal_cancellation_rate
FROM fact_orders
WHERE order_status IN ('delivered', 'canceled', 'unavailable');


-- name: standard_on_time_delivery_rate
SELECT ROUND(
    SUM(
        CASE
            WHEN order_delivered_customer_date
                <= order_estimated_delivery_date
            THEN 1.0
            ELSE 0
        END
    ) / NULLIF(COUNT(order_id), 0),
    6
) AS on_time_delivery_rate
FROM fact_orders
WHERE order_status = 'delivered'
  AND order_delivered_customer_date IS NOT NULL
  AND order_estimated_delivery_date IS NOT NULL;


-- name: standard_average_order_delivery_days
SELECT ROUND(
    AVG(
        julianday(order_delivered_customer_date)
        - julianday(order_purchase_timestamp)
    ),
    6
) AS average_order_delivery_days
FROM fact_orders
WHERE order_status = 'delivered'
  AND order_delivered_customer_date IS NOT NULL
  AND order_delivered_customer_date >= order_purchase_timestamp;
