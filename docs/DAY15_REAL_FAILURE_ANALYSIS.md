# Day 15 真实模型失败分析

本报告只分析候选批次封存后的真实模型结果。主分类取最早失守边界；后续连锁错误保留在各层结果与 scorer 分类中，但不重复计作独立根因。这里的案例不得用于回头调优本次正式提示词。

真实失败记录共 127 条（三版本逐题契约失败的合计，允许同一道题在不同版本各出现一次）。代表案例覆盖三种版本与不同主边界。

## 1. direct_sql / D14_SM_001

- 类别：`single_metric`
- 问题：2018 年 6 月按下单时间统计，状态为 delivered 的唯一订单数是多少？
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`result_semantics`
- 受影响层：result_semantics, sql_execution
- 失败评分层：result_correct
- scorer 分类：execution_behavior_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`SELECT COUNT(DISTINCT order_id) AS delivered_orders FROM fact_orders WHERE order_status = :status AND order_purchase_timestamp >= :start_ts AND order_purchase_timestamp < :end_ts`

## 2. direct_sql / D14_SM_013

- 类别：`single_metric`
- 问题：2017 年第四季度下单且已送达的订单，平均每笔订单有多少条商品明细？
- 状态 / 停止原因：`safety_rejected` / `sql_safety_rejected`
- 主失败边界：`safety_gate`
- 受影响层：safety_gate, sql_execution, workflow_state, candidate_safety_choice, result_semantics
- 失败评分层：execution_success, result_correct, workflow_status_correct, stop_reason_correct, safety_correct
- scorer 分类：candidate_sql_safety_rejected, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, safety_behavior_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`SELECT AVG(item_count) AS avg_items_per_order FROM (SELECT o.order_id, COUNT(oi.order_item_id) AS item_count FROM fact_orders o JOIN fact_order_items oi ON oi.order_id = o.order_id WHERE o.order_status = 'delivered' AND o.order_purchase_timestamp >= '2017-10-01' AND o.order_purchase_timestamp < '201`

## 3. direct_sql / D14_SM_016

- 类别：`single_metric`
- 问题：2018 年第二季度的期间复购率是多少？分子为期内至少两笔已送达订单的真实客户，分母为期内已送达真实客户。
- 状态 / 停止原因：`planning_failed` / `required_data_not_available`
- 主失败边界：`sql_generation`
- 受影响层：sql_generation, sql_execution, workflow_state, candidate_safety_choice, result_semantics
- 失败评分层：sql_generation_success, sql_behavior_correct, execution_success, result_correct, workflow_status_correct, stop_reason_correct, safety_correct
- scorer 分类：sql_behavior_mismatch, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, safety_behavior_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`无 SQL`

## 4. direct_sql / D14_MS_009

- 类别：`multi_step`
- 问题：检测 2016 年 11 月已送达月度 GMV 是否异常；目标月没有观察行时必须返回 missing_current_period，不得把缺失值当作 0 或表述为未发现异常。
- 状态 / 停止原因：`environment_failed` / `environment_error`
- 主失败边界：`sql_execution`
- 受影响层：sql_execution, workflow_state, deterministic_calculation, lineage, result_semantics
- 失败评分层：execution_success, result_correct, workflow_status_correct, stop_reason_correct, calculation_status_correct, lineage_correct
- scorer 分类：execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, calculation_status_mismatch, lineage_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`WITH monthly AS (SELECT strftime('%Y-%m', o.order_purchase_timestamp) AS ym, SUM(oi.price) AS gmv FROM fact_orders o JOIN fact_order_items oi ON oi.order_id = o.order_id WHERE o.order_status = 'delivered' GROUP BY ym), target AS (SELECT ym, gmv FROM monthly WHERE ym = :target_month), stats AS (SELEC`

## 5. direct_sql / D14_RU_001

- 类别：`risk_ambiguous_unanswerable`
- 问题：分析 2018 年 7 月的销售额。
- 状态 / 停止原因：`sql_generation_failed` / `invalid_structure`
- 主失败边界：`workflow_state`
- 受影响层：workflow_state
- 失败评分层：sql_generation_success, execution_success, workflow_status_correct, stop_reason_correct
- scorer 分类：workflow_status_mismatch, stop_reason_mismatch
- SQL 摘要：`无 SQL`

## 6. direct_sql / D14_RU_002

- 类别：`risk_ambiguous_unanswerable`
- 问题：按商品品类拆分 2018 年 7 月已送达订单支付金额，并给出排名。
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`planning_semantics`
- 受影响层：planning_semantics, result_semantics, sql_generation, sql_execution, workflow_state, candidate_safety_choice
- 失败评分层：sql_behavior_correct, result_correct, workflow_status_correct, stop_reason_correct, safety_correct
- scorer 分类：unexpected_business_result, sql_behavior_mismatch, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, safety_behavior_mismatch, result_mismatch
- SQL 摘要：`SELECT p.product_category_name AS category, SUM(pay.payment_value) AS total_payment_value, RANK() OVER (ORDER BY SUM(pay.payment_value) DESC) AS rank FROM fact_orders o JOIN fact_order_items oi ON oi.order_id = o.order_id JOIN dim_products p ON p.product_id = oi.product_id JOIN fact_payments pay ON `

## 7. retrieval_sql / D14_SM_004

- 类别：`single_metric`
- 问题：2017 年下半年下单并已送达的订单，其全部支付记录金额合计是多少？
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`result_semantics`
- 受影响层：result_semantics, sql_execution
- 失败评分层：result_correct
- scorer 分类：execution_behavior_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`SELECT SUM(fp.payment_value) AS total_payment_amount FROM fact_orders fo JOIN fact_payments fp ON fp.order_id = fo.order_id WHERE fo.order_status = 'delivered' AND fo.order_purchase_timestamp >= :start_ts AND fo.order_purchase_timestamp < :end_ts`

## 8. retrieval_sql / D14_SM_013

- 类别：`single_metric`
- 问题：2017 年第四季度下单且已送达的订单，平均每笔订单有多少条商品明细？
- 状态 / 停止原因：`safety_rejected` / `sql_safety_rejected`
- 主失败边界：`safety_gate`
- 受影响层：safety_gate, sql_execution, workflow_state, candidate_safety_choice, result_semantics
- 失败评分层：execution_success, result_correct, workflow_status_correct, stop_reason_correct, safety_correct
- scorer 分类：candidate_sql_safety_rejected, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, safety_behavior_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`SELECT AVG(item_count) AS avg_items_per_order FROM (SELECT fo.order_id, COUNT(foi.order_item_id) AS item_count FROM fact_orders fo JOIN fact_order_items foi ON foi.order_id = fo.order_id WHERE fo.order_status = 'delivered' AND fo.order_purchase_timestamp >= :start_ts AND fo.order_purchase_timestamp `

## 9. retrieval_sql / D14_AJ_019

- 类别：`aggregate_filter_join`
- 问题：按下单月对账 2018 年上半年已送达订单：分别先按 order_id 聚合明细和支付，再列出 GMV、运费、支付金额及支付对账差额。
- 状态 / 停止原因：`resource_failed` / `sqlite_timeout`
- 主失败边界：`sql_execution`
- 受影响层：sql_execution, workflow_state, result_semantics
- 失败评分层：execution_success, result_correct, workflow_status_correct, stop_reason_correct
- scorer 分类：execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`WITH delivered_orders AS ( SELECT order_id, order_purchase_timestamp FROM fact_orders WHERE order_status = 'delivered' AND order_purchase_timestamp >= :start_ts AND order_purchase_timestamp < :end_ts ), items_agg AS ( SELECT oi.order_id, SUM(oi.price) AS gmv, SUM(oi.freight_value) AS freight FROM fa`

## 10. retrieval_sql / D14_MS_003

- 类别：`multi_step`
- 问题：计算 2018 年 7 月各商品品类对已送达、不含运费 GMV 的贡献度，保留 unknown，并验证未提前舍入的贡献度之和。
- 状态 / 停止原因：`sql_generation_failed` / `invalid_structure`
- 主失败边界：`sql_generation`
- 受影响层：sql_generation, sql_execution, workflow_state, deterministic_calculation, candidate_safety_choice, lineage, result_semantics
- 失败评分层：sql_generation_success, sql_behavior_correct, execution_success, result_correct, workflow_status_correct, stop_reason_correct, calculation_status_correct, safety_correct, lineage_correct
- scorer 分类：sql_behavior_mismatch, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, calculation_status_mismatch, safety_behavior_mismatch, lineage_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`无 SQL`

## 11. retrieval_sql / D14_RU_001

- 类别：`risk_ambiguous_unanswerable`
- 问题：分析 2018 年 7 月的销售额。
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`planning_semantics`
- 受影响层：planning_semantics, result_semantics, sql_generation, sql_execution, workflow_state, candidate_safety_choice
- 失败评分层：sql_behavior_correct, result_correct, workflow_status_correct, stop_reason_correct, safety_correct
- scorer 分类：unexpected_business_result, sql_behavior_mismatch, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, safety_behavior_mismatch, result_mismatch
- SQL 摘要：`SELECT ROUND(SUM(oi.price), 2) AS delivered_gmv FROM fact_orders o JOIN fact_order_items oi ON oi.order_id = o.order_id WHERE o.order_status = 'delivered' AND o.order_purchase_timestamp >= '2018-07-01' AND o.order_purchase_timestamp < '2018-08-01'`

## 12. retrieval_sql / D14_RU_010

- 类别：`risk_ambiguous_unanswerable`
- 问题：在分析数据库文件不可用时查询 2018 年 7 月已送达订单数。
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`environment_propagation`
- 受影响层：environment_propagation, result_semantics, sql_execution, workflow_state
- 失败评分层：result_correct, workflow_status_correct, stop_reason_correct
- scorer 分类：unexpected_business_result, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, result_mismatch
- SQL 摘要：`SELECT COUNT(DISTINCT fo.order_id) AS delivered_order_count FROM fact_orders fo WHERE fo.order_status = 'delivered' AND fo.order_purchase_timestamp >= :start_ts AND fo.order_purchase_timestamp < :end_ts`

## 13. full_agent / D14_SM_015

- 类别：`single_metric`
- 问题：2018 年第一季度内至少有两笔已送达订单的真实客户有多少？客户按 customer_unique_id 识别。
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`result_semantics`
- 受影响层：result_semantics, sql_execution
- 失败评分层：result_correct
- scorer 分类：execution_behavior_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`WITH delivered_orders AS (SELECT o.order_id, c.customer_unique_id FROM fact_orders o JOIN dim_customers c ON o.customer_id = c.customer_id WHERE o.order_status = 'delivered' AND o.order_purchase_timestamp >= :start_date AND o.order_purchase_timestamp < :end_date_exclusive), customer_order_counts AS `

## 14. full_agent / D14_AJ_014

- 类别：`aggregate_filter_join`
- 问题：2018 年 6 月下单且已送达的订单，按客户城市汇总支付金额，返回金额最高的 10 个城市。
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`sql_execution`
- 受影响层：sql_execution, result_semantics
- 失败评分层：execution_success
- scorer 分类：candidate_execution_trace_mismatch, execution_behavior_mismatch, source_result_mismatch
- SQL 摘要：`WITH delivered_orders AS ( SELECT o.order_id, o.customer_id FROM fact_orders o WHERE o.order_status = 'delivered' AND o.order_purchase_timestamp >= :start_date AND o.order_purchase_timestamp < :end_date_exclusive ), order_payment_totals AS ( SELECT p.order_id, SUM(p.payment_value) AS payment_amount `

## 15. full_agent / D14_MS_005

- 类别：`multi_step`
- 问题：计算 AM 州 2017 年 2 月已送达 GMV 的环比；若精确的 2017 年 1 月没有观察值，必须返回缺失比较期而不是补零。
- 状态 / 停止原因：`planning_failed` / `invalid_semantics`
- 主失败边界：`sql_generation`
- 受影响层：sql_generation, sql_execution, workflow_state, deterministic_calculation, candidate_safety_choice, lineage, result_semantics
- 失败评分层：sql_generation_success, sql_behavior_correct, execution_success, result_correct, workflow_status_correct, stop_reason_correct, calculation_status_correct, safety_correct, lineage_correct
- scorer 分类：sql_behavior_mismatch, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, calculation_status_mismatch, safety_behavior_mismatch, lineage_mismatch, source_result_mismatch, result_mismatch
- SQL 摘要：`无 SQL`

## 16. full_agent / D14_RU_001

- 类别：`risk_ambiguous_unanswerable`
- 问题：分析 2018 年 7 月的销售额。
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`planning_semantics`
- 受影响层：planning_semantics, result_semantics, sql_generation, sql_execution, workflow_state, candidate_safety_choice
- 失败评分层：sql_behavior_correct, result_correct, workflow_status_correct, stop_reason_correct, safety_correct
- scorer 分类：unexpected_business_result, sql_behavior_mismatch, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, safety_behavior_mismatch, result_mismatch
- SQL 摘要：`WITH monthly_gmv AS ( SELECT strftime('%Y-%m', fo.order_purchase_timestamp) AS purchase_month, SUM(oi.price) AS gmv FROM fact_orders fo JOIN fact_order_items oi ON fo.order_id = oi.order_id WHERE fo.order_status = 'delivered' AND fo.order_purchase_timestamp >= :start_date AND fo.order_purchase_times`

## 17. full_agent / D14_RU_002

- 类别：`risk_ambiguous_unanswerable`
- 问题：按商品品类拆分 2018 年 7 月已送达订单支付金额，并给出排名。
- 状态 / 停止原因：`planning_failed` / `invalid_semantics`
- 主失败边界：`workflow_state`
- 受影响层：workflow_state
- 失败评分层：sql_generation_success, execution_success, stop_reason_correct
- scorer 分类：stop_reason_mismatch
- SQL 摘要：`无 SQL`

## 18. full_agent / D14_RU_010

- 类别：`risk_ambiguous_unanswerable`
- 问题：在分析数据库文件不可用时查询 2018 年 7 月已送达订单数。
- 状态 / 停止原因：`succeeded` / `completed`
- 主失败边界：`environment_propagation`
- 受影响层：environment_propagation, result_semantics, sql_execution, workflow_state
- 失败评分层：result_correct, workflow_status_correct, stop_reason_correct
- scorer 分类：unexpected_business_result, execution_behavior_mismatch, workflow_status_mismatch, stop_reason_mismatch, result_mismatch
- SQL 摘要：`SELECT COUNT(DISTINCT order_id) AS delivered_order_count FROM fact_orders WHERE order_status = 'delivered' AND order_purchase_timestamp >= :start_date AND order_purchase_timestamp < :end_date_exclusive`

## 解释边界

- 代表案例是事后诊断样本，不是二次调参集。
- 相同题目跨版本的差异可能来自检索、提示布局、规划、状态机、安全门或确定性计算；只有 direct_sql 与 retrieval_sql 的对照接近检索上下文消融，仍混有 token 长度与提示布局差异。
- 完整 Agent 主运行没有触发 repair 调用，因此不能估计修复机制的实际增益。
- 业务答案没有独立金标准，不能从 SQL 结果正确推导业务结论正确。
