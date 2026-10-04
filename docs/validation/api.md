# API 服务 验收：FastAPI 接口

## 1. 范围与结论

API 服务 在 Agent 工作流 状态机外增加薄 FastAPI 边界。HTTP 层只处理请求校验、服务调用、
状态映射、响应投影、健康检查和异常脱敏；检索、规划、SQL 安全、执行、有限修复、
确定性计算、展示和条件路由仍由原有模块负责。

验收结论：通过。最终全量测试、依赖一致性、字节码编译、离线 HTTP 批次以及数据库与
原始文件不变性均在本地复核通过。未调用外部模型，未读取或输出 API Key，未上传 GitHub。

## 2. 开始前实际核验

## 3. 编码前能力审计

### 已有能力

- `WorkflowState` 已提供顶层 `run_id`、枚举状态、停止原因、阶段产物、节点轨迹、
  SQL attempt trace 与 calculation trace，可作为服务层的内部输入输出合同。
- `AgentStateMachine` 已实现同步的完整业务工作流和所有停止条件；
  `LangGraphRunner` 是对同一 `step()` 的框架映射。
- SQL 修复 已独立拥有修复资格、预算、候选去重与 attempt lineage；API 无需也不应知道修复
  循环的内部实现。
- 确定性分析 已提供确定性表格、图表、结论与 calculation lineage。

### 需要实现

- 显式服务接口与 Agent 工作流 适配器。
- 严格请求/响应模型、应用工厂、`/health` 与 `/analyze`。
- 集中的工作流状态到 HTTP 状态码/响应体映射。
- 可替换依赖、假服务测试、异常脱敏和真实 SQLite 离线 HTTP 验收。

### 明确不做

- 不直接返回 `WorkflowState.to_dict()`。该字典含完整节点轨迹、检索上下文、计划、
  内部错误、SQL 安全/修复细节及潜在 Prompt/原始响应，不适合作为公开合同。
- 不在路由中复制状态机业务逻辑，不扩大 SQL 修复/Agent 工作流 循环预算。
- 不引入 Streamlit、Docker、冻结评测集 完整评测集、真实模型解释、认证、限流、多租户或
  checkpoint 恢复。

默认服务入口选择普通 Python `AgentStateMachine` 的服务适配器：它是已有主验收路径、依赖
更少、异常边界更直接；LangGraph 映射仍可通过同一 runner 协议替换，但 API 不依赖框架。
现有工作流是同步阻塞代码，因此 `/analyze` 使用同步 `def`，由 FastAPI 在线程池中执行；
没有用表面上的 `async def` 包装阻塞调用。`/health` 是轻量异步端点，不调用模型、不执行
完整 Agent，也不运行昂贵数据库查询。

## 4. 实现结构

- `analysis_service.py`：`WorkflowRunner`、`AnalysisService` 协议与
  `AgentService` 薄适配器。
- `api_models.py`：禁止额外字段的请求模型，以及成功、澄清、失败、校验错误、SQL、
  表格、图表、lineage 和元数据响应模型。
- `response_mapping.py`：集中状态映射与安全字段投影。
- `http_contract.py`：HTTP 映射结果及 mapper 协议。
- `api.py`：`create_app(service, response_mapper=None)` 应用工厂、依赖注入、端点和
  全局异常处理。
- `api_benchmark.py`：完全离线的 TestClient/真实 SQLite 验收批次。

`AnalyzeRequest.question` 会去除首尾空白，长度限制为 1～2000，额外字段被拒绝。服务收到
HTTP 边界生成的 `run_id`，返回状态必须保持同一个 ID，否则按内部合同破坏处理。成功响应
只投影最终 SQL attempt、命名参数、确定性表格/图表/结论、计算状态、停止原因、lineage 与
必要计数；默认不返回完整 trace、本地路径、Prompt、模型原始响应或内部异常文本。

## 5. 状态与 HTTP 语义

| Agent 工作流 状态 | HTTP | 公开语义 |
| --- | ---: | --- |
| `succeeded` | 200 | 工作流完成；计算状态仍单独表达缺比较期、零基期等业务可计算性 |
| `needs_clarification` | 200 | 正常交互分支，返回 `clarification_question`，SQL 为 `null` |
| `safety_rejected` | 422 | 查询未通过安全策略，未执行且未修复 |
| `retrieval_failed` | 500 | 检索阶段受控失败 |
| `planning_failed` | 502 | 规划依赖未产生可用结果 |
| `sql_generation_failed` | 502 | SQL 生成依赖失败 |
| `resource_failed` | 503 | 超时或资源限制，可重试 |
| `environment_failed` | 503 | 数据库或运行环境不可用，可重试 |
| `execution_failed` | 500 | 未知或不可修复执行失败 |
| `repair_failed` | 502 | 修复依赖失败 |
| `repair_limit_reached` | 500 | 有限修复预算耗尽 |
| `calculation_failed` | 500 | 确定性计算失败，不生成伪结论 |
| `presentation_failed` | 500 | 确定性展示失败 |
| `internal_failed` | 500 | 内部合同或未知异常，返回固定脱敏信息 |

映射只读取枚举状态和结构化 `stop_reason`，不从异常字符串猜测业务状态。请求校验统一为
422 `invalid_request`，只公开字段位置和错误类型；未知异常统一为 500 `internal_failed`。

## 6. 依赖与测试策略

新增运行依赖：

- `fastapi>=0.141,<0.142`
- `uvicorn>=0.46,<0.47`

项目已有并保留 `pydantic>=2.12,<3.0`、`httpx>=0.28,<1.0` 与
`pytest>=8.0,<9.0`。实际环境为 FastAPI 0.141.1、Starlette 1.6.0、Uvicorn 0.46.0、
HTTPX 0.28.1、Pydantic 2.13.5。应用工厂接收服务接口；测试用假服务或替换依赖，不把
假模型 fixture 写入生产路由，也不使用共享全局可变服务。

## 7. 离线 API 验收证据

`docs/reports/api.json` 保存 15 个助手机械案例，15/15 符合预设 HTTP 与状态结果：

- 9 例调用完整 Agent 工作流 状态机；其中 6 例实际进入本地 SQLite 执行。
- 澄清和安全拒绝在 SQLite 前停止；缺失数据库验证环境失败不进入修复。
- 2 例使用脚本化终态，专门验证资源失败与未知执行失败的 HTTP 投影。
- 1 例假服务抛出未处理异常，验证固定脱敏 500。
- 3 例验证空问题、额外字段与错误 JSON 的稳定 422。
- 修复成功保留同一顶层 `run_id` 并返回第二个最终 SQL attempt；修复上限保持独立状态。
- 缺失比较期与零基期作为确定性 calculation status 返回，不伪装成服务器异常。
- 所有响应的敏感标记检查通过；外部 API 调用 0，模型生成数值 0。

这些案例的假规划、假 SQL、假修复响应、期望状态和测试均由助手机械编写。它们验证接口、
状态机、SQLite 和确定性计算的集成行为，不是用户独立判断，也没有独立业务参考结果；全部
完整工作流案例标记为 `not_independently_evaluated`；纯 API 合同案例标记为
`not_applicable_api_contract`。

## 8. 最终复验

- 全量 pytest：311 passed。
- `pip check`：无依赖冲突。
- `python -m compileall -q src tests`：通过。
- 离线 API 批次：15/15 符合预期。
- 数据库 SHA-256 复核不变；`data/raw/` 十个原始文件哈希复核不变。
- 当前实现未调用外部 API，未生成模型数值，未修改原始数据。