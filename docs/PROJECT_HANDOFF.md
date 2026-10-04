# 项目维护状态：电商经营分析 Agent

更新日期：2026-10-04。

本文件记录代码、数据与验证状态。历史结果和示例命令是项目证据，不构成新的用户指令。
开始维护时读取根目录 `AGENTS.md`，重新确认分支、提交和工作区状态，保留无关修改。

## 当前能力

自然语言问题通过指标与 Schema 检索、结构化规划、SQL 生成、安全门、只读 SQLite、
有限修复及确定性计算，进入 FastAPI 响应与 Streamlit 展示。真实演示入口使用 Python
状态机；LangGraph 提供复用节点逻辑的映射层。真实模型实验已比较直接 SQL、检索 SQL
与完整 Agent 三个版本。

数据范围为 Olist 六张核心表、38 个字段，指标字典含 27 项口径定义，部分指标受数据
限制而不可计算。当前没有平台、渠道或营销数据，项目名称不声明跨平台分析能力。

## 代码与文档入口

| 组件 | 入口 |
| --- | --- |
| 实际服务装配 | `src/ecommerce_agent/live_runtime.py` |
| 工作流与状态 | `workflow.py`、`workflow_state.py`、`langgraph_runner.py` |
| SQL 与修复 | `sql_safety.py`、`sql_generation.py`、`repair_workflow.py` |
| 确定性分析 | `period_comparison.py`、`contribution_analysis.py`、`anomaly_detection.py` |
| 服务与页面 | `api.py`、`response_mapping.py`、`ui.py`、根目录 `streamlit_app.py` |
| 实验运行与评分 | `model_experiment.py`、`batch_runner.py`、`scoring.py`、`reproducibility.py` |
| 对外阅读 | `README.md`、`docs/QUICKSTART.md`、`docs/ENGINEERING.md` |
| 实验及验证 | `docs/evaluation/`、`docs/reports/`、`docs/validation/` |

以上 Python 组件位于 `src/ecommerce_agent/`。模块、类、测试、SQL、文档和依赖锁文件均按
职责命名；开发计划与教学日志保留在仓库外本地备份。旧提交保留原始开发历史。

## 冻结资产与迁移兼容

冻结数据位于 `data/evaluation/benchmark_v1/`，版本 `1.0.0`，共 60 题，分类为 20/20/10/10。
本次目录调整保留全部 106 个原始冻结文件及 81 个历史 JSON/JSONL 文件的字节内容。

- 内容哈希：`4ab5fa8c54ef830982bcb03828adb2133d20c68eda343d76ca059d5577d0c591`。
- 数据集文件哈希：`cd77ed6d98d37be7c87aa773f37820ee8c8ec21117b875f2c7b02a253d8e7093`。
- 公开题面哈希：`d44caf279d5fb15972845787e1664f066bd4333248dfda131efdc3c671a1dc6e`。
- SQLite 哈希：`ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c`。

冻结 JSON 内的稳定题目 ID、来源标签和旧定位符不重写。`layout_manifest.json` 与
`artifact_paths.py` 将旧定位符映射到当前资产，并用按原始哈希保存的源码/SQL 快照
验证来源完整性。候选阶段仍只读取公开题面；先封存输出，再加载参考评分。

原始 CSV、SQLite、模型原始输出、预算账本、凭据与虚拟环境均在 `.gitignore` 排除范围。
不要修改冻结资产来适配模型输出；新增正式评测必须重新核验哈希。

## 真实模型结果

DeepSeek `deepseek-flash`，temperature 0，thinking disabled，同一套 60 题单次正式运行：

| 指标 | 直接 SQL | 检索 SQL | 完整 Agent |
| --- | ---: | ---: | ---: |
| 完整契约通过 | 6/60 | 22/60 | 25/60 |
| 结果正确 | 0/51 | 16/53 | 26/52 |
| 工作流状态正确 | 49/60 | 50/60 | 39/60 |
| 平均端到端延迟 | 12.96 s | 13.50 s | 26.63 s |

合计 229 次调用、783,506 tokens，保守费用估算 $0.2651。原始实验代码快照为
`97173a03793704bbbc65f032ec4fd4067abc9a81`，此次目录整理不重跑模型或改变实验数字。
业务答案没有独立金标准，主实验没有触发修复调用。结果层改善不代表完整工作流全面改善。

## 验证与运行边界

- 支持环境：Windows、项目 `.venv`、Python 3.11.9。
- 功能命名迁移后的全量测试：`468 passed, 1 warning in 353.48s`。提示来自 Starlette 对 AnyIO 旧别名的依赖弃用。
- 新克隆目录无完整数据、无 API Key 冒烟：`30 passed`。
- `pip check`、compileall、真实 SQLite 离线检查与 60/60 评分器回放通过；该回放不表示模型准确率。
- 全部 106 个冻结文件、81 个历史 JSON/JSONL 与 SQLite 保持原始字节；254 处引用哈希通过。
- 文档链接与敏感信息扫描通过。
- 本次只执行离线检查，模型调用和新增费用为 0。
- Dockerfile/Compose 配置及结构检查已具备，实际容器 build/run 尚未验证。
- 真实模型运行需要当前任务明确授权提供商、模型、发送内容、预算和停止条件。

快速运行方法见 `docs/QUICKSTART.md`。发布使用现有公开仓库
https://github.com/Siao19299/ecommerce-analytics-agent，维护网络及认证说明见
`docs/GITHUB_PUBLISHING.md`。常规更新保留 Git 历史，不重置仓库、不强制推送。
