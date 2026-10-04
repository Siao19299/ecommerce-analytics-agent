# 模型评测 模块 6：统一批量运行器、断点续跑与逐题原始记录

## 1. 统一运行边界

三个适配器都暴露 `candidate_version` 和 `run_case(PublicCase, run_id=...)`。批量运行器
只读取冻结公开清单，按其中顺序运行 60 题，不加载 dataset.v1.json、references 或评分
规则。适配器版本必须与运行清单一致；主实验要求运行清单记录的工作区已经干净提交。

每题使用稳定的 `<experiment_run_id>-<case_id>` lineage，原始记录包含开始/结束时间、
wall-clock、适配器记录类型、完整 payload 及 payload SHA-256。适配器意外异常被记录为
固定 `adapter_exception`，不保存异常文本、路径或凭据。

## 2. 追加写入和断点续跑

`candidate_outputs.jsonl` 只追加、不覆盖。每行写入后执行 flush 与 fsync，再原子更新
checkpoint。续跑时，既有记录必须是公开清单的严格前缀，run ID、版本、ordinal、case ID
和 payload 哈希必须全部匹配；完成的案例不会再次调用适配器。

checkpoint 保存完成 case ID、候选文件前缀字节数和 SHA-256。历史前缀改变即拒绝续跑。
若进程在成功追加完整行后、checkpoint 原子替换前中断，运行器可在完整合同校验后重建
checkpoint；这不会重新生成候选，也不会读取金标准。

## 3. 封存条件

只有 60 个公开案例全部存在且顺序正确，运行器才生成 `candidate_outputs.seal.json`。封存
记录候选 JSONL 的路径、SHA-256、字节数、非空行数、时间和完整性。未完成批次只保留
checkpoint，不生成封存证明，也不能进入后续私有评分授权。

再次调用已完成运行只复核现有记录并重新确认同一哈希，不调用适配器。运行器结果明确
记录 resumed/newly-run 数量以及 `private_references_loaded=false`。

## 4. 当前验证范围

本模块使用 60 题公开清单和离线 stub 适配器验证完整运行、零调用续跑、7+53 断点恢复、
篡改拒绝、版本错配和输出目录边界。它验证运行与封存机制，不是模型评测结果。外部 API
调用和真实模型运行仍为 0。
