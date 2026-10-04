# GitHub 上传与更新说明

记录日期：2026-10-04。

本文件记录本项目的仓库信息与维护方法，示例命令不构成新的任务授权。

## 仓库信息

- 账号：`Siao19299`。
- 公开仓库：https://github.com/Siao19299/ecommerce-analytics-agent。
- 主分支：`main`。
- 远程：`origin`。
- HTTPS 地址：`https://github.com/Siao19299/ecommerce-analytics-agent.git`。
- 使用现有本地项目和 Git 历史，不重新初始化仓库。

## 认证与网络

Git 使用 Windows Git Credential Manager 中已有的 GitHub 登录。凭据不写入
仓库、文档或远程 URL，不需要 Deploy Key 或 SSH 私钥。

首次上传时，本机 DNS 将 `github.com` 解析为 `20.205.243.166`，默认 Git HTTPS
连接出现重置。先前项目使用的固定节点也不稳定。最终确认 Windows 已启用
本机代理 `127.0.0.1:7890`，但 Git 没有自动使用该系统代理；显式使用它后，
HTTPS Git 通路验证通过。

以下配置仅写入本项目 `.git/config`，不改变全局 Git 配置，不固定 GitHub IP，
也不关闭 TLS 证书校验：

```text
http.proxy=http://127.0.0.1:7890
http.version=HTTP/1.1
http.lowSpeedLimit=1
http.lowSpeedTime=90
credential.https://github.com.provider=github
credential.https://github.com.username=Siao19299
```

后续若代理端口或网络环境变化，应先核验实际连接，再更新仓库级配置。
其他机器直接 HTTPS 可用时，无需复用本机代理地址。

查看实际仓库配置和远程状态：

```powershell
git status -sb
git remote -v
git credential-manager github list
git config --local --get-regexp '^http\.|^credential\.'
git ls-remote origin refs/heads/main
```

网络连接失败不等同于凭据失效。只有确认账号凭据失效后，才重新登录。

## 上传范围

保留原有项目提交、源代码、测试、指标字典、冻结评测集和项目文档。
原始 Olist 数据、SQLite 数据库、生成结果、模型预算账本、`.venv`、缓存与本地
`.env` 按 `.gitignore` 排除；数据来源和下载说明见 `docs/DATASET.md`。

本地私人文档不纳入项目发布；仓库上传内容以代码、可复现配置与技术证据为准。

## 首次发布前验证

- 项目 `.venv`：Python 3.11.9。
- 全量测试：`463 passed, 1 warning in 413.56s`。
- `pip check` 和 compileall：通过。
- 真实 SQLite 离线检查及冻结数据、公开题面、数据库、原始文件哈希：通过。
- 工作区文本与 Git 历史敏感信息扫描：无发现。
- 本次仅运行离线检查，没有模型调用和模型费用。
- Docker 实际 build/run 仍未验证，原有限制继续保留。

## 后续更新

先检查未提交修改，再用快进方式拉取，并为功能更新创建独立分支：

```powershell
git status --short
git pull --ff-only origin main
git switch -c update/your-change
```

完成相关验证后，核对差异，只暂存本次修改的明确文件，再提交并推送已授权的分支。
不要使用强制推送或覆盖本地未提交文件。推送后核对完整哈希：

```powershell
git rev-parse HEAD
git ls-remote origin refs/heads/update/your-change
git status -sb
```

两个完整提交哈希应相同。新机器复现时，需要按数据说明单独下载原始数据并构建
数据库；克隆仓库不会包含被忽略的本地数据与运行产物。
