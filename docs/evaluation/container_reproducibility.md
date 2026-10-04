# 模型评测 Docker/Compose 离线复现

## 目标与非目标

容器包用于复核 Python 版本、冻结集/公开清单/数据库/原始文件哈希、60 题结构、私有评分器确定性回放、依赖完整性和字节码编译。它不会调用候选模型或外部 API，不读取 `.env`，也不报告三版本准确率。

该配置不是生产部署证明：它没有验证云环境、并发容量、认证授权、长期稳定性、跨架构一致性或供应链安全。镜像构建需要从包索引下载依赖；构建完成后的 Compose 验收运行使用 `network_mode: none`。

## 依赖与隔离

- 基础镜像固定到 Python `3.11.9` patch 版本；尚未固定镜像 digest。
- `requirements.lock.txt` 精确锁定项目的直接依赖版本；传递依赖未使用哈希锁文件，因此不是完全可验证的供应链锁定。
- `.dockerignore` 排除 Git、`.venv`、`.env`、缓存和非必要生成物，但明确保留冻结评测资产、九个 CSV、`archive.zip` 与固定 SQLite 数据库。
- Compose 运行时断网、根文件系统只读，只提供临时 `/tmp`；没有挂载 API Key 或宿主机 `.env`。
- 镜像内的私有金标准只供评分器回放。真实候选运行仍必须使用两阶段隔离，不能让候选进程读取整个镜像文件系统。

## 复现命令

在项目根目录执行：

```powershell
$env:SOURCE_COMMIT = git rev-parse HEAD
docker compose build offline-check
docker compose run --rm offline-check
```

命令成功时，容器向标准输出写出结构化 JSON，`accepted` 应为 `true`。该 JSON 是离线环境检查结果，不是模型评测结果。如需保留记录，应由调用方在容器外重定向到本次运行目录，并与候选封存物分开保存。

## 当前验证状态

宿主机上的同一检查入口及配置结构测试可以用项目 `.venv` 执行：

```powershell
.\.venv\Scripts\python.exe -m src.ecommerce_agent.offline_check
.\.venv\Scripts\python.exe -m pytest tests/test_offline_check.py -q
```

2026-09-19 当前开发机器未安装或未暴露 Docker CLI，因此不能声称镜像构建和 Compose 运行已经通过。此限制应保留到最终验收；只有在具有 Docker 的环境实际执行上述两条命令后，才能把 Docker/Compose 状态改为已验证。
