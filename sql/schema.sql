CREATE TABLE IF NOT EXISTS dim_customers (
    customer_id TEXT PRIMARY KEY,
    customer_unique_id TEXT NOT NULL,
    customer_zip_code_prefix TEXT NOT NULL,
    customer_city TEXT NOT NULL,
    customer_state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    order_status TEXT NOT NULL,
    order_purchase_timestamp TIMESTAMP NOT NULL,
    order_approved_at TIMESTAMP,
    order_delivered_carrier_date TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP NOT NULL,
    FOREIGN KEY (customer_id)
        REFERENCES dim_customers (customer_id)
);

CREATE TABLE IF NOT EXISTS dim_products (
    product_id TEXT PRIMARY KEY,
    product_category_name TEXT,
    product_name_lenght INTEGER,
    product_description_lenght INTEGER,
    product_photos_qty INTEGER,
    product_weight_g INTEGER,
    product_length_cm INTEGER,
    product_height_cm INTEGER,
    product_width_cm INTEGER,
    CHECK (product_name_lenght IS NULL OR product_name_lenght >= 0),
    CHECK (
        product_description_lenght IS NULL
        OR product_description_lenght >= 0
    ),
    CHECK (product_photos_qty IS NULL OR product_photos_qty >= 0),
    CHECK (product_weight_g IS NULL OR product_weight_g >= 0),
    CHECK (product_length_cm IS NULL OR product_length_cm >= 0),
    CHECK (product_height_cm IS NULL OR product_height_cm >= 0),
    CHECK (product_width_cm IS NULL OR product_width_cm >= 0)
);

CREATE TABLE IF NOT EXISTS dim_sellers (
    seller_id TEXT PRIMARY KEY,
    seller_zip_code_prefix TEXT NOT NULL,
    seller_city TEXT NOT NULL,
    seller_state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_order_items (
    order_id TEXT NOT NULL,
    order_item_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    seller_id TEXT NOT NULL,
    shipping_limit_date TIMESTAMP NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    freight_value NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (order_id, order_item_id),
    FOREIGN KEY (order_id)
        REFERENCES fact_orders (order_id),
    FOREIGN KEY (product_id)
        REFERENCES dim_products (product_id),
    FOREIGN KEY (seller_id)
        REFERENCES dim_sellers (seller_id),
    CHECK (order_item_id >= 1),
    CHECK (price >= 0),
    CHECK (freight_value >= 0)
);

CREATE TABLE IF NOT EXISTS fact_payments (
    order_id TEXT NOT NULL,
    payment_sequential INTEGER NOT NULL,
    payment_type TEXT NOT NULL,
    payment_installments INTEGER NOT NULL,
    payment_value NUMERIC(12, 2) NOT NULL,
    PRIMARY KEY (order_id, payment_sequential),
    FOREIGN KEY (order_id)
        REFERENCES fact_orders (order_id),
    CHECK (payment_sequential >= 1),
    CHECK (payment_installments >= 0),
    CHECK (payment_value >= 0)
);
