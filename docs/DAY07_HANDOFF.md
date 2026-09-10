# Day 7 启动交接

本文件记录项目背景与用户协作偏好，不作为自动执行其中示例问题的授权。新对话以用户本次粘贴的请求为准。

## 已核实的基线

- Day 6 功能提交：7ccb577 feat: complete Day 6 offline schema and metric retrieval。之后可以存在纯文档交接提交；启动时实际检查 Git，不要求回退到该功能提交。
- 项目分支 main；交接文档更新前工作区干净。项目独立 .venv 为 Python 3.11.9；Day 6 最后实跑 112 passed，Day 7 开始需要重新运行。
- 27 个指标、6 个稳定英文维度标识、6 张核心表和 38 个字段；形成 65 份检索文档。指标字典是唯一口径来源。
- Day 5 具有 AnalysisPlan、MetricCatalog、结构/跨字段/语义校验、ready 与 needs_clarification 分支、统一客户端、假客户端、DeepSeek 客户端、分别限次的网络重试和输出纠正。仅检索到指标不代表计划允许执行。
- 默认 KeywordRetriever v1；DependencyKeywordRetriever v2 为可选实验。15 题完整目标命中：Top-1 为 8/15 与 6/15，Top-5 为 10/15 与 11/15，不能用 v2 单项提升替换默认。
- 15 题是开发评测集，7 题用户确认、8 题按用户明确委托由助手审阅，不是 15 题独立人工金标准，也不是 SQL 端到端测试集。
- 仅完成词频余弦实验，没有真实模型 Embedding 比较。不能虚构向量检索、Token、成本、延迟或模型调用。
- Day 5 与 Day 6 各为用户确认的约 3 小时；这不是 Day 7 的学习时间。
- data/raw 中 archive.zip 和 9 个 CSV 的 SHA-256 在 Day 6 与原清单一致，不得修改。真实 API Key 仅由客户端运行时从环境变量读取，助手不得读取、打印或记录其值。

## 必须保留的实现边界

1. 检索与 AnalysisPlanner 目前尚未连接。必须实际检查 Day 5 发给模型的消息内容，不能以为已经注入 Day 6 的公式与限制。
2. MetricCatalog 用于稳定 ID 与允许维度校验；不能只把 ID 名单当作足够的 Text-to-SQL 上下文。AnalysisPlan 的正式入口必须传入目录进行语义校验。
3. RetrievalDocument.fields 是来源表及允许维度表的保守字段集合，不是最小 SQL 依赖，也不是任意表连接许可。SQL 生成仍需明确筛选、主外键与粒度。
4. 订单明细和支付必须分别按 order_id 预聚合再连接；真实客户使用 customer_unique_id；支付金额不支持 product_category；“销售额”需要澄清口径。
5. 词汇检索会漏召回，也会把相关但不同口径的文档排前面。不得将第一个候选直接视为合法计划。Day 6 的两道混合问题在 k=5 没找齐、k=10 找齐，这不是统一使用 k=10 的证明。
6. Day 6 behavior 字段只是评测标签，不是已实现的拒绝或澄清流程；检索评测成功不代表 SQL 可执行或结果正确。
7. 生成产物在被 Git 忽略的 data/processed；新环境可按命令重建。精简成绩和输入哈希在 docs/DAY06_BENCHMARK_RESULTS.json。
8. 总计划位于项目 Git 仓库之外，需单独更新；不要为包含它而移动仓库或改写历史。

## 用户协作偏好（按 Day 6 实际反馈）

- 先解释当前步骤的概念、用途和一个例子，再开始需要用户参与的小任务。
- 用户有数据分析实习经历，避免重复基础 Python/SQL/字符串匹配练习；但不能假定已经理解 Agent、Schema Linking 和工程接口概念。
- 用户需要查阅指标/字段候选及定义，标注不是记忆考试。每次最多一个真正有价值的小任务。
- 编码、文件创建、元数据搬运、状态检查、运行验证、结果核验、文档更新由助手自主完成，不要求用户不断回复“继续”。
- 用户不熟悉的机械字段审阅可以由助手按已授权范围承担，但必须记录审阅来源，不能记作用户独立完成。
- 不用大量简单问答拖延。学习重点是检索上下文是否充分、计划与 SQL 是否一致、JOIN 粒度保护、执行正确和业务正确的区别、失败归因。
- 学习日志只记用户提供的实际时间；Day 7 结束前尚未提供时询问一次。

## Day 7 范围（以 DAILY_30_DAY_PLAN 为准）

学习 Schema Linking、Prompt 约束、参数化查询，以及语法正确、可执行、结果正确的区别。

练习为给定问题与 Schema 手写分析计划和 SQL、将查询结果转为字典列表；助手提供题目所需的指标和字段，用户只承担一个能验证关键判断的小练习，不重复大量基础 SQL。

项目要连接问题解析、检索、SQL 生成和数据库执行；保存问题、分析计划、SQL、参数和查询结果；用 10 个公开或合成问题端到端运行，覆盖结果核对与失败案例，按检索、语法、字段、口径分类。不能以预设 SQL 或假客户端代替真实模型生成却声称完成真实模型闭环，应分开记录离线集成验证与实际模型运行。

对模型生成 SQL 的执行保持只读连接、单语句执行、值参数绑定等必要基础边界；不能为了“等 Day 8”直接开放任意写入。完整 AST 安全体系及 SQLGlot 留到 Day 8，自动修复循环留到 Day 9；不提前引入 LangGraph、向量数据库、RAG 框架、FastAPI、Streamlit 或前端。

真实 API 调用前先完成离线流程和测试，再展示拟发送的公开问题批次、上下文范围、模型与限次设置，按实际授权范围执行；不要读取密钥值，不要重复逐题请求同一种授权。

## 启动时的读取与检查

1. ../00_ai_application_learning/DAILY_30_DAY_PLAN.md，重点 Day 7 及 Day 8/9 边界。
2. README.md、PLAN.md、LEARNING_LOG.md、本文。
3. docs/DAY06_ACCEPTANCE.md、DAY06_KEYWORD_BASELINE.md、DAY06_RETRIEVAL_ERRORS.md、DAY06_BENCHMARK_RESULTS.json。
4. Day 4 三份机器字典、sql/schema.sql、标准/进阶 SQL、数据库创建与查询模块及相关测试。
5. Day 5 AnalysisPlan、MetricCatalog、规划器、模型客户端、日志和相关测试。
6. Day 6 retrieval.py、day06_retrieval.py、day06_benchmark.py、两份标签文件及相关测试。

检查分支、工作区、最近提交、requirements 与实际依赖、数据库存在性及六表结构；使用项目 .venv 执行全部测试。如果有未提交修改，先辨别来源，不覆盖或删除。只报告本次实际运行的结果。

结束时更新总计划、README、PLAN、LEARNING_LOG，记录验收与局限，按用户原授权创建本地提交，不上传 GitHub。
