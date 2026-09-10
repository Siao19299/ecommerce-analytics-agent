# 跨平台电商经营分析 Agent

## 项目定位

- 目标岗位：AI 应用工程师、大模型应用开发、数据分析 Agent 开发
- 项目角色：求职简历中的主项目
- 预计投入：约 70 小时
- 当前状态：Day 7 Text-to-SQL 最小闭环已完成并验收（2026-09-10，`128 passed`；离线 10 题与真实 DeepSeek 4 题分开记录；实际学习 2 小时）

## 一句话介绍

构建一个基于指标语义层和可验证 Text-to-SQL 的电商经营分析 Agent，使用户能够用自然语言完成指标查询、维度下钻、趋势分析、异常诊断和图表生成。

## 项目背景

项目场景来源于多渠道电商数据分析中常见的指标口径分散、取数依赖 SQL、报表解释成本高等问题，与由莱科技实习中的指标体系、自动化报表和跨平台分析经验相呼应。

项目必须使用公开或合成数据，不得使用由莱科技的内部数据、代码、指标口径或未公开业务信息，也不得宣称项目曾在公司上线。

## 核心工作流

```text
用户问题
→ 意图识别
→ 指标定义与 Schema 检索
→ 分析计划
→ SQL 生成
→ 只读安全校验
→ 执行与错误修复
→ 确定性统计分析
→ 图表与经营结论
→ 结果自检
```

## 必做功能

1. 指标语义层：统一 GMV、订单量、客单价、转化率等指标口径。
2. Schema/指标检索：根据问题召回相关指标、表和字段。
3. Text-to-SQL：支持筛选、聚合、排序、时间趋势和多表关联。
4. SQL 安全：仅允许查询语句，拦截 DDL、DML 和高风险查询。
5. 自动修复：根据数据库报错修正字段、语法或连接关系。
6. 分析工具：同比、环比、贡献度、异常识别和维度下钻。
7. 可视化：根据结果选择合适图表并生成文字结论。
8. 可观测性：记录每个节点的输入、输出、耗时、Token 和成本。
9. API 与演示：提供 FastAPI 接口和 Streamlit 演示页。
10. 自动评测：使用固定问题集比较不同版本。

## 建议技术栈

- Python 3.11
- FastAPI
- LangGraph
- PostgreSQL + pgvector
- SQLGlot
- pandas
- Plotly / Streamlit
- pytest
- Docker Compose

可以先用简单 Python 状态机完成最小闭环，再迁移到 LangGraph；不要因为框架学习阻塞核心功能。

## 当前可重复生成的成果

以下命令均在项目根目录使用项目独立 `.venv` 执行：

```powershell
# 重建六张核心表的 SQLite 数据库
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day03_pipeline

# 重建数据质量报告
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day03_quality

# 执行五条基础经营 SQL 并保存结果
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day03_metrics

# 执行 Day 4 的十条标准 SQL 与五条进阶 SQL 并保存结果
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day04_metrics

# 可选：使用已配置的环境变量进行一次真实 DeepSeek 结构化规划
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day05_live `
  --question "分析全部数据中每月的已送达 GMV。" `
  --model deepseek-v4-flash

# 运行全部自动测试
.\.venv\Scripts\python.exe -m pytest

# 运行 Day 7 十题离线闭环与参考查询核对
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day07_benchmark
```

数据库、质量报告和查询结果生成在 `data/processed/`，属于可再生成且被 Git 忽略的产物；`data/raw/` 中的原始文件不得修改。

Day 4 指标语义层的机器可读定义位于
`data/metadata/metric_dictionary.csv`。标准 SQL 位于
`sql/day04_standard_metrics.sql`，进阶 SQL 位于
`sql/day04_advanced_metrics.sql`。进阶查询覆盖月度环比、同比、品类
Top-N、贡献度以及先按订单预聚合的 GMV/支付对账。

## Day 5：模型 API 与结构化输出

Day 5 使用 Pydantic 定义 `AnalysisPlan`，包含指标、维度、过滤条件和
时间范围。模型输出先经过 JSON 与字段结构校验，再使用 Day 4 指标字典
检查未知指标、未知维度及“指标 × 维度”兼容性。缺少必要时间范围时返回
结构化的 `needs_clarification`，不静默猜测。

`ModelClient` 统一假客户端与 DeepSeek 客户端的调用契约；
`RetryingModelClient` 只对超时、限流等暂时性错误进行有限重试。
非 JSON 或非法计划最多进行一次带错误反馈的输出纠正，仍失败则返回受控
错误。日志以 JSON Lines 保存到被 Git 忽略的 `data/processed/logs/`，
只记录运行 ID、模型名、轮次、耗时、Token、结束原因和错误类型，不记录
API Key、问题原文或模型响应原文。

真实 API Key 只从 `DEEPSEEK_API_KEY` 环境变量读取。`.env.example`
只列出变量名，不包含真实值。真实调用会产生供应商费用，并会把 system/user
消息发送至外部模型服务，运行前必须确认数据可以外发。

2026-09-08 使用公开样例问题完成一次真实 `deepseek-v4-flash` 验证。
第一轮返回内容未通过 JSON 校验，第二轮在一次有限纠正后生成合法计划；
两轮分别使用 `1121/512` 和 `1155/120` 个输入/输出 Token。没有根据这些
数据虚构费用，原始模型响应未写入日志。

## Day 6：Schema 与指标检索

已从现有字典生成 27 份指标文档和 38 份字段文档，实现独立 Retriever
接口、关键词评分、Top-k、类型及表过滤、完整结果与评分依据保存。
口径和允许维度沿用 Day 4/5，检索不生成 SQL。

15 题覆盖指标、Schema 和混合检索；7 题来自用户确认，新增 8 题按用户明确
委托由助手对照字典和 DDL 审阅，不宣称全部由用户独立标注。
原基线与依赖展开版本在 Top-5 分别找齐 10/15、11/15 题的指定目标，
但后者 Top-1 从 8/15 降为 6/15，因此默认保留原基线。

运行 `.\.venv\Scripts\python.exe -m src.ecommerce_agent.day06_benchmark --require-reviewed`
重建完整比较。单题模块 `day06_retrieval` 支持 `--type metric/schema/both`、
`--top-k` 和 `--retriever baseline/dependencies`。
运行 `day06_cosine` 重建五个短文本的词频余弦实验；这不是模型 Embedding。
实际学习时间为用户提供的 3 小时。

验收与复现详见 `docs/DAY06_ACCEPTANCE.md`；实际错误、局限和精简评测结果
分别保存于 `docs/DAY06_RETRIEVAL_ERRORS.md` 和 `docs/DAY06_BENCHMARK_RESULTS.json`。
Day 6 验收时尚未实现 SQL 生成闭环；该缺口已在下述 Day 7 工作中补齐。

## Day 7：Text-to-SQL 最小闭环（已完成）

Day 7 已连接关键词检索、Day 5 AnalysisPlanner、计划级规范上下文、SQL
JSON 生成合同、命名参数绑定、只读 SQLite 执行和字典列表结果。规划 Prompt
不再只提供 ID；本次召回文档的定义、公式、字段、时间口径、允许维度和粒度
限制会一并发送。计划通过既有 Pydantic 与 MetricCatalog 校验后，SQL 上下文
再从唯一指标字典、维度字典、数据库字典和 DDL 补齐所需信息。

10 个公开 Olist 合成问题已实际运行：4 题查询结果与独立参考查询一致，另有
检索、SQL 语法、字段、业务口径错误及澄清分支。真实客户反例使用
`customer_id` 得到 96,478，SQL 可执行但与 `customer_unique_id` 规范结果
93,358 不一致，证明“可执行”不等于“业务正确”。完整结果保存在被忽略的
`data/processed/day07/offline_benchmark.json`，精简结果和输入/数据库哈希保存
于 `docs/DAY07_BENCHMARK_RESULTS.json`。

本批次规划为 `fake_model_response`，SQL 为
`fake_model_response_with_preset_sql_fixture`，外部调用为 0；这只验证工程闭环，
不是实际模型 SQL 正确率。执行器保留单 SELECT/WITH、单语句、命名参数、SQLite
只读 URI 和 `query_only` 基础边界。完整 AST 安全留到 Day 8，自动修复留到
Day 9。验收、概念、错误分类与拟定真实调用批次见
`docs/DAY07_ACCEPTANCE.md`。

在用户明确授权后，又使用 `deepseek-v4-flash` 请求配置运行 `D7_01/02/04/05`
四个公开问题。每题恰好一次真实规划与一次真实 SQL 生成，共 8 次成功响应，
没有重试或修复；四题均通过有据规划、参数校验和只读执行，结果与独立参考查询
一致。供应商返回总用量为 28,152 输入 Token 和 739 输出 Token；未计算费用，
不把四题结果外推为整体模型准确率。精简记录见
`docs/DAY07_LIVE_RESULTS.json`。

学习验收聚焦 Agent 设计而非重复基础 SQL：Schema Linking、检索与规划
grounding、Prompt 与程序校验的分工、参数合同、只读执行、结果字典化，及
运行状态与离线评测结论的区别。用户能够指出给定 trace 的根因在 SQL 生成
阶段，并理解 `execution_succeeded` 不等于业务结果已被证明正确。用户明确
提供的 Day 7 实际学习时间为 2 小时。

## 评测设计

建立至少 60 道固定测试题：

- 20 道单指标查询
- 20 道聚合、筛选和多表关联
- 10 道多步骤分析
- 10 道危险、歧义或无法回答的问题

比较三个版本：

1. 直接生成 SQL
2. Schema/指标检索 + SQL
3. 完整 Agent

记录以下指标：

- SQL 执行成功率
- 结果正确率
- 危险 SQL 拦截率
- 平均响应延迟
- 单次问题 API 成本
- 自动修复成功率

所有简历数字必须来自保存的评测结果，不得预先编造。

## 完成标准

- 可以从零启动数据库、API 和演示页面。
- 至少覆盖指标查询、趋势分析、维度下钻和异常诊断。
- 所有数据库访问默认只读。
- 60 道评测题能够一键运行并保存结果。
- README 包含架构、数据说明、复现步骤、实验结果和局限性。
- 能在 5 分钟内独立讲清系统流程、失败案例和技术取舍。

## 新对话启动提示词

> 请先完整阅读当前目录中的 README.md、PLAN.md 和 LEARNING_LOG.md。我的目标是在一个月求职准备中完成“跨平台电商经营分析 Agent”，用于投递 AI 应用工程师岗位。我编程基础较弱，可以在 AI 辅助下开发，但需要同时理解和掌握核心代码。请从 PLAN.md 中第一个未完成任务继续，每次先说明本轮目标和验收标准，再协助我实现、测试，并更新计划和学习日志。不要使用或虚构任何公司内部数据。
