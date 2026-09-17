# Day 13 验收：Streamlit、测试与用户体验

## 1. 范围与结论

Day 13 在 Day 12 稳定公开合同之上增加薄 Streamlit 页面。页面负责输入、一次提交、
session state、API 客户端调用和公开结果展示；没有重写检索、规划、SQL 安全、执行、
修复、确定性计算、Day 11 路由或 Day 12 状态到 HTTP 的映射。

验收结论：工程与离线机械验收通过。最终全量测试、依赖一致性、字节码编译、离线
Streamlit/API 批次和数据不变性均通过。没有外部模型调用，没有读取或输出 API Key，
没有上传 GitHub。用户明确提供的 Day 13 实际学习时间为 2 小时；不按助手编码、
测试、依赖下载或对话等待时间估算。

## 2. 开始前实测

- 分支 `main`，开始时工作区干净；HEAD 为
  `aa47c73 feat: complete Day 12 FastAPI interface`。
- Day 11 功能提交为
  `7c33124 feat: complete Day 11 state machine orchestration`。
- 项目独立 `.venv` 为 Python 3.11.9。
- 开始前全量实跑 `311 passed in 115.33s`；`pip check` 无冲突。
- Day 12 结果文件为 15/15：9 个完整状态机案例、6 个真实 SQLite 执行、
  2 个脚本终态、1 个未处理异常和 3 个请求校验。
- Day 12 外部 API 调用与模型生成数值均为 0；用户明确提供的 Day 12 实际学习时间
  为 2 小时。
- SQLite 为六张核心表、38 字段；SHA-256 为
  `ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c`。
- `data/raw/` 的 `archive.zip` 和九个 CSV 全部与既有清单匹配。
- 开始时未安装 Streamlit 或 Plotly。新增并实测 Streamlit 1.50.0；
  `requirements.txt` 约束为 `streamlit>=1.50,<1.51`。图表使用 Streamlit/Altair
  原生能力，没有增加不必要的 Plotly 依赖。

## 3. 编码前审计结论

- 页面直接消费 `AnalyzeSuccessResponse`、`AnalyzeClarificationResponse` 和
  `AnalyzeErrorResponse`；请求校验响应也由客户端做严格解析。
- 页面只依赖 Day 12 API，不直接调用 Day 11。否则会绕过公开字段投影、集中 HTTP
  映射、run_id 边界和异常脱敏。
- session state 只保存页面阶段、当前提交 token、最后问题和已验证的公开结果。
  API Key、原始响应、异常对象、Prompt、模型响应、trace 和本地路径不作为页面状态。
- `st.form_submit_button` 只在显式点击时为真；`PageState.begin()` 的 submitting 状态
  和 token 阻止同一执行中的重复提交，普通重运行只重画已有结果。
- 客户端返回自定义 `ApiResponse | ApiClientFailure`。前者同时保留 HTTP 状态和严格
  Pydantic 联合响应；后者只含固定公开文案，不返回裸字典或异常字符串。
- 网络、超时、非 JSON 和合同不匹配是客户端失败；有效 API 响应里的失败是工作流
  状态；`calculation_status` 是成功工作流里的确定性计算状态。三者分别展示。
- 澄清是 HTTP 200 的预期交互分支，不是系统错误；必须有问题文本且不得显示 SQL。
- chart 数据可直接消费，但需要 DataFrame 结构适配。适配只选择声明的 x/y 字段，
  不聚合、不补零、不计算增长率、不改写数值。
- 完整 trace 不展示。公开合同已经给出 run_id、lineage 和必要计数；内部 trace 可能
  含检索上下文、路径、Prompt、模型响应和异常细节，也不适合非开发者界面。
- 完全离线测试使用假客户端 AppTest、FastAPI TestClient 进程内传输、假模型响应和
  本地 SQLite，不启动真实模型或外部网络。

## 4. 实现结构

- `streamlit_app.py`：可由 `streamlit run` 启动的页面入口。
- `day13_page_state.py`：与框架无关的空闲/提交中/完成状态及一次提交 token。
- `day13_api_client.py`：可替换客户端协议、HTTP 实现、30 秒默认超时、严格合同解析及
  四种固定脱敏客户端失败。
- `day13_view_models.py`：成功展示投影、命名参数、表格与 chart DataFrame 适配。
- `day13_error_views.py`：工作流失败的用户标题和下一步说明，不包含 HTTP 映射规则。
- `day13_streamlit.py`：表单、session state、组件分发和成功/澄清/失败布局。
- `day13_benchmark.py`：离线 AppTest/API/完整状态机/真实 SQLite 验收和不变性检查。

页面成功结果包括 workflow status、顶层 `run_id`、结论、最终 SQL、命名参数、表格、
图表、calculation status、stop reason、attempt 计数、执行标记、截断标记和耗时。
Day 12 公开合同没有暴露分析计划；Day 13 没有为满足界面而绕过 API 读取内部状态。

## 5. 状态与错误体验

- `needs_clarification`：信息提示和 `clarification_question`；无 SQL、表格或图表。
- `safety_rejected`：明确说明未执行 SQL、未进入自动修复。
- `resource_failed`：资源/时间限制，可缩小范围后重试。
- `environment_failed`：数据库或运行环境不可用。
- `repair_limit_reached`：达到有限修复上限后停止。
- `calculation_failed`：SQL 可能完成，但确定性计算没有完成，不展示业务结论。
- `internal_failed`：固定内部错误提示，明确隐藏异常与堆栈。
- `missing_comparison_period`、`zero_baseline`：保留为成功页计算状态，分别说明不补零和
  相对变化不可计算，不伪装成服务器错误。
- timeout、network、non-JSON、contract mismatch：四种独立客户端失败，均使用固定
  脱敏文案。

## 6. 测试边界与结果

Day 13 新增 26 个助手机械 pytest 项：

- 纯单元测试：页面状态、一次提交、展示投影、chart/table 值保持、客户端超时和合同
  解析。
- Streamlit AppTest：输入、提交、普通重运行、成功组件、澄清、失败、缺比较期、
  零基期和敏感标记。
- API—前端集成：AppTest 注入 `Day13ApiClient`，经 FastAPI TestClient 实际调用
  Day 12 `/analyze`，验证只调用一次及 run_id 一致。
- 真实 SQLite：离线验收通过 Day 12 API 调用完整 Day 11 假模型状态机，并读取本地
  Olist SQLite。

最终全量结果：`337 passed in 145.35s`；`pip check` 无冲突；`compileall` 通过。
保留一条 Starlette/anyio 上游弃用警告；它来自 TestClient 依赖，不影响合同结果。

离线验收共 14 个 AppTest 案例，14/14 符合预设：

- 5 个经 Day 12 API 的完整 Day 11 假模型状态机案例；
- 其中 3 个实际进入真实 SQLite：正常成功、缺失比较期、零基期；
- 5 个脚本化公开失败响应：资源、环境、修复上限、计算、内部；
- 4 个脚本化客户端失败：超时、网络、非 JSON、合同不匹配；
- 外部 API 调用和模型生成数值均为 0；
- 所有可见组件均未出现诱饵本地路径、API Key、Prompt、模型原始响应或 Traceback。

这些是助手机械样本和机械期望。用户独立完成状态为 `false`；不存在独立业务参考结果，
所有业务案例为 `not_independently_evaluated`，不能表述为业务准确率。

## 7. 数据与边界复核

- 验收前后数据库 SHA-256 均为
  `ef08ccb7cf6ca5cc96e1ee56c15af0af1f3a0af159985b6461ea38e1ecfa675c`。
- `data/raw/` 的十个清单文件前后哈希完全一致。
- 指标字典仍是唯一口径来源；客户、订单明细、支付预聚合、品类支付拆分限制、销售额
  歧义和不完整月份边界未改变。
- 页面没有改变 Day 8 安全失败路径、Day 9 三套 attempt 计数、Day 10 数值来源、
  Day 11 run_id/停止条件或 Day 12 HTTP 映射。
- 未实现 Docker、Day 14 评测集、真实模型解释、新 RAG、多 Agent、自动归因、认证、
  限流、多租户或生产部署。

复现命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m src.ecommerce_agent.day13_benchmark
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q src tests streamlit_app.py
```
