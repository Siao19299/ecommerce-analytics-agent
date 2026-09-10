# Day 9 交接：SQL 错误修复与可观测性

记录日期：2026-09-11。

## 1. 已核验起点

- 分支：`main`；创建本交接前工作区干净。
- Day 8 功能提交：`f753e88 feat: complete Day 8 SQL safety controls`。
- Day 8 之前的交接提交：`e8a587b docs: record Day 8 handoff`。
- 项目解释器：独立 `.venv`，Python 3.11.9。
- SQLGlot：30.18.0；`requirements.txt` 为 `sqlglot>=30.0,<31.0`。
- 最终全量复验：`155 passed in 7.08s`；`pip check` 无冲突；compileall 通过。
- Day 8 真实 SQLite 安全批次：16/16 符合预期；12 个安全拒绝均为
  `execution_started=false`，1 个结果截断，1 个带 `LIMIT 1` 的高成本查询
  被超时中断。
- `data/processed/olist.sqlite3` 仍为六张核心表、38 个字段；Day 8 批次运行前后
  SHA-256 均为
  `ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c`。
- `data/raw/` 中 `archive.zip` 和九个 CSV 未修改。
- Day 8 实际学习时间：用户明确提供的 2 小时。
- Day 8 没有真实模型或外部 API 调用，没有读取 API Key。

以上状态在新会话中仍应实际检查，不能只相信本交接记录。发现未提交修改时先
判断来源，不得覆盖或删除。

## 2. Day 8 已形成的边界

`src/ecommerce_agent/sql_safety.py` 使用 SQLGlot SQLite AST，默认拒绝空 SQL、
解析失败、多语句、非查询、DML/DDL/SQLite 特殊操作、危险函数及不支持的数据源；
通过 scope/qualify 校验物理表、别名、CTE、子查询、列归属和通配符。

权限关系为：

```text
全局六表/38 字段 ∩ 本次 AnalysisPlan/SqlGenerationContext 范围
```

`execute_read_only_query` 在执行前再次运行相同安全校验，并保留 SQLite URI
`mode=ro`、`PRAGMA query_only=ON`、单次 `execute`、命名参数绑定、数据库 Schema
一致性核对、默认最多 1000 行和默认 10 秒 progress handler 截止时间。

`day07_pipeline.py` 已保存候选 SQL、参数、安全 trace、结果、截断、超时和
`execution_started`。安全失败直接返回，没有修复循环。

## 3. Day 9 必须先解决的设计问题

总计划要求学习异常分类、有限重试、指数退避、日志等级、运行 ID、结构化日志，
并实现受控修复提示、最大修复次数、完整尝试记录、不可修复错误提示和至少五类
SQL 错误测试。

开始编码前先审计当前错误路径，形成明确的修复资格矩阵。特别注意：Day 8 已经
在执行前拦截未知表、未知字段、解析失败和参数合同错误，因此不能为了满足 Day 9
测试数量而虚构这些错误仍能正常到达 SQLite。需要区分：

- 安全失败：永不进入修复；包括表字段越权、解析失败、多语句、非查询、危险
  操作、Schema 漂移和资源超时。
- 可考虑修复的数据库 SQL 错误：必须是候选 SQL 已通过 Day 8 安全门，随后由
  SQLite 返回，并且修改 SQL 不需要扩大 AnalysisPlan、允许列表或参数合同。
- 不可修复的运行环境错误：数据库不存在、损坏、锁定、权限或基础设施故障等，
  应清晰返回，不应让模型反复尝试。
- 业务结果错误：SQL 安全且成功执行但口径、粒度、时间字段或 JOIN 错误；不能
  冒充数据库错误修复成功，仍以指标字典和独立参考结果为准。

如果现有架构使某类计划中的“字段/表名错误”在 SQLite 前就被安全拒绝，应如实
记录并调整 Day 9 的可达测试设计，而不是削弱 Day 8 安全门。

## 4. 修复循环建议约束

具体方案应先讲解、练习并由用户理解后再定稿。至少评估以下约束：

1. 首条 SQL 只有通过 Day 8 安全校验后，数据库的可修复错误才可触发修复。
2. 修复候选每次都重新经过 AST、两级允许范围、参数合同和资源限制。
3. 修复次数设硬上限；“重试次数”与“总尝试次数”必须定义清楚。
4. 对 SQL 做规范化或哈希，拒绝原样输出和已经尝试过的重复候选，防止循环。
5. 修复不能增加计划外表字段、改变指标口径或绕过命名参数。
6. 超时、安全拒绝和环境故障不得送入修复模型。
7. 指数退避适用于限流、超时等暂时性模型传输错误，不应机械用于确定性的 SQL
   数据库错误；SQL 修复次数和模型客户端传输重试需要分别计数。
8. 离线先使用明确标记的假模型响应和真实 SQLite 执行；不得把 fixture 结果写成
   真实模型能力。

## 5. 可观测性与统计建议

一次请求应使用同一个 `run_id`，并按尝试保存：

- attempt 编号与触发原因；
- 原 SQL、规范化 SQL 或哈希、命名参数；
- 数据库错误类别与脱敏消息；
- 修复 Prompt 的上下文来源；
- 修复 SQL 及其安全 trace；
- 是否进入 SQLite、执行结果和耗时；
- 模型名、Token、延迟、finish reason；
- 最终状态和停止原因。

不得记录 API Key。没有真实供应商调用时，不虚构 Token、费用或模型延迟。成本
只有在存在可靠价格来源和真实用量时才计算，否则显式记录不可用。

自动修复成功率应先定义分母。建议至少分别报告：首轮成功数、符合修复资格并
实际尝试的失败数、修复成功数、达到上限数、重复候选数、安全拒绝数和不可修复
错误数；核心修复成功率为“修复成功数 / 实际进入修复的合格案例数”，不要除以
全部请求来美化结果。

## 6. 必须保留的项目边界

- 不得削弱或绕过 Day 8 安全门；修复后的 SQL 与首条 SQL 使用同一策略。
- 指标字典仍是唯一业务口径来源，安全或修复成功不等于业务正确。
- `RetrievalDocument.fields` 不是任意连接许可。
- 明细与支付分别按 `order_id` 预聚合；真实客户使用 `customer_unique_id`；支付
  金额不支持按 `product_category` 拆分；“销售额”不能静默选择口径。
- 不提前引入 LangGraph、向量数据库、RAG 框架、FastAPI、Streamlit 或前端。
- Day 7 的真实 API 授权不延续。离线流程完成后，如确需真实调用，先说明公开
  问题、外发上下文、模型配置、调用上限与预期费用，再等待用户明确授权。
- API Key 只能由客户端运行时从环境变量读取，不读取、打印、记录或提交其值。
- 不修改 `data/raw/`，不虚构调用、Token、成本、延迟、修复率或测试结果。

## 7. 建议读取顺序

1. 仓库外 `00_ai_application_learning/DAILY_30_DAY_PLAN.md`，重点 Day 9，并了解
   Day 10 确定性分析的边界。
2. `README.md`、`PLAN.md`、`LEARNING_LOG.md`。
3. 本文件与 `docs/DAY08_ACCEPTANCE.md`、`docs/DAY08_SECURITY_RESULTS.json`。
4. `docs/DAY07_ACCEPTANCE.md`、Day 7 benchmark/live 结果。
5. `sql_safety.py`、`sql_generation.py`、`day07_pipeline.py`、模型客户端、
   `AnalysisPlan`/planner、日志与相关测试。

先用项目 `.venv` 运行全量测试，核对数据库和原始数据，再根据可达错误路径提出
Day 9 方案。先讲概念并每次只安排一道有价值的小任务；不要像 Day 8 初始过程
那样先把全部工程做完再补学习。
