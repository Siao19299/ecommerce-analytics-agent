# 模型评测 模块 5：完整 Agent 版本适配器

## 1. 复用而非复制

完整版本直接调用现有 `AgentStateMachine.run(question, run_id=...)`。检索、规划、SQL
生成、SQL 安全 安全门、只读 SQLite、SQL 修复 有限修复、确定性分析 确定性计算、展示和最终
停止均由原工作流负责。模型评测 适配器不重写节点、条件边、修复资格、16 步上限或状态
到 HTTP 的映射。

API 服务 HTTP 与 用户界面 UI 不参与正确性判断。它们分别是传输投影和页面展示，不是业务
执行事实。

## 2. 状态投影

每题记录：顶层 run_id、最终 workflow status、stop reason、节点序列、检索文档 ID、
初始与最终 SQL、最终命名参数、安全决定、是否曾进入 SQLite、最终执行状态、列与行、
截断状态、calculation status、calculation lineage、确定性结论以及完整状态快照的
SHA-256。

修复后最终 SQL 来自 SQL 修复 最后一条 SQL attempt，不使用仍指向初始候选的
`generated_query` 冒充最终 SQL。SQLite 进入行为按任一 SQL attempt 是否启动判断，
避免修复路径覆盖首次执行事实。

## 3. 调用与 attempt 计量

SQL attempt、repair attempt、生成阶段模型调用、修复模型调用及模型传输 attempt 分开。
规划纠错属于生成阶段的额外模型调用；修复事件只来自 SQL 修复 trace。规划和初始 SQL
生成响应内容不复制到统一记录，但保存 SHA-256 以便与原始运行包对账；现有 SQL 修复
trace 没有保存修复响应原文，因此不虚构对应哈希。

端到端延迟由适配器本地计时，包含检索、规划、执行和确定性计算。模型 latency 与 token
只在所有相关 trace 都提供时求和；部分缺失则整体保持 `null`。没有定价表时费用和币种
保持 `null`。

## 4. 边界验证

机械集成测试使用现有 Agent 工作流 假模型装配和真实只读 Olist SQLite，覆盖正常成功、澄清、
安全拒绝、一次修复成功、缺失用量以及 runner 边界异常。安全拒绝和澄清均不得进入
SQLite 或修复；修复路径保持初始/最终 SQL 与 calculation source attempt 的 lineage。

这些测试验证工程控制流，不是候选模型成绩或独立业务准确率。外部 API 调用仍为 0。
