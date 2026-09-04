# Olist 原始数据字典

数据来源：Olist Brazilian E-Commerce Public Dataset。下述“预期粒度”和键关系来自
数据集语义，唯一性、缺失值和异常值仍需在数据质量检查中用代码验证。

## `olist_orders_dataset.csv`

预期粒度：每行一笔订单。`order_id` 为主键，`customer_id` 关联客户表。

| 字段 | 含义 |
|---|---|
| `order_id` | 订单标识 |
| `customer_id` | 本次订单对应的客户标识 |
| `order_status` | 订单状态 |
| `order_purchase_timestamp` | 下单时间 |
| `order_approved_at` | 订单批准时间 |
| `order_delivered_carrier_date` | 交付承运商时间 |
| `order_delivered_customer_date` | 送达客户时间 |
| `order_estimated_delivery_date` | 预计送达时间 |

## `olist_order_items_dataset.csv`

预期粒度：每行是一笔订单中的一个商品明细；逻辑键为
`order_id + order_item_id`。一笔订单可以对应多行明细。

| 字段 | 含义 |
|---|---|
| `order_id` | 订单标识，关联订单表 |
| `order_item_id` | 商品在订单内的序号 |
| `product_id` | 商品标识，关联商品表 |
| `seller_id` | 卖家标识，关联卖家表 |
| `shipping_limit_date` | 卖家发货时限 |
| `price` | 商品价格，不含运费 |
| `freight_value` | 该明细对应的运费 |

## `olist_customers_dataset.csv`

预期粒度：每行是一次订单使用的客户记录。`customer_id` 关联订单；
同一真实客户的多笔订单通过 `customer_unique_id` 识别。

| 字段 | 含义 |
|---|---|
| `customer_id` | 订单级客户标识 |
| `customer_unique_id` | 跨订单识别同一客户的匿名标识 |
| `customer_zip_code_prefix` | 客户邮编前缀 |
| `customer_city` | 客户城市 |
| `customer_state` | 客户州代码 |

## `olist_products_dataset.csv`

预期粒度：每行一个商品，`product_id` 为主键。原始数据中的 `lenght`
为字段原名拼写，导入时不得静默改写。

| 字段 | 含义 |
|---|---|
| `product_id` | 商品标识 |
| `product_category_name` | 葡萄牙语商品品类 |
| `product_name_lenght` | 商品名称长度 |
| `product_description_lenght` | 商品描述长度 |
| `product_photos_qty` | 商品图片数量 |
| `product_weight_g` | 商品重量（克） |
| `product_length_cm` | 商品长度（厘米） |
| `product_height_cm` | 商品高度（厘米） |
| `product_width_cm` | 商品宽度（厘米） |

## `olist_sellers_dataset.csv`

预期粒度：每行一个卖家，`seller_id` 为主键。

| 字段 | 含义 |
|---|---|
| `seller_id` | 卖家标识 |
| `seller_zip_code_prefix` | 卖家邮编前缀 |
| `seller_city` | 卖家城市 |
| `seller_state` | 卖家州代码 |

## `olist_order_payments_dataset.csv`

预期粒度：每行是一笔订单的一次支付记录；逻辑键为
`order_id + payment_sequential`。一笔订单可能有多条支付记录。

| 字段 | 含义 |
|---|---|
| `order_id` | 订单标识，关联订单表 |
| `payment_sequential` | 支付记录在订单内的序号 |
| `payment_type` | 支付方式 |
| `payment_installments` | 分期数 |
| `payment_value` | 本次支付金额 |

## `olist_order_reviews_dataset.csv`

预期粒度：每行一条订单评价记录。通过 `order_id` 关联订单；实际唯一性
和一单多评情况需在质量检查中确认。

| 字段 | 含义 |
|---|---|
| `review_id` | 评价标识 |
| `order_id` | 订单标识 |
| `review_score` | 评价分数 |
| `review_comment_title` | 评价标题 |
| `review_comment_message` | 评价正文 |
| `review_creation_date` | 评价创建日期 |
| `review_answer_timestamp` | 评价回复时间 |

## `olist_geolocation_dataset.csv`

预期粒度：每行一个地理编码观测。邮编前缀并不保证唯一，关联前需聚合或
去重，否则可能造成数据行膨胀。

| 字段 | 含义 |
|---|---|
| `geolocation_zip_code_prefix` | 邮编前缀 |
| `geolocation_lat` | 纬度 |
| `geolocation_lng` | 经度 |
| `geolocation_city` | 城市 |
| `geolocation_state` | 州代码 |

## `product_category_name_translation.csv`

预期粒度：每行一个品类翻译，`product_category_name` 关联商品表。

| 字段 | 含义 |
|---|---|
| `product_category_name` | 葡萄牙语品类名称 |
| `product_category_name_english` | 英文品类名称 |

## JOIN 风险提示

- 订单与订单明细是 `1 → N`，订单数应使用唯一订单标识计算。
- 订单与支付也可能是 `1 → N`。
- 同时直接连接明细和支付，会形成同一订单内的多对多组合并放大金额。
- 地理位置表的邮编前缀不唯一，不能未经聚合直接作为唯一维表连接。
- GMV、支付金额和含运费收入是不同口径，必须分别定义。
