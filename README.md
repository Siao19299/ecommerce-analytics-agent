# 跨平台电商经营分析 Agent

## 项目定位

- 目标岗位：AI 应用工程师、大模型应用开发、数据分析 Agent 开发
- 项目角色：求职简历中的主项目
- 预计投入：约 70 小时
- 当前状态：Day 4 已完成并验收（2026-09-06，`27 passed`）

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

# 运行全部自动测试
.\.venv\Scripts\python.exe -m pytest
```

数据库、质量报告和查询结果生成在 `data/processed/`，属于可再生成且被 Git 忽略的产物；`data/raw/` 中的原始文件不得修改。

Day 4 指标语义层的机器可读定义位于
`data/metadata/metric_dictionary.csv`。标准 SQL 位于
`sql/day04_standard_metrics.sql`，进阶 SQL 位于
`sql/day04_advanced_metrics.sql`。进阶查询覆盖月度环比、同比、品类
Top-N、贡献度以及先按订单预聚合的 GMV/支付对账。

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
