# 工程设计与代码导航

本项目的能力证据由实现、测试和保存的真实模型结果组成。阅读路线按组件职责排列，
源码中的 `dayXX` 仅表示最初的开发阶段。

## 建议阅读顺序

| 阅读目标 | 入口 | 可以核对的能力 |
| --- | --- | --- |
| 看完整请求如何装配 | [live_runtime.py](../src/ecommerce_agent/live_runtime.py) | 依赖注入、共享调用预算、规划/生成/修复客户端和服务装配 |
| 看业务口径如何进入模型 | [metric_catalog.py](../src/ecommerce_agent/metric_catalog.py)、[analysis_planner.py](../src/ecommerce_agent/analysis_planner.py) | 指标、维度、过滤、时间范围及检索证据的结构化校验 |
| 看模型 SQL 如何被约束 | [sql_safety.py](../src/ecommerce_agent/sql_safety.py)、[sql_generation.py](../src/ecommerce_agent/sql_generation.py) | AST、作用域、字段与表授权、参数合同、只读执行和资源限制 |
| 看错误如何停止与修复 | [day09_pipeline.py](../src/ecommerce_agent/day09_pipeline.py)、[day11_workflow.py](../src/ecommerce_agent/day11_workflow.py) | 错误分类、有限循环、状态不变量和统一 run_id |
| 看数值和结论如何生成 | [day10_comparison.py](../src/ecommerce_agent/day10_comparison.py)、[day10_presentation.py](../src/ecommerce_agent/day10_presentation.py) | 可比较性状态、确定性计算、展示模板和计算来源 |
| 看 API 与页面如何隔离 | [day12_api_models.py](../src/ecommerce_agent/day12_api_models.py)、[day13_streamlit.py](../src/ecommerce_agent/day13_streamlit.py) | 严格响应合同、不同终态的用户语义、避免页面重算和重复请求 |
| 看效果如何被验证 | [day15_reproducibility.py](../src/ecommerce_agent/day15_reproducibility.py)、[真实结果](DAY15_LIVE_RESULTS.json) | 候选封存、参考隔离、分层评分、成本遥测和配对统计 |

## 1. 用业务语义约束 Text-to-SQL

指标字典记录定义、公式、源表、粒度、时间字段、允许维度和约束。模型输出
`AnalysisPlan` 后，Pydantic 与 MetricCatalog 检查合同，规划还必须给出允许的
检索证据。SQL 上下文由已验证计划补齐规范表、字段和口径。

三个容易出现“SQL 正常执行、业务口径错误”的例子：

- 客户去重使用 `customer_unique_id`，订单级 `customer_id` 不能替代真实客户标识。
- 明细与支付分别按 `order_id` 预聚合后再连接，避免两个一对多表相乘。
- 支付金额不能直接归因到商品品类；“销售额”未指定定义时进入澄清分支。

验证：[计划测试](../tests/test_day05_analysis_plan.py)、[规划器测试](../tests/test_day05_analysis_planner.py)、
[指标测试](../tests/test_day04_metric_dictionary.py)。

## 2. 将模型生成与数据库授权分开

安全门通过 SQLGlot 解析 SQLite AST，而不是用关键字字符串判断查询是否合法。
允许的表和字段是全局数据字典与本次计划范围的交集；CTE、别名、子查询和通配符
都需要经过作用域解析。即使表在数据库中存在，本次计划未授权时也不能访问。

执行器再使用 `mode=ro` 与 `query_only`、命名参数、返回行数上限和 SQLite
progress handler。当前默认最多返回 1,000 行、查询截止时间 10 秒；小 `LIMIT`
不能替代扫描与计算时间限制。

验证：[AST 安全测试](../tests/test_day08_sql_safety.py)、[生成与执行合同](../tests/test_day07_sql_generation.py)、
[真实 SQLite 安全案例](DAY08_SECURITY_RESULTS.json)。这些是机制测试，不能视为真实模型安全率。

## 3. 有限修复与可解释的终止状态

只有通过安全校验、实际进入 SQLite、属于允许修复的局部 SQL 错误才进入修复。
安全拒绝、资源超时、环境故障和业务结果错误保持各自终态。每条修复候选重新
经过原安全门，不能通过修复扩大计划权限。

本地装配默认最多 2 次修复；规范化 SQL 的 SHA-256 检测重复候选。SQL attempt、
repair attempt 和模型传输 attempt 分开计数。顶层状态机设置节点步数上限，
且节点只能修改声明的状态字段。

验证：[修复工作流](../tests/test_day09_pipeline.py)、[重复 SQL](../tests/test_day09_sql_identity.py)、
[状态机合同](../tests/test_day11_state_machine.py)。真实主实验没有触发修复调用，暂不能量化修复增益。

## 4. 数值计算与来源追踪

SQL 返回原始聚合数据，Python 按上一自然月或上年同月计算变化。缺比较期、零基期、
不完整期间和不足历史不被补成常规增长率。品类贡献校验同一分析范围；规则型异常
使用当前期之前的连续历史窗口 median/MAD，避免未来信息进入基线。

计算 trace 保存 `parent_run_id`、`source_sql_attempt`、计算方法和输入哈希。
页面使用 API 已计算的数据绘图，不再聚合或重新生成增长率。

验证：[同比/环比](../tests/test_day10_comparison.py)、[贡献度](../tests/test_day10_contribution.py)、
[异常检测](../tests/test_day10_anomaly.py)、[页面成功展示](../tests/test_day13_success_view.py)。
可直接检查[实际离线 API 示例](examples/monthly_gmv_response.json)。

## 5. 服务边界与工作流编排

FastAPI 通过应用工厂注入服务，HTTP 映射层显式投影内部状态；响应不返回 Prompt、
模型原始输出或内部异常。`/health` 仅检查装配状态，不发起模型调用。Streamlit
只调用公开 API，并通过提交状态避免普通页面重运行再次发起分析。

普通 Python 状态机是核心实现。LangGraph 的 `StateGraph` 与条件边调用同一个
`step()`，便于核对框架映射是否保持原工作流语义；本地真实演示没有另写一套业务逻辑。

验证：[运行时装配与澄清](../tests/test_live_runtime.py)、[API 路由](../tests/test_day12_analyze_route.py)、
[HTTP 状态映射](../tests/test_day12_mapping.py)、[页面提交](../tests/test_day13_streamlit_submit.py)。

## 6. 评测设计与实际发现

候选只读取公开 `case_id/question`。全部输出保存并封存后才加载评分参考，分别评价
SQL 执行、结果、状态、停止原因、计算、安全、lineage、调用和成本。评分器的
60/60 oracle replay 与真实模型表现分开记录。

真实实验中，完整 Agent 在共同可评估的 52 题上，相对检索版改善 11 题、退化 1 题，
结果正确率差异为 +19.2 个百分点。但完整契约净提升只有 3/60，状态层明显退化，
平均延迟从 13.50 秒增加到 26.63 秒。这说明评估工作流需要观察失败传播与服务代价。

验证：[评测隔离测试](../tests/test_day15_reproducibility.py)、[评分测试](../tests/test_day15_scoring.py)、
[配对结果与限制](DAY15_EXPERIMENT_REPORT.md)、[真实失败案例](DAY15_REAL_FAILURE_ANALYSIS.md)。

## 下一步工程优先级

1. 根据已保存的失败边界改进规划语义与结果合同，在新开发题集验证，保留冻结最终集。
2. 改进完整 Agent 的状态与停止原因传播，再评估结果收益和延迟是否值得。
3. 在具备 Docker 的环境完成容器实跑，补充新环境与并发条件的验证记录。

当前版本是可演示的本地作品集：没有生产部署或业务上线证据，业务答案也未经过独立金标准验证。
