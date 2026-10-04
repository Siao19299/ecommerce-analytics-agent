-- Business metrics 进阶业务 SQL：CTE、窗口函数、同比、环比、Top-N 和贡献度

-- name: monthly_gmv_mom
WITH data_bounds AS (
    SELECT
        date(MIN(order_purchase_timestamp), 'start of month')
            AS first_month,
        date(MAX(order_purchase_timestamp), 'start of month')
            AS last_month
    FROM fact_orders
),
monthly_gmv AS (
    SELECT
        date(o.order_purchase_timestamp, 'start of month')
            AS purchase_month,
        SUM(i.price) AS monthly_gmv
    FROM fact_orders AS o
    JOIN fact_order_items AS i
        ON o.order_id = i.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY date(o.order_purchase_timestamp, 'start of month')
),
monthly_with_flags AS (
    SELECT
        m.purchase_month,
        m.monthly_gmv,
        CASE
            WHEN m.purchase_month IN (b.first_month, b.last_month) THEN 1
            ELSE 0
        END AS is_dataset_boundary_month
    FROM monthly_gmv AS m
    CROSS JOIN data_bounds AS b
),
monthly_with_previous AS (
    SELECT
        purchase_month,
        monthly_gmv,
        is_dataset_boundary_month,
        LAG(purchase_month) OVER (ORDER BY purchase_month)
            AS previous_month,
        LAG(monthly_gmv) OVER (ORDER BY purchase_month)
            AS previous_month_gmv,
        LAG(is_dataset_boundary_month) OVER (ORDER BY purchase_month)
            AS is_previous_month_dataset_boundary
    FROM monthly_with_flags
)
SELECT
    purchase_month,
    ROUND(monthly_gmv, 2) AS monthly_gmv,
    previous_month,
    ROUND(previous_month_gmv, 2) AS previous_month_gmv,
    is_dataset_boundary_month,
    is_previous_month_dataset_boundary,
    CASE
        WHEN previous_month = date(purchase_month, '-1 month')
             AND previous_month_gmv <> 0
        THEN ROUND(1.0 * monthly_gmv / previous_month_gmv - 1, 6)
        ELSE NULL
    END AS mom_rate,
    CASE
        WHEN previous_month = date(purchase_month, '-1 month')
             AND is_dataset_boundary_month = 0
             AND is_previous_month_dataset_boundary = 0
        THEN 1
        ELSE 0
    END AS passes_boundary_comparability_check
FROM monthly_with_previous
ORDER BY purchase_month;


-- name: monthly_gmv_yoy
WITH data_bounds AS (
    SELECT
        date(MIN(order_purchase_timestamp), 'start of month')
            AS first_month,
        date(MAX(order_purchase_timestamp), 'start of month')
            AS last_month
    FROM fact_orders
),
monthly_gmv AS (
    SELECT
        date(o.order_purchase_timestamp, 'start of month')
            AS purchase_month,
        SUM(i.price) AS monthly_gmv
    FROM fact_orders AS o
    JOIN fact_order_items AS i
        ON o.order_id = i.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY date(o.order_purchase_timestamp, 'start of month')
),
monthly_with_flags AS (
    SELECT
        m.purchase_month,
        m.monthly_gmv,
        CASE
            WHEN m.purchase_month IN (b.first_month, b.last_month) THEN 1
            ELSE 0
        END AS is_dataset_boundary_month
    FROM monthly_gmv AS m
    CROSS JOIN data_bounds AS b
)
SELECT
    current.purchase_month,
    ROUND(current.monthly_gmv, 2) AS monthly_gmv,
    previous.purchase_month AS previous_year_month,
    ROUND(previous.monthly_gmv, 2) AS previous_year_month_gmv,
    current.is_dataset_boundary_month,
    previous.is_dataset_boundary_month
        AS is_previous_year_month_dataset_boundary,
    CASE
        WHEN previous.monthly_gmv <> 0
        THEN ROUND(
            1.0 * current.monthly_gmv / previous.monthly_gmv - 1,
            6
        )
        ELSE NULL
    END AS yoy_rate,
    CASE
        WHEN previous.purchase_month IS NOT NULL
             AND current.is_dataset_boundary_month = 0
             AND previous.is_dataset_boundary_month = 0
        THEN 1
        ELSE 0
    END AS passes_boundary_comparability_check
FROM monthly_with_flags AS current
LEFT JOIN monthly_with_flags AS previous
    ON previous.purchase_month = date(current.purchase_month, '-1 year')
ORDER BY current.purchase_month;


-- name: monthly_category_top3
WITH monthly_category_gmv AS (
    SELECT
        date(o.order_purchase_timestamp, 'start of month')
            AS purchase_month,
        COALESCE(p.product_category_name, 'unknown')
            AS product_category_name,
        SUM(i.price) AS category_gmv
    FROM fact_orders AS o
    JOIN fact_order_items AS i
        ON o.order_id = i.order_id
    LEFT JOIN dim_products AS p
        ON i.product_id = p.product_id
    WHERE o.order_status = 'delivered'
    GROUP BY
        date(o.order_purchase_timestamp, 'start of month'),
        COALESCE(p.product_category_name, 'unknown')
),
ranked_categories AS (
    SELECT
        purchase_month,
        product_category_name,
        category_gmv,
        DENSE_RANK() OVER (
            PARTITION BY purchase_month
            ORDER BY category_gmv DESC
        ) AS category_rank
    FROM monthly_category_gmv
)
SELECT
    purchase_month,
    product_category_name,
    ROUND(category_gmv, 2) AS category_gmv,
    category_rank
FROM ranked_categories
WHERE category_rank <= 3
ORDER BY purchase_month, category_rank, product_category_name;


-- name: monthly_category_gmv_contribution
WITH monthly_category_gmv AS (
    SELECT
        date(o.order_purchase_timestamp, 'start of month')
            AS purchase_month,
        COALESCE(p.product_category_name, 'unknown')
            AS product_category_name,
        SUM(i.price) AS category_gmv
    FROM fact_orders AS o
    JOIN fact_order_items AS i
        ON o.order_id = i.order_id
    LEFT JOIN dim_products AS p
        ON i.product_id = p.product_id
    WHERE o.order_status = 'delivered'
    GROUP BY
        date(o.order_purchase_timestamp, 'start of month'),
        COALESCE(p.product_category_name, 'unknown')
)
SELECT
    purchase_month,
    product_category_name,
    ROUND(category_gmv, 2) AS category_gmv,
    ROUND(
        1.0 * category_gmv
        / NULLIF(
            SUM(category_gmv) OVER (PARTITION BY purchase_month),
            0
        ),
        6
    ) AS category_gmv_contribution
FROM monthly_category_gmv
ORDER BY purchase_month, category_gmv DESC, product_category_name;


-- name: monthly_gmv_payment_reconciliation
WITH order_item_totals AS (
    SELECT
        order_id,
        SUM(price) AS order_gmv,
        SUM(freight_value) AS order_freight_amount
    FROM fact_order_items
    GROUP BY order_id
),
order_payment_totals AS (
    SELECT
        order_id,
        SUM(payment_value) AS order_payment_amount
    FROM fact_payments
    GROUP BY order_id
)
SELECT
    date(o.order_purchase_timestamp, 'start of month')
        AS purchase_month,
    ROUND(SUM(COALESCE(i.order_gmv, 0)), 2) AS monthly_gmv,
    ROUND(SUM(COALESCE(i.order_freight_amount, 0)), 2)
        AS monthly_freight_amount,
    ROUND(SUM(COALESCE(p.order_payment_amount, 0)), 2)
        AS monthly_payment_amount,
    ROUND(
        SUM(COALESCE(i.order_gmv, 0))
        + SUM(COALESCE(i.order_freight_amount, 0)),
        2
    ) AS monthly_gmv_including_freight,
    ROUND(
        SUM(COALESCE(p.order_payment_amount, 0))
        - SUM(COALESCE(i.order_gmv, 0)),
        2
    ) AS payment_minus_gmv,
    ROUND(
        SUM(COALESCE(p.order_payment_amount, 0))
        - SUM(COALESCE(i.order_gmv, 0))
        - SUM(COALESCE(i.order_freight_amount, 0)),
        2
    ) AS payment_reconciliation_difference
FROM fact_orders AS o
LEFT JOIN order_item_totals AS i
    ON o.order_id = i.order_id
LEFT JOIN order_payment_totals AS p
    ON o.order_id = p.order_id
WHERE o.order_status = 'delivered'
GROUP BY date(o.order_purchase_timestamp, 'start of month')
ORDER BY purchase_month;
