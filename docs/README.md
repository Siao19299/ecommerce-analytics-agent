# 文档导航

对外项目概览见[仓库首页](../README.md)。本目录按运行、工程和实验提供阅读入口。

## 运行与工程

| 文档 | 内容 |
| --- | --- |
| [快速运行](QUICKSTART.md) | 新克隆目录安装、公开数据准备、API/页面启动与离线检查 |
| [工程设计与代码导航](ENGINEERING.md) | 指标语义、安全、修复、状态、数值计算和测试证据 |
| [离线 API 示例](examples/monthly_gmv_response.json) | 固定规划/SQL 输入在真实 SQLite 上得到的响应，明确模型调用为 0 |
| [数据来源与许可](DATASET.md) | Olist 来源、使用许可和数据限制 |
| [数据库结构](OLIST_SCHEMA.md) | 六张核心表及关联 |
| [字段字典](OLIST_DATA_DICTIONARY.md) | 原数据字段含义与约束 |

## 实验与验证证据

| 文档 | 内容 |
| --- | --- |
| [真实模型实验报告](DAY15_EXPERIMENT_REPORT.md) | 三版本结果、配对统计、费用与解释边界 |
| [结构化真实结果](DAY15_LIVE_RESULTS.json) | 指标分子/分母、封存哈希与逐题失败摘要 |
| [真实失败分析](DAY15_REAL_FAILURE_ANALYSIS.md) | 18 个代表案例及失败层次 |
| [评测覆盖](DAY14_COVERAGE_REPORT.md) | 60 道冻结题的类别与覆盖 |
| [评测隔离与可重复性](DAY15_REPRODUCIBILITY.md) | 公开题面、候选封存与参考评分边界 |
| [最终验收](DAY15_ACCEPTANCE.md) | 保存的验收状态与证据范围 |
| [Docker 复现边界](DAY15_DOCKER_REPRODUCIBILITY.md) | 配置、离线运行方法和未验证限制 |

## 维护与历史

- [项目交接](PROJECT_HANDOFF.md)：协作者与维护工具的当前状态入口。
- [GitHub 更新说明](GITHUB_PUBLISHING.md)：仓库认证、网络和发布方法。
- [历史开发首页](archive/DEVELOPMENT_NOTES.md)：整理前的逐阶段开发说明。
- [历史计划](archive/PLAN.md)与[学习日志](archive/LEARNING_LOG.md)：保留开发过程。

`DAYxx_*` 文件保留原名便于追溯历史验收与结构化结果；当前功能状态以首页、
实现代码和实际测试为准。历史计划中的建议技术与阶段待办不自动视为已实现功能。
