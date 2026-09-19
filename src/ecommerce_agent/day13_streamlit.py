"""Testable Streamlit entry point for the Day 13 user interface."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from src.ecommerce_agent.day12_api_models import (
    AnalyzeClarificationResponse,
    AnalyzeErrorResponse,
    AnalyzeSuccessResponse,
    RequestValidationErrorResponse,
)
from src.ecommerce_agent.day13_api_client import (
    AnalysisApiClient,
    ApiClientFailure,
    ApiCallResult,
    ApiResponse,
    create_http_api_client,
)
from src.ecommerce_agent.day13_error_views import (
    CALCULATION_NOTICES,
    FAILURE_VIEWS,
)
from src.ecommerce_agent.day13_page_state import PagePhase, PageState
from src.ecommerce_agent.day13_view_models import (
    build_chart_view,
    build_success_view,
    build_table_frame,
)


PAGE_STATE_KEY = "_day13_page_state"
API_CLIENT_KEY = "_day13_api_client"
API_URL_ENV = "ECOMMERCE_AGENT_API_URL"


@st.cache_resource
def _default_client(base_url: str) -> AnalysisApiClient:
    return create_http_api_client(base_url)


def _get_client(explicit_client: AnalysisApiClient | None) -> AnalysisApiClient:
    if explicit_client is not None:
        return explicit_client
    injected = st.session_state.get(API_CLIENT_KEY)
    if injected is not None:
        return injected
    return _default_client(
        os.getenv(API_URL_ENV, "http://127.0.0.1:8000")
    )


def _get_page_state() -> PageState[ApiCallResult]:
    state = st.session_state.get(PAGE_STATE_KEY)
    if isinstance(state, PageState):
        return state
    state = PageState[ApiCallResult]()
    st.session_state[PAGE_STATE_KEY] = state
    return state


def render_result(result: ApiCallResult) -> None:
    if isinstance(result, ApiClientFailure):
        st.error(result.public_message)
        st.caption(
            f"客户端状态：{result.kind.value} · "
            f"可重试：{'是' if result.retryable else '否'}"
        )
        return
    payload = result.payload
    if isinstance(payload, AnalyzeSuccessResponse):
        _render_success(payload)
    elif isinstance(payload, AnalyzeClarificationResponse):
        st.info("需要补充信息")
        st.write(payload.clarification_question)
        st.caption(
            f"工作流状态：{payload.status} · Run ID：{payload.run_id} · "
            f"Stop reason：{payload.stop_reason}"
        )
    elif isinstance(payload, AnalyzeErrorResponse):
        view = FAILURE_VIEWS[payload.status]
        getattr(st, view.level)(view.title)
        st.write(view.guidance)
        st.caption(
            f"工作流状态：{payload.status.value} · Run ID：{payload.run_id} · "
            f"Stop reason：{payload.stop_reason}"
        )
        st.caption(
            f"已进入执行：{'是' if payload.metadata.execution_started else '否'} · "
            f"SQL attempts：{payload.metadata.sql_attempt_count} · "
            f"修复 attempts：{payload.metadata.repair_attempt_count} · "
            f"可重试：{'是' if payload.error.retryable else '否'}"
        )
    elif isinstance(payload, RequestValidationErrorResponse):
        st.warning("提交内容未通过 API 请求合同校验，请检查问题后重试。")
        st.caption(f"请求状态：invalid_request · Run ID：{payload.run_id}")


def _render_success(response: AnalyzeSuccessResponse) -> None:
    view = build_success_view(response)
    st.success("分析完成")
    st.caption(
        f"工作流状态：{view.workflow_status} · Run ID：{view.run_id}"
    )
    st.subheader("结论")
    st.write(view.conclusion)
    notice = CALCULATION_NOTICES.get(view.calculation_status)
    if notice is not None:
        st.info(notice)

    st.subheader("结果表")
    st.dataframe(
        build_table_frame(response.table),
        hide_index=True,
        width="stretch",
    )

    st.subheader("图表")
    try:
        chart = build_chart_view(response.chart)
    except ValueError:
        st.error("图表数据与展示合同不一致，当前无法安全绘制。")
    else:
        st.caption(chart.title)
        if not chart.renderable:
            st.info("当前结果是单值或缺少独立横轴，保留结果表而不强制绘图。")
        elif chart.chart_type == "bar":
            st.bar_chart(
                chart.frame,
                x=chart.x_field,
                y=list(chart.y_fields),
                width="stretch",
            )
        elif chart.chart_type == "line":
            st.line_chart(
                chart.frame,
                x=chart.x_field,
                y=list(chart.y_fields),
                width="stretch",
            )
        if chart.notes:
            st.caption("图表说明：" + "；".join(chart.notes))

    with st.expander("查询 SQL 与命名参数", expanded=True):
        st.code(view.sql_statement, language="sql")
        parameter_rows = [
            {"参数名": item.name, "参数值": item.value}
            for item in view.parameters
        ]
        if parameter_rows:
            st.dataframe(
                pd.DataFrame(parameter_rows),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("本次查询没有命名参数。")
        st.caption(
            f"SQL attempt：{view.sql_attempt} · "
            f"是否修复：{'是' if view.repaired else '否'}"
        )

    st.subheader("运行信息")
    first, second, third = st.columns(3)
    first.metric("Calculation status", view.calculation_status)
    second.metric("Stop reason", view.stop_reason)
    third.metric("工作流耗时", f"{view.workflow_duration_ms:.1f} ms")
    st.caption(
        f"节点 {view.node_count} · SQL attempts {view.sql_attempt_count} · "
        f"修复 attempts {view.repair_attempt_count} · "
        f"已进入执行：{'是' if view.execution_started else '否'} · "
        f"结果截断：{'是' if view.rows_truncated else '否'}"
    )


def run_page(client: AnalysisApiClient | None = None) -> None:
    st.set_page_config(
        page_title="电商经营分析 Agent",
        page_icon="📊",
        layout="wide",
    )
    st.title("跨平台电商经营分析 Agent")
    st.write("输入经营问题，页面将通过稳定 API 合同运行一次分析。")

    state = _get_page_state()
    with st.form("analysis_form", clear_on_submit=False):
        question = st.text_area(
            "经营问题",
            placeholder="例如：比较 2018 年 7 月与 6 月的已送达商品 GMV。",
            max_chars=2000,
        )
        submitted = st.form_submit_button(
            "开始分析",
            disabled=state.phase is PagePhase.SUBMITTING,
            type="primary",
        )

    if submitted:
        pending, submission = state.begin(question)
        st.session_state[PAGE_STATE_KEY] = pending
        if submission is None:
            st.warning("请输入经营问题后再提交。")
        else:
            with st.spinner("正在分析，请稍候……"):
                result = _get_client(client).analyze(submission.question)
            state = pending.finish(submission, result)
            st.session_state[PAGE_STATE_KEY] = state

    state = _get_page_state()
    if state.result is not None:
        render_result(state.result)
