# Day 6：补齐 15 题的标签审阅

R01–R07 来自已确认的五个核心指标和两个边界判断。用户明确回复“我不知道，你来审阅吧”后，R08–R15 已由助手按现有字典和 Schema 完成审阅，状态为 assistant_reviewed。总共 15 题，但不是 15 题用户独立人工标注；审阅来源逐题保存。

这些标签规定本次检索的必要目标，不宣称是生成完整 SQL 的所有依赖。both 问题同时需要指标文档与指定字段文档；指标文档附带了某表所有字段，并不算实际找到了该字段文档。

| 编号 | 问题要点 | 审阅后的期望召回 | 类型 |
|---|---|---|---|
| R08 | 不含运费的商品价格在哪 | fact_order_items.price | Schema |
| R09 | 跨订单识别真实客户 | dim_customers.customer_unique_id | Schema |
| R10 | 明细表复合主键 | fact_order_items.order_id + order_item_id | Schema |
| R11 | 支付表复合主键 | fact_payments.order_id + payment_sequential | Schema |
| R12 | 月度已送达 GMV 口径及时间、价格字段 | 已送达月度 GMV（或已送达 GMV）+ fact_orders.order_purchase_timestamp + fact_order_items.price | 两者 |
| R13 | 承运配送平均时长及起止字段 | 平均承运配送时长 + fact_orders.order_delivered_carrier_date + order_delivered_customer_date | 两者 |
| R14 | 已送达商品金额连同运费 | 已送达订单含运费成交额 | 指标 |
| R15 | 本月客户中本月之前就买过的比例 | 历史回购客户占比 | 指标 |

具体问题和带表名前缀的完整 ID 存在 tests/fixtures/day06/retrieval_cases.json。R10/R11 与 DDL 的复合主键一致；R09 区分真实客户标识和订单级客户记录标识；R12 的两种指标都允许下单月维度；R13 保留从交承运商到实际送达的起止字段。R15 是对之前讲解题的审阅，不作为额外独立答对的练习。评测报告保留 7 题 human_reviewed 与 8 题 assistant_reviewed 的计数。
