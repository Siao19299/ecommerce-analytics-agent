# Day 6：关键词基线 v1 与依赖展开实验

## 当前成果

初始基线测试 94 passed；增加评测检查及词频余弦实验后为 105 passed。2026-09-10 加入依赖展开与完整评测后为 112 passed，Day 6 离线验收完成。未安装新依赖，未调用模型或 Embedding API，未生成 SQL。用户确认实际学习时间共 3 小时。

从现有来源生成 65 份文档：27 份 metric、38 份 schema（每字段一份）。包含 document_id、document_type、identifier、chinese_name、description、sources、grain、tables、fields、available_dimensions、constraints；指标另有 formula 和 default_time_field。

- 指标公式、定义、粒度和限制逐字来自 Day 4 指标字典；维度通过现有 MetricCatalog 映射至稳定英文 ID。
- Schema 中文描述来自 docs/OLIST_DATA_DICTIONARY.md，字段、主外键元数据来自数据库字典，每份字段文档附带该表完整 DDL。
- 在内存 SQLite 中加载 schema.sql，检查实际字段集合与数据库字典一致，不访问业务数据库或 data/raw。
- 指标 fields 是来源表及允许维度表的全部字段上下文，不是已推导完成的最小 SQL 依赖；metadata.field_scope 明确标记这一点。表结构上下文保留各表粒度和复合键。
- Schema 的 available_dimensions 仅表示字段与维度标识的映射，不表示所有指标都允许这些维度。
- 检索结果不授予执行权限；支付金额仍不能通过 Day 5 的 product_category 语义校验。

## 评分方式

版本：keyword-bigram-idf-v1。

中文连续文本拆成相邻双字片段，如“支付金额”得到“支付”“付金”“金额”；英文名和带下划线的 ID 保持完整，统一小写。这里的词项不是模型 Token。

每个词项 t 的权重：idf(t) = ln((N + 1) / (df(t) + 1)) + 1。
N 为全部文档数，df 为包含该词项的文档数。罕见词项的权重较大。

分数 = 名称命中词项的 IDF 之和 × 3 + 描述命中词项的 IDF 之和 × 1。
词项在同一字段重复出现不重复计数。名称与描述都命中时，分别贡献分数。

约束始终返回，但不参与正向匹配评分。分数不是概率，不同检索实现的分数不能直接比较。当前没有长度归一化、同义词扩展、否定识别或意图识别；长描述、共享词项和中英文词汇差异都可能影响排序。并列按 document_id 排序，只保证确定性，不代表业务优先级。

先按 document_type 和来源表过滤，再排序取 Top-k；IDF 始终基于完整文档集合。零分不返回。没有按指标允许维度做硬过滤，以便保留不兼容请求的解释依据。

## 复现

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day06_retrieval --question "2018 年 6 月，已送达订单的商品金额加运费，与支付金额相差多少？" --type metric --top-k 5
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day06_retrieval --question "商品价格在哪个字段" --type schema --top-k 3
.\.venv\Scripts\python.exe -m pytest -q
```

输出文档及完整结果保存至 data/processed/retrieval/documents.json 和 latest_result.json。latest_result.json 每次覆盖；需要保留比较实验时使用 comparison.json。结果包含原问题、Top-k、过滤条件、文档、分数、排名和每个命中词项的评分贡献。该离线命令应使用公开或合成问题。

业务边界 retrieve_payload 依赖 Retriever 协议；更换实现不需要改写该边界。尚未连接 Day 5 模型规划流程，也没有实现 Day 7。

## 实际观察

用户已确认的对账问题，指标检索 Top-5：

| 排名 | 指标 | 分数（显示至六位小数） |
|---|---|---:|
| 1 | delivered_payment_reconciliation_difference | 72.439470 |
| 2 | delivered_payment_amount | 71.340858 |
| 3 | delivered_gmv_including_freight | 58.086902 |
| 4 | delivered_gmv | 48.756262 |
| 5 | delivered_freight_amount | 45.188468 |

核心指标虽然排第一，但这不证明全部 Schema 或依赖已召回。用户确认的是核心指标，不是完整召回集合。

混合检索“商品价格在哪个字段”，Top-3 实际依次为 fact_order_items.price（33.8769）、delivered_item_count（9.1971）、dim_products.product_category_name（6.8978）。后两份不是回答存储位置的必要资料：扩大 k 引入噪声；schema 类型过滤可以排除指标文档，但仍需判断剩余字段是否相关。

另行探查发现“实付的钱是多少”没有正分结果；“今年 GMV 与去年相差多少”的前三项是贡献度、环比、同比，均为 11.9697。前者反映词汇不匹配，后者说明共同命中 GMV 与稳定并列排序不能识别比较意图。这些问题尚未由用户标注，不能据此计算人工评测准确率，也不能把同比增长率擅自当作跨年绝对差额。

## 五题基线历史结果

### 五题人工核心指标实测

五题指标选择练习已完成，另有两题边界判断。运行 `.\.venv\Scripts\python.exe -m src.ecommerce_agent.day06_evaluate`，完整结果含评分贡献保存至 data/processed/retrieval/primary_metric_evaluation.json。

仅纳入 annotation_status=human_confirmed_primary_metric_only，排除歧义和维度不兼容判断；这不是完整 Schema 召回率或 SQL 正确率。这些题用于教学与开发，不是独立留出测试集。

| 人工核心标签 | Top-5 内排名 |
|---|---:|
| 支付对账差额 | 1 |
| 期间复购率 | 未命中 |
| GMV 同比增长率 | 2 |
| 平均承运配送时长 | 1 |
| 终态订单取消率 | 1 |

Top-1/3/5 命中率分别为 3/5、4/5、4/5。期间复购问题第一名是已送达客户数（50.900664），期间复购客户数排第四（34.189810），期望的期间复购率未进前五；不能把客户数量当作客户比例。同比题中环比和同比同为 48.731695，按 document_id 并列排序使环比排第一；当前中文双字词项和英文 ID 提取不包含数字年份，无法凭时间间隔区分。后续应诊断评分和表达匹配，不能通过修改人工标签掩盖错误。

## 依赖展开 v2 与完整 15 题结果

DependencyKeywordRetriever 继承同一接口，增加权重为 1 的 dependencies 评分字段。该字段来自公式中已有指标的一层名称与定义。原始公式及分子/分母角色、限制保留在 metadata.formula_dependencies 中，不参与正向匹配；没有新建指标口径。v1 不对新增字段评分，因此基线排名可复现。展开后文档词项频次变化，v2 的 IDF 也随之变化，分数不宜跨版本直接比较。

五题核心指标中期间复购率从第 20 升到第 2，Top-3 命中从 4/5 变为 5/5。正式 15 题包含 7 题用户确认、8 题按用户委托由助手审阅；完整命中题数 v1/v2 为 Top-1 8/6、Top-3 9/10、Top-5 10/11、Top-10 12/13。依赖展开改善部分召回，但更丰富的对账文档也挤占支付金额和含运费成交额的位置；默认仍为 v1。

完整结果与指标定义见 docs/DAY06_ACCEPTANCE.md 和 docs/DAY06_BENCHMARK_RESULTS.json，错误诊断见 docs/DAY06_RETRIEVAL_ERRORS.md。重跑 `.\.venv\Scripts\python.exe -m src.ecommerce_agent.day06_benchmark --require-reviewed` 生成逐题完整报告。

五文本词频余弦实验已完成，用户已独立回答其中一组为 1/2；没有真实模型 Embedding 比较。下一步按总计划进入 Day 7，本次未实现 SQL 生成闭环。
