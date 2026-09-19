# Day 15 验收：三版本真实评测与项目收尾

## 当前结论

Day 15 的工程、真实三版本主实验、配对统计、真实失败分析和本地交互 Demo 均已完成，并按用户明确提供的 2 小时记录实际学习时间。用户在了解当前机器没有 Docker CLI 后，接受将 Compose 实际 build/run 作为已披露的环境限制，并要求完成本地提交。因此本次验收结论为：**通过，带一项非阻塞的 Docker 实跑限制**；不得把它表述为容器或生产部署已验证。

## 真实主实验

- 模型：DeepSeek `deepseek-flash`，thinking disabled，temperature 0。
- 三版本各使用同一 60 题公开清单，顺序、数据库、评分规则和超时边界一致。
- 候选批次先封存，再物化规范化提交、二次封存并授权私有评分。
- 直接 SQL：完整契约 6/60，结果正确 0/51，60 次调用。
- 检索 + SQL：完整契约 22/60，结果正确 16/53，60 次调用。
- 完整 Agent：完整契约 25/60，结果正确 26/52，109 次调用。
- 正式实验合计 229 次调用、750,124 prompt tokens、33,382 completion tokens，本地保守费用估算 $0.2651。
- 完整 Agent 的 60 次规划产生 49 次 SQL 生成、0 次修复；未追加无信息量的 repair-off 真实运行。
- 三版业务答案分母均为 0，状态为未独立评估。

首次临时快照运行因漏复制遥测字段而产生 60 条 `adapter_exception`，在私有评分前停止；另有 1 次诊断调用。无效产物与正式结果分开保留，没有修改提示词、冻结题目或评分规则。包含无效运行的共享账本合计 290 次调用，保守估算 $0.3042 / ¥2.04。

## 冻结与隔离复核

- Day 14 数据集仍为 `1.0.0`，60 题分类为 20/20/10/10。
- 内容哈希：`4ab5fa8c54ef830982bcb03828adb2133d20c68eda343d76ca059d5577d0c591`。
- 数据集文件哈希：`cd77ed6d98d37be7c87aa773f37820ee8c8ec21117b875f2c7b02a253d8e7093`。
- 公开清单哈希：`d44caf279d5fb15972845787e1664f066bd4333248dfda131efdc3c671a1dc6e`。
- SQLite 哈希：`ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c`。
- `archive.zip` 与九个 CSV 的哈希全部匹配 Day 14 基线。
- 真实候选只读取公开题面、Schema 与允许的检索资料；标准 SQL、预期结果、评分规则、数据库行和 API key 未外发。
- 正式运行来自干净独立快照提交 `97173a03793704bbbc65f032ec4fd4067abc9a81`；主工作区保留 Day 15 未提交修改用于最终交付。

## 统计与失败分析

- 每版比例保留 Wilson 95% 区间；同题版本差异使用 seed `15015`、10,000 次 paired bootstrap。
- 检索上下文相对直接版的完整契约差异为 +26.7 个百分点，区间 `[+16.7, +38.3]`。
- 完整 Agent 相对检索版的结果正确差异为 +19.2 个百分点，区间 `[+7.7, +30.8]`；但状态正确差异为 -18.3 个百分点，区间 `[-30.0, -6.7]`。
- 127 条真实逐题契约失败完成主边界归因，并选取 18 个跨版本代表案例。
- 13 个机械故障场景继续用于分类器边界验证，不冒充真实模型失败。

## 工程验收结果

- 分支：`main`；Day 15 起点提交：`6f918b8e2ebbb27ea9f01f1c3d8046507b1d7a86`。
- Python：项目 `.venv` 的 3.11.9。
- 全量测试：`463 passed, 1 warning in 392.93s`。
- `pip check`：通过。
- `compileall`：通过。
- 真实 SQLite 离线复现：通过；60/60 oracle replay 仅验证评分器。
- 敏感信息扫描：352 个文本文件，0 个发现；API key 值读取次数为 0。
- Docker 配置结构测试和宿主机等价入口：通过。
- Docker/Compose 实际 build/run：未执行；当前机器不存在 `docker` 或 `docker-compose` 命令。
- 本地真实交互 Demo：通过。澄清路径返回 200 且不进入 SQLite；明确问题完成规划、SQL、安全门、SQLite 和展示，返回已送达订单数 96,478。Demo 与正式评测使用不同预算账本。

唯一 warning 来自 Starlette TestClient 对 AnyIO 旧别名的依赖弃用提示，不影响验收结果。

## 可追溯产物

- 结构化真实结果：`docs/DAY15_LIVE_RESULTS.json`。
- 总结果：`docs/DAY15_RESULTS.json`。
- 实验报告：`docs/DAY15_EXPERIMENT_REPORT.md`。
- 真实失败分析：`docs/DAY15_REAL_FAILURE_ANALYSIS.md`。
- 实验设计与隔离：`docs/DAY15_DESIGN_AUDIT.md`、`docs/DAY15_REPRODUCIBILITY.md`。
- Docker 边界：`docs/DAY15_DOCKER_REPRODUCIBILITY.md`。

## 已知限制

1. 当前主机没有 Docker CLI，不能声称 Compose 已实际运行；Dockerfile/Compose 仅完成结构测试和宿主机等价离线检查。
2. 真实实验只做一次温度为 0 的主运行，没有多随机种子重复，置信区间描述的是当前固定题集上的题目抽样不确定性，不是跨模型、跨数据集或跨随机运行的普遍结论。
3. 三个版本均没有独立业务答案金标准，业务正确性保持“未独立评估”。

最终快速哈希、依赖、编译和敏感扫描复核后创建本地 Git 提交；不上传 GitHub。若以后获得 Docker 环境，可补做 Compose 实跑并单独记录，不修改本次封存实验。
