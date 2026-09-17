"""FastAPI application factory for the Day 12 HTTP boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.ecommerce_agent.day12_api_models import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnalyzeErrorResponse,
    ApiErrorDetail,
    HealthResponse,
    PublicFailureStatus,
    RequestValidationErrorResponse,
    RunMetadata,
    ValidationIssue,
)
from src.ecommerce_agent.day12_http import WorkflowHttpMapper
from src.ecommerce_agent.day12_mapping import Day12WorkflowHttpMapper
from src.ecommerce_agent.day12_service import AnalysisService


async def get_analysis_service(request: Request) -> AnalysisService:
    return request.app.state.analysis_service


async def get_workflow_http_mapper(request: Request) -> WorkflowHttpMapper:
    return request.app.state.workflow_http_mapper


def create_app(
    service: AnalysisService,
    response_mapper: WorkflowHttpMapper | None = None,
) -> FastAPI:
    """Create an isolated app without probing models or the database."""
    if not callable(getattr(service, "analyze", None)):
        raise TypeError("service 必须实现可调用的 analyze 方法")
    mapper = response_mapper or Day12WorkflowHttpMapper()
    if not callable(getattr(mapper, "map", None)):
        raise TypeError("response_mapper 必须实现可调用的 map 方法")

    app = FastAPI(
        title="Cross-platform E-commerce Analytics Agent",
        version="0.12.0",
    )
    app.state.analysis_service = service
    app.state.workflow_http_mapper = mapper

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.run_id = uuid4().hex
        request.state.created_at = datetime.now(timezone.utc)
        request.state.started_at = perf_counter()
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        issues = tuple(
            ValidationIssue(
                location=tuple(item["loc"]),
                error_type=str(item["type"]),
            )
            for item in error.errors()
        )
        body = RequestValidationErrorResponse(
            run_id=request.state.run_id,
            error=ApiErrorDetail(
                code="invalid_request",
                message="请求体不符合 /analyze 输入合同。",
                retryable=False,
            ),
            issues=issues,
        )
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(body),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        del error  # Raw exception text and traceback are not public API data.
        created_at = getattr(
            request.state,
            "created_at",
            datetime.now(timezone.utc),
        )
        started_at = getattr(request.state, "started_at", perf_counter())
        body = AnalyzeErrorResponse(
            status=PublicFailureStatus.INTERNAL_FAILED,
            run_id=getattr(request.state, "run_id", uuid4().hex),
            stop_reason="unhandled_exception",
            error=ApiErrorDetail(
                code="internal_failed",
                message="分析服务发生内部错误。",
                retryable=False,
            ),
            metadata=RunMetadata(
                created_at=created_at,
                workflow_duration_ms=max(
                    0.0,
                    (perf_counter() - started_at) * 1000,
                ),
                node_count=0,
                sql_attempt_count=0,
                repair_attempt_count=0,
                execution_started=False,
                rows_truncated=False,
            ),
        )
        return JSONResponse(
            status_code=500,
            content=jsonable_encoder(body),
        )

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Shallow application health check",
    )
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.post(
        "/analyze",
        response_model=AnalyzeResponse,
        responses={422: {"model": RequestValidationErrorResponse}},
        tags=["analysis"],
        summary="Run the complete analytics workflow",
    )
    def analyze(
        payload: AnalyzeRequest,
        request: Request,
        response: Response,
        service_dependency: Annotated[
            AnalysisService,
            Depends(get_analysis_service),
        ],
        mapper_dependency: Annotated[
            WorkflowHttpMapper,
            Depends(get_workflow_http_mapper),
        ],
    ) -> AnalyzeResponse:
        run_id = request.state.run_id
        state = service_dependency.analyze(payload.question, run_id=run_id)
        if state.run_id != run_id:
            raise RuntimeError("分析服务返回了不一致的 run_id")
        mapped = mapper_dependency.map(state)
        response.status_code = mapped.status_code
        return mapped.body

    return app
