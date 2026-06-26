"""
FastAPI 应用主模块

定义应用生命周期、中间件、路由和异常处理。
"""

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from harness.core.config import Config, get_config
from harness.core.exceptions import HarnessError
from harness.core.logger import get_logger, setup_logger


logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期管理

    启动时初始化日志系统，关闭时清理资源。
    """
    # 启动
    config = get_config()
    logger.info(
        "[lifespan] Application startup",
        name=config.app.name,
        version=config.app.version,
        environment=config.app.environment,
    )

    # 确保日志系统已设置
    setup_logger()

    # 初始化数据库
    try:
        from harness.core.database import init_db
        init_db()
        logger.info("[lifespan] Database initialized")
    except Exception as e:
        logger.error("[lifespan] Failed to initialize database", error=str(e))

    # 初始化Agent系统
    try:
        from harness.agent import initialize_agent
        await initialize_agent()
        logger.info("[lifespan] Agent system initialized")
    except Exception as e:
        logger.error("[lifespan] Failed to initialize Agent system", error=str(e))

    # 启动飞书通道
    try:
        from harness.core.feishu_config import get_feishu_config, is_feishu_configured, list_channels
        feishu_cfg = get_feishu_config()

        if feishu_cfg.get("feishu.enabled", "false").lower() == "true":
            channels = list_channels()
            if channels:
                # 多通道模式：仅启动 ChannelManager
                from harness.services.feishu_client import get_channel_manager
                manager = get_channel_manager()
                count = manager.start_all()
                logger.info("[lifespan] Feishu multi-channel started", count=count)
            elif is_feishu_configured():
                # 单通道兼容模式：无多通道配置时使用默认通道
                from harness.services.feishu_client import get_feishu_client
                client = get_feishu_client()
                client.start()
                logger.info("[lifespan] Feishu single-channel started (compat)")
            else:
                logger.info("[lifespan] Feishu enabled but not configured")
        else:
            logger.info("[lifespan] Feishu channel not enabled")
    except Exception as e:
        logger.error("[lifespan] Failed to start Feishu channel", error=str(e))

    # 启动定时任务调度器
    try:
        from harness.services.scheduler import get_scheduler
        scheduler = get_scheduler()
        scheduler.start()
        logger.info("[lifespan] Scheduler started")
    except Exception as e:
        logger.error("[lifespan] Failed to start scheduler", error=str(e))

    yield

    # 关闭飞书 Channel
    try:
        from harness.services.feishu_client import get_feishu_client, get_channel_manager
        get_feishu_client().stop()
        get_channel_manager().stop_all()
    except Exception:
        pass

    # 关闭调度器
    try:
        from harness.services.scheduler import get_scheduler
        get_scheduler().shutdown()
    except Exception:
        pass

    # 关闭
    logger.info("[lifespan] Application shutdown")


def setup_middleware(app: FastAPI) -> None:
    """
    配置中间件

    添加 CORS 和 HTTP 请求日志中间件。
    """
    # 配置CORS中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 开发环境允许所有来源
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next) -> JSONResponse:
        """
        记录所有 HTTP 请求及处理时间
        """
        start_time = time.time()

        # 记录请求
        logger.info(
            "[request] Incoming request",
            method=request.method,
            path=request.url.path,
            client=str(request.client) if request.client else None,
        )

        # 处理请求
        try:
            response = await call_next(request)
        except Exception as e:
            logger.error(
                "[request] Request processing error",
                method=request.method,
                path=request.url.path,
                error=str(e),
            )
            raise

        # 计算处理时间
        process_time = time.time() - start_time

        # 记录响应
        logger.info(
            "[request] Request completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            process_time_ms=round(process_time * 1000, 2),
        )

        # 添加处理时间到响应头
        response.headers["X-Process-Time"] = str(process_time)

        return response


def setup_routes(app: FastAPI, config: Config) -> None:
    """
    配置路由

    添加健康检查和信息端点。
    """
    @app.get("/health", tags=["health"])
    async def health_check() -> dict:
        """
        健康检查端点

        返回应用状态信息。
        """
        return {
            "status": "healthy",
            "app": config.app.name,
            "version": config.app.version,
            "environment": config.app.environment,
        }

    @app.get("/api/v1/info", tags=["info"])
    async def app_info() -> dict:
        """
        应用信息端点

        返回详细的应用配置信息。
        """
        return {
            "name": config.app.name,
            "version": config.app.version,
            "description": config.app.description,
            "environment": config.app.environment,
            "debug": config.app.debug,
        }

    # 注册通达信数据路由
    from harness.api.routers import tdx_router
    app.include_router(tdx_router)

    # 注册GLM服务路由
    from harness.api.routers import glm_router
    app.include_router(glm_router)

    # 注册Agent服务路由
    from harness.api.routers import agent_router
    app.include_router(agent_router)

    # 注册策略回测路由
    from harness.api.routers import backtest_router
    app.include_router(backtest_router)

    # 注册配置管理路由
    from harness.api.routers import settings_router
    app.include_router(settings_router)

    # 注册监控路由
    from harness.api.routers import monitor_router
    app.include_router(monitor_router)

    # 注册 Channel 管理路由
    from harness.api.routers import channel_router
    app.include_router(channel_router)

    # 注册定时任务路由
    from harness.api.routers import scheduler_router
    app.include_router(scheduler_router)

    # 注册自选股路由
    from harness.api.routers import watchlist_router
    app.include_router(watchlist_router)

def setup_exception_handlers(app: FastAPI) -> None:
    """
    配置异常处理器

    处理 HarnessError 和通用异常。
    """

    @app.exception_handler(HarnessError)
    async def harness_error_handler(
        request: Request, exc: HarnessError
    ) -> JSONResponse:
        """
        处理 HarnessError 异常
        """
        logger.error(
            "[exception] HarnessError occurred",
            message=exc.message,
            error_code=exc.error_code,
            path=request.url.path,
        )

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                }
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """
        处理通用异常
        """
        logger.error(
            "[exception] Unexpected error occurred",
            error=str(exc),
            error_type=type(exc).__name__,
            path=request.url.path,
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred. Please try again later.",
                }
            },
        )


def create_app() -> FastAPI:
    """
    创建并配置 FastAPI 应用

    Returns:
        FastAPI: 配置好的应用实例
    """
    # 获取配置
    config = get_config()

    # 创建应用
    app = FastAPI(
        title=config.app.name,
        description=config.app.description,
        version=config.app.version,
        docs_url="/docs" if config.app.debug else None,
        redoc_url="/redoc" if config.app.debug else None,
        lifespan=lifespan,
    )

    # 配置中间件
    setup_middleware(app)

    # 配置路由
    setup_routes(app, config)

    # 配置异常处理
    setup_exception_handlers(app)

    logger.info("[create_app] Application created successfully")

    return app
