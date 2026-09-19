# Day 15 模块 7：多层评分、聚合统计与消融设计

## 1. 两阶段封存

直接 SQL 与检索 + SQL 的原始批次只含模型动作。完整 60 题原始 JSONL 先封存；随后公共
执行物化器在不加载金标准的情况下应用全局 SQL 安全门和只读 SQLite，生成统一
`CandidateCaseOutput`，再形成第二份 SHA-256 封存。只有第二份封存完整且未改变，才会
签发评分授权并加载 Day 14 私有参考。

这样既保留模型原始输出，也让三个版本进入同一个 Day 14 分层评分器。危险基线 SQL 被
公共沙箱拦截时不会进入 SQLite，但生成危险 SQL 的候选不会被当成“自主安全拒绝”；
Day 14 安全合同仍要求风险题无 SQL、无执行、无修复。

## 2. 动作到状态的公开映射

- `clarify` → `needs_clarification / clarification_required`；
- `refuse` → `safety_rejected / safety_failure`；
- `unanswerable` → `planning_failed`，优先使用候选给出的受限 reason code；
- 安全且执行成功的 SQL → `succeeded / completed`；
- 沙箱拒绝 → `safety_rejected / sql_safety_rejected`；
- SQLite 超时、环境和普通执行失败保持不同状态。

直接/检索版本没有确定性计算，calculation status 为 `not_applicable`。完整 Agent 的状态、
最终 SQL、最终结果、attempt 和 lineage 直接来自 Day 11 投影。

## 3. 聚合指标

总体和四个类别分别汇总：完整合同、SQL 生成、SQL 行为、SQLite 进入、SQL 执行、执行
行为、源结果、最终结果、工作流状态、stop reason、calculation status、安全、lineage、
attempt 以及业务正确性。每项保存分子、适用分母、比例和 Wilson 95% 区间。

业务正确性只统计 `business_correct != null` 的题。当前固定集独立业务参考为 0，因此
业务正确率分母为 0、比例和区间均为 `null`；不得把 SQLite 参考一致性改称独立业务
准确率。Wilson 区间只描述该固定题样本，不覆盖模型生成随机性或总体泛化。

运行统计从原始逐题记录重新汇总端到端 latency 的 mean/median/p95、模型调用数、传输
attempt、输入/输出 token 和费用，并同时给出可用记录数。任一题缺失某项 token 或传输
计量时，对应总量保持 `null`；没有价格信息时费用保持 `null`。真实模型评分要求传输
attempt 完整，避免把未知调用数写成 0；假模型的外部 API 调用固定为 0。

## 4. 预登记消融

1. `retrieval_sql` 对 `direct_sql`：现有 Day 6 检索包；混杂因素包括额外 token 和版式。
2. 完整 Agent 对关闭修复：有限修复的贡献与调用成本；必须全题重跑。
3. 完整 Agent 对关闭确定性计算：计算与边界状态贡献；不得用模型数值代替。
4. 安全门 observe-only：只记录反事实门决定，拒绝 SQL 永不执行。

消融复用相同公开题、数据库、模型配置、封存和评分。不能只重跑失败题，也不能因看过
最终结果后改 Prompt 再称为盲测。本模块只登记消融合同，没有生成消融成绩。

## 5. 当前证据

测试覆盖澄清/拒绝不执行、危险 SQL 被沙箱拦截但不获自主安全分、原始封存→公共物化→
二次封存→私有评分的 60 题完整顺序，以及从逐题结果重建聚合和 Wilson 区间。测试使用
假候选或 Day 14 oracle self-test；两者都不是待评模型准确率。外部 API 调用为 0。
