# 冻结评测集 验收：固定 Agent 评测集

## 1. 范围与结论

冻结评测集 建立并冻结了 60 道固定评测题、标准 SQL/结果或停止合同、覆盖与重复度校验、语义结果比较器、盲测清单以及逐题执行框架。完整三版本实验、真实模型批量评测、失败统计和 Docker 保留到 模型评测。

验收结论：`通过`。数据集版本 `1.0.0`，内容哈希 `4ab5fa8c54ef830982bcb03828adb2133d20c68eda343d76ca059d5577d0c591`。

## 2. 固定集与覆盖

- 总题数：60；分类：20 单指标 / 20 聚合筛选多表 / 10 多步骤 / 10 风险歧义不可回答。
- 难度：easy 15 / medium 26 / hard 19。
- 直接覆盖指标字典 ID：24/27；六个受支持维度全部覆盖。
- 归一化模板完全重复：0；高相似问题对：0。
- 当前 60 题均为助手机械编写；用户独立编写、用户审核和独立业务参考均为 0。

## 3. 金标准与隔离

- 候选系统只接收 `cases.public.jsonl` 中的 case_id 与 question。
- 候选输出先完成校验并计算 SHA-256，评测器随后才加载内部参考 SQL、结果和评分规则。
- 52 份标准 SQL 全部通过哈希、命名参数与现有安全门；50 道业务 SQL 重新读取真实 SQLite 并与保存结果一致。
- SQL 文本不做完全相等判断；优先比较执行结果、列、行、多重集、稳定顺序、NULL、ISO 日期和数值容差。
- 状态题比较工作流状态、停止原因、安全行为、执行边界和修复边界。

## 4. 逐题执行框架

逐题结果分别记录 SQL 生成、执行、状态、停止原因、calculation status、源结果、最终结果、安全、lineage、attempt 计数和业务参考状态。SQL attempt、修复轮次与模型传输 attempt 保持分离。

内部 oracle replay 的 60/60 仅用于验证评测器，不是待评模型准确率。真实待评模型运行数为 0，外部 API 调用为 0，模型生成数值为 0。

## 5. 离线验收

- 全量测试：`390 passed, 1 warning`。
- pip check：`No broken requirements found.`。
- compileall：`passed`。
- SQLite：6 张核心表、38 字段；SHA-256 `ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c`。
- data/raw/ 的 archive.zip 和九个 CSV 与 用户界面 基线哈希一致。
- 敏感信息扫描：129 个文件，发现 0 项。没有读取 API Key 值。

## 6. 边界与诚实披露

## 7. 复现命令

```powershell
.\.venv\Scripts\python.exe -m src.ecommerce_agent.dataset_validation
.\.venv\Scripts\python.exe -m src.ecommerce_agent.reference_verification
.\.venv\Scripts\python.exe -m src.ecommerce_agent.evaluator
.\.venv\Scripts\python.exe -m src.ecommerce_agent.dataset_acceptance
```
