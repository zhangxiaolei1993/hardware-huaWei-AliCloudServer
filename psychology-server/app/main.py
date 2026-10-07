"""psychology-server 入口：FastAPI 应用组装。

架构：通用 Device + 通用 Session + 独立业务 API（当前仅 emotion）。
未来新增业务（心率 / EEG / 眼动）时，新增各自的 api/models/schemas/services 即可。

文档：
- 使用说明总览页：GET /
- 交互式 API 文档（Swagger UI）：GET /docs
- 备用文档（ReDoc）：GET /redoc
- OpenAPI 原始描述：GET /openapi.json
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import devices, emotion, health, sessions
from app.api.docs_page import router as docs_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.reaper import SessionReaper
from app.db.database import init_db

setup_logging()
logger = get_logger(__name__)

# OpenAPI 标签分组（顺序即文档中显示顺序）
OPENAPI_TAGS: List[Dict[str, str]] = [
    {
        "name": "docs",
        "description": "使用说明总览页。",
    },
    {
        "name": "health",
        "description": "健康检查，用于探活与负载均衡探测。",
    },
    {
        "name": "devices",
        "description": "**通用设备管理**：设备注册、查询、心跳。"
        "所有类型设备（当前 Atlas，未来心率/EEG 设备）共用。",
    },
    {
        "name": "sessions",
        "description": "**通用会话管理**：一次测评/采集过程的创建、查询、"
        "状态流转（created → running → completed）。",
    },
    {
        "name": "emotion",
        "description": "**表情识别业务（当前唯一业务）**：测评结束后批量上传原始 timeline，"
        "由服务器重新计算最终统计结果。",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_db()
    reaper = SessionReaper()
    reaper.start()
    logger.info(
        "%s started: env=%s port=%d", settings.app_name, settings.env, settings.port
    )
    yield
    reaper.stop()
    logger.info("%s stopped", settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(
        title="psychology-server 心理测评云平台",
        description=(
            "多设备心理测评云平台后端。\n\n"
            "**架构**：通用 Device 管理 + 通用 Session 管理 + 独立业务 API。\n\n"
            "- 当前业务：华为 Atlas 200I DK A2 + 摄像头 + 表情识别\n"
            "- 未来新增心率 / EEG / 眼动时，各自使用独立的 API、Schema、Service 与数据表，"
            "复用 Device / Session 层，互不影响\n\n"
            "**设备认证**：除注册、健康检查外，设备接口需在请求头携带 "
            "`X-Device-Id` 与 `X-Device-Token`（token 在注册时返回）。\n\n"
            "更多说明见根路径 `/`。"
        ),
        version="0.1.0",
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # 统一 JSON 返回结构：{"code", "message", "data"}
    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning("validation error on %s: %s", request.url.path, exc.errors()[:3])
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": 422,
                "message": "validation error",
                "data": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"code": 500, "message": "internal server error", "data": None},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        logger.info("http error on %s: %d %s", request.url.path, exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "data": None},
            headers=exc.headers if exc.headers else None,
        )

    # 使用说明总览页
    app.include_router(docs_router)

    # 业务路由
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(devices.router, prefix="/api/v1")
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(emotion.router, prefix="/api/v1")

    return app


app = create_app()
