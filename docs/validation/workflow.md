# Agent 工作流：状态机与 LangGraph 验收

## 1. 起点实测

- 分支为 `main`，开始时工作区干净。
- HEAD 为 `3ef58a9 feat: complete 确定性分析 deterministic analytics`；确定性分析
  开始前提交为 `b663ccd feat: complete SQL 修复 SQL 修复 observability`。
- 项目独立 `.venv` 为 Python 3.11.9。
- SQLGlot 实际版本为 30.18.0；`requirements.txt` 约束
  `sqlglot>=30.0,<31.0`。LangGraph 开始时未安装且无依赖约束。
- 开始前全量实跑为 `240 passed`；`pip check` 无冲突。
- 确定性分析 真实 SQLite 离线机械批次实跑为 4/4 符合预期；外部 API 调用和模型
  生成数值均为 0；四例均为 `not_independently_evaluated`。
- SQLite 实际为六张核心表、38 个字段；数据库 SHA-256 为
  `ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c`。
- `data/raw/` 的 `archive.zip` 和九个 CSV 大小与 SHA-256 全部匹配清单。

## 2. 编码前能力审计

查询链路 的 `QueryAgent.run()` 顺序执行指标/Schema 检索、规划、SQL 上下文构建、
SQL 生成及安全校验、只读执行和字典结果组装。它有最终状态，但没有统一的类型
状态、节点合同、条件边或顶层节点 trace。

SQL 修复 已经是成熟的局部状态机：`AttemptCounter` 分离修复轮次、SQL attempt、
模型传输 attempt 和 SQLite entry；`RepairLimits` 强制修复上限；SQL 规范化哈希
拒绝历史重复；错误分类默认拒绝；每个候选重新经过 SQL 安全 安全和参数合同；同一
`run_id` 保存逐 attempt trace。因此 Agent 工作流 没有重写第二套修复循环。

确定性分析 的 `CalculationLineage` 和 `CalculationTrace` 已通过 `parent_run_id` 与
`source_sql_attempt` 连接 SQL 修复。同比/环比、贡献度、异常、查询结果适配和确定性
展示均可直接包装。现有业务分支主要通过结果对象和枚举传播，输入合同或程序
不变量通过异常传播；Agent 工作流 保留并明确这一区分。

## 3. 普通 Python 状态机

`WorkflowState` 统一保存顶层 `run_id`、当前节点、最终状态、停止原因、
检索、规划、SQL、SQL 安全 safety trace、SQL 修复 attempt trace、确定性分析 calculation
trace、确定性展示和节点 trace。

九个节点为：

1. retrieval
2. planning
3. sql_generation
4. sql_safety
5. execution
6. bounded_repair
7. deterministic_analysis
8. presentation
9. finalization

每个 `NodeContract` 声明用途、前置字段和允许修改字段；引擎拒绝越权写状态和
非法条件边。预期业务分支返回 `NodeResult`；合同或程序不变量异常在节点边界转成
对应受控失败。澄清、安全拒绝、资源失败、环境失败、执行失败、修复失败、修复
上限、计算失败和展示失败不再混成一个 `failed`。

顶层状态机最多执行 16 个节点。SQL 修复 内层仍由 `max_repair_attempts`、重复候选
哈希和模型客户端传输上限强制停止。顶层不重置 SQL 修复 计数，也不把传输 attempt
冒充 SQL attempt。

## 4. 关键条件边

- 规划要求澄清：直接 finalization，不生成 SQL。
- SQL 生成输出危险语句：进入独立 sql_safety 节点后拒绝，不执行、不修复。
- 首次执行成功：进入确定性分析。
- 可修复 SQLite 错误：由 SQL 修复 有限循环处理；修复成功后才进入确定性分析。
- 修复达到上限、重复候选、修复候选安全失败或修复模型失败：按各自停止原因结束。
- 超时、环境错误、参数合同失败和未知数据库错误：不进入修复。
- SQL 成功但 确定性分析 输入或计算合同失败：以 calculation_failed 结束，不展示。
- 缺失比较期或零基期等合法 确定性分析 状态：保留计算状态，可继续确定性展示，
  不冒充工作流失败或标准可比结论。

## 5. run_id、trace 和恢复边界

一次顶层请求创建一个 `run_id`，原样传给 `SqlRepairWorkflow.run()`。确定性
分析必须引用最终成功 SQL attempt；状态机验证 calculation trace 的
`parent_run_id` 等于顶层 `run_id`，并验证 `source_sql_attempt` 等于最终成功
attempt。节点 trace 保存顺序、开始/结束时间、耗时、结果、下一节点、实际更新
字段和必要摘要，可以还原顶层执行顺序。

工作流定义可恢复边界，当前不启用持久化恢复：SQL 修复的完整局部 trace 和确定性分析
不可变 calculation trace 是可重放证据；LangGraph checkpointer、跨进程恢复和
人工中断留待存在真实需求时评估，未为框架演示而增加状态存储。

## 6. LangGraph 映射

在普通 Python 状态机通过指定分支测试后，参考 LangGraph 官方 v1 Graph API，
新增 `langgraph>=1.2,<1.3`，实际安装版本为 1.2.11。映射使用 `StateGraph`、
`START`、条件边和 `compile()`。

LangGraph envelope 只保存规范 `WorkflowState`。每个框架节点调用同一个
`AgentStateMachine.step()`，没有复制指标字典、安全、执行、修复、计算或展示
逻辑。未引入 LangChain agent、LangSmith 上报、checkpointer、向量库或多 Agent。
普通 Python 测试继续作为主验收，LangGraph 测试验证节点完整映射、正常成功和
澄清终止分支。

官方参考：

- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://docs.langchain.com/oss/python/langgraph/use-graph-api
- https://docs.langchain.com/oss/python/releases/langgraph-v1
- https://pypi.org/project/langgraph/

## 7. 自动测试与真实 SQLite 离线闭环

Agent 工作流 新增 16 项助手机械自动测试，覆盖：

- 九个节点合同、前置字段、允许写字段和非法路径保护；
- 正常成功、澄清、安全拒绝、一次修复成功和修复上限；
- 超时、环境错误和未知数据库错误不进入修复；
- 缺失比较期作为确定性状态保留、计算异常受控失败；
- 顶层 16 步硬上限；
- 节点顺序、最终状态序列化和同一 run_id 的 SQL/calculation lineage；
- LangGraph 九节点映射、正常成功和澄清分支；
- 七案例真实 SQLite 批次的范围与诚实口径。

最终全量结果为 `256 passed in 70.85s`；`pip check` 无冲突；`compileall` 通过。

七个机械案例使用假规划、假 SQL 生成和假修复响应，实际读取本地 Olist SQLite：

| 案例 | 最终状态 | SQL attempt | 关键结果 |
|---|---|---:|---|
| 正常成功 | succeeded | 1 | 环比 deterministic status=computed |
| 需要澄清 | needs_clarification | 0 | 未生成 SQL |
| 安全拒绝 | safety_rejected | 0 | DELETE 在执行前拒绝 |
| 一次修复成功 | succeeded | 2 | 修复后继续确定性分析 |
| 修复上限 | repair_limit_reached | 2 | 达到硬上限后停止 |
| 缺失比较期 | succeeded | 1 | missing_comparison_period 被保留 |
| 受控计算失败 | calculation_failed | 1 | 未进入展示 |

7/7 符合助手机械预期；外部 API 调用为 0；模型生成数值为 0；数据库前后哈希
相同。案例没有独立业务参考结果，全部为 `not_independently_evaluated`，不称为
业务准确率、真实模型能力或用户独立完成。完整最终状态和 trace 见
`docs/reports/workflow.json`。

## 8. 最终不可变性与边界

- SQLite 仍为六张核心表、38 个字段，SHA-256 仍为
  `ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c`。
- `data/raw/` 的 10 个清单文件全部重新核对，大小与 SHA-256 无差异。
- 指标字典仍是唯一业务口径来源；真实客户、预聚合、支付金额维度和“销售额”
  歧义边界未改变。
- 安全通过、修复成功、确定性计算完成和业务结论正确仍是四个不同结论。
- 没有真实模型调用，没有读取 API Key，没有 FastAPI、Streamlit、Docker、
  冻结评测集 完整评测集、向量数据库、新 RAG、复杂多 Agent 或自动业务归因。

复现命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m src.ecommerce_agent.workflow_benchmark
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q src tests
```
