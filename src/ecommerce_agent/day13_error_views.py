"""User-facing semantics for validated failure statuses, never HTTP mapping."""

from __future__ import annotations

from dataclasses import dataclass

from src.ecommerce_agent.day12_api_models import PublicFailureStatus


@dataclass(frozen=True)
class FailureView:
    title: str
    guidance: str
    level: str


FAILURE_VIEWS: dict[PublicFailureStatus, FailureView] = {
    PublicFailureStatus.SAFETY_REJECTED: FailureView(
        "安全策略拒绝",
        "查询未通过只读安全策略；本次不执行 SQL，也不进入自动修复。",
        "warning",
    ),
    PublicFailureStatus.RETRIEVAL_FAILED: FailureView(
        "指标与字段检索失败",
        "分析所需的指标或字段上下文未能准备完成。",
        "error",
    ),
    PublicFailureStatus.PLANNING_FAILED: FailureView(
        "分析计划失败",
        "没有生成通过验证的分析计划，可稍后重试或调整问题表述。",
        "error",
    ),
    PublicFailureStatus.SQL_GENERATION_FAILED: FailureView(
        "查询生成失败",
        "没有生成通过合同验证的查询。",
        "error",
    ),
    PublicFailureStatus.RESOURCE_FAILED: FailureView(
        "资源限制",
        "查询受到时间或资源限制，未完成；可缩小范围后重试。",
        "warning",
    ),
    PublicFailureStatus.ENVIRONMENT_FAILED: FailureView(
        "运行环境不可用",
        "数据库或必要运行环境当前不可用，请恢复环境后重试。",
        "error",
    ),
    PublicFailureStatus.EXECUTION_FAILED: FailureView(
        "查询执行失败",
        "查询未能受控完成，页面没有生成业务结论。",
        "error",
    ),
    PublicFailureStatus.REPAIR_FAILED: FailureView(
        "查询修复失败",
        "有限修复流程没有产生可接受的安全查询。",
        "error",
    ),
    PublicFailureStatus.REPAIR_LIMIT_REACHED: FailureView(
        "已达到修复上限",
        "查询在允许的修复次数内仍未成功，系统已停止继续尝试。",
        "error",
    ),
    PublicFailureStatus.CALCULATION_FAILED: FailureView(
        "确定性计算失败",
        "SQL 阶段可能已经完成，但确定性计算未完成，因此不展示业务结论。",
        "error",
    ),
    PublicFailureStatus.PRESENTATION_FAILED: FailureView(
        "结果整理失败",
        "分析结果未能转换成稳定的公开展示结构。",
        "error",
    ),
    PublicFailureStatus.INTERNAL_FAILED: FailureView(
        "内部错误",
        "分析服务发生内部错误；页面已隐藏异常细节和调用堆栈。",
        "error",
    ),
}


CALCULATION_NOTICES: dict[str, str] = {
    "missing_comparison_period": (
        "缺少精确比较期，确定性计算保留该状态，没有补零或改用相邻记录。"
    ),
    "zero_baseline": (
        "比较期为零，相对变化不可计算；这不是服务器错误。"
    ),
    "incomplete_period": (
        "至少一个期间不完整，结果不应包装为标准同比或环比。"
    ),
}

