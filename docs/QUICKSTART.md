# 快速运行

推荐环境为 Python **3.11.9** 与项目独立 `.venv`。以下 PowerShell 命令用于
新克隆目录；已有冻结评测数据库的目录应直接使用已有数据，避免重建覆盖。

## 1. 克隆与安装

先确保 `python` 指向 Python 3.11.9，再执行：

```powershell
git clone https://github.com/Siao19299/ecommerce-analytics-agent.git
cd ecommerce-analytics-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-day15.lock.txt
.\.venv\Scripts\python.exe -m pip check
```

该依赖文件记录验证时的精确直接依赖版本，传递依赖未做完整哈希锁定。
不会自动加载 `.env`；真实模型凭据需要进入 API 进程的环境。

## 2. 无模型、无完整数据的检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_day05_analysis_plan.py tests/test_live_runtime.py
```

这组测试使用注入的假模型，检查结构化计划、服务装配、健康检查与歧义澄清，
不调用外部 API。结果展示可直接查看[已保存的离线响应](examples/monthly_gmv_response.json)
和[首页说明](../README.md)；完整查询链路需要下一步的数据准备。

## 3. 准备公开数据

从 [Olist 数据页面](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)下载原始包，
按平台要求登录并接受条款。数据采用 CC BY-NC-SA 4.0，保留数据来源署名。
将下载包保存为 `data/raw/archive.zip`，解压九个 CSV 到同一目录：

```text
data/raw/
  archive.zip
  olist_customers_dataset.csv
  olist_geolocation_dataset.csv
  olist_order_items_dataset.csv
  olist_order_payments_dataset.csv
  olist_order_reviews_dataset.csv
  olist_orders_dataset.csv
  olist_products_dataset.csv
  olist_sellers_dataset.csv
  product_category_name_translation.csv
```

在**新克隆目录**中构建六张核心表的数据库：

```powershell
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day03_pipeline
```

成功后会逐表输出 CSV 与数据库行数比对结果，数据库位于
`data/processed/olist.sqlite3`。下载文件和数据库被 Git 忽略；源文件清单及哈希见
[文件清单](../data/metadata/olist_file_manifest.csv)，字段说明见[数据字典](OLIST_DATA_DICTIONARY.md)。

## 4. 启动真实交互

在运行 API 的终端，通过本地环境配置 `DEEPSEEK_API_KEY`，不写入项目文件。
可以使用 PowerShell 隐藏输入将其仅设置到当前终端进程：

```powershell
$secureKey = Read-Host 'DeepSeek API Key' -AsSecureString
[System.Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', [System.Net.NetworkCredential]::new('', $secureKey).Password, 'Process')
Remove-Variable secureKey
```

模型和预算可以用非敏感环境变量设置；以下是当前默认值：

```powershell
$env:ECOMMERCE_AGENT_MODEL = 'deepseek-flash'
$env:ECOMMERCE_AGENT_MAX_HTTP_CALLS = '40'
$env:ECOMMERCE_AGENT_MAX_COST_USD = '0.15'
```

API 进程继承上述环境后，启动服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.ecommerce_agent.live_runtime:create_live_app --factory --host 127.0.0.1 --port 8000
```

另开终端进入同一项目目录，启动页面：

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

- 页面：`http://127.0.0.1:8501/`。
- API 文档：`http://127.0.0.1:8000/docs`。
- 健康检查：`GET /health`，只报告服务装配，不调用模型。
- 分析接口：`POST /analyze`，请求为 `{"question":"比较 2018 年 7 月与 6 月的已送达月度 GMV 环比。"}`。

页面提交分析会产生真实模型调用。共享预算账本位于
`data/processed/live_demo/api_budget_ledger.json`，跨请求与重启保留已用预算，
同时限制 HTTP 调用、输入/输出 token 和保守成本；已达限额时不会无限续跑。
不要将已用账本删除后把累计消费当作未发生。

建议演示两类问题：明确口径的月度比较，以及未定义“销售额”的澄清请求。
实际模型输出可能失败，结果与历史示例不保证一致。

## 5. 完整离线验收

完整测试和固定输入验收需要准备好的真实数据、数据库与冻结评测资产：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day15_offline_check
```

离线检查核对 Python 版本、题集与公开清单、数据库和全部原始文件 SHA-256，
并执行评分器回放、`pip check` 与 compileall。`accepted: true` 是复现验收结果，
不表示待评模型 100% 准确。若文件哈希不匹配，应确认数据版本和环境，保留原始资产。

原始真实模型运行文件没有随 Git 上传；保存的正式结果与逐题失败证据见
[结构化实验结果](DAY15_LIVE_RESULTS.json)。在新机器上重新生成完整实验需要另行
准备数据、模型凭据与预算，不属于离线检查。

Docker/Compose 的可选命令和未完成的实跑验证见[容器复现说明](DAY15_DOCKER_REPRODUCIBILITY.md)。
