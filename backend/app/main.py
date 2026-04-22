from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.core.logger import log
from app.core.middleware import LoggingMiddleware
from app.core.exceptions import AppException
from app.api import chat, health, conversations, messages, memory
import uvicorn
import os
from pathlib import Path

def create_app() -> FastAPI:
    """创建FastAPI应用实例"""

    # 定义 API 标签的中文描述，显示在接口列表页
    openapi_tags = [
        {
            "name": "健康检查",
            "description": "服务状态监控与应用基本信息查询"
        },
        {
            "name": "聊天",
            "description": "与 AI Agent 进行对话，支持流式与非流式模式"
        },
        {
            "name": "会话管理",
            "description": "管理对话会话的创建、查询、更新与删除，支持消息历史查看"
        },
        {
            "name": "消息",
            "description": "流式消息发送与 SSE 实时响应接收"
        },
        {
            "name": "记忆管理",
            "description": "长期记忆的检索、配置管理与清理（基于 PostgreSQL 向量存储）"
        },
    ]

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="企业级 AI Agent 后端 API，支持多模型对话、会话管理、向量记忆等功能",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=openapi_tags
    )

    # 配置CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 添加日志中间件
    app.add_middleware(LoggingMiddleware)

    # 注册路由
    app.include_router(health.router)
    app.include_router(chat.router, prefix=settings.API_PREFIX)
    app.include_router(conversations.router, prefix=f"{settings.API_PREFIX}/conversations")
    app.include_router(messages.router, prefix=settings.API_PREFIX)
    app.include_router(memory.router, prefix=settings.API_PREFIX)

    # 配置静态文件服务 (前端构建文件)
    frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        # 挂载静态资源目录
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")
        
        # 处理前端路由,返回index.html
        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str):
            """服务前端静态文件"""
            # 如果请求的是文件且存在,返回文件
            file_path = frontend_dist / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            # 否则返回index.html (SPA路由)
            return FileResponse(frontend_dist / "index.html")
        
        log.info(f"前端静态文件服务已启用: {frontend_dist}")
    else:
        log.warning(f"前端构建文件不存在: {frontend_dist}")

    # 全局异常处理
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        log.error(f"应用异常: {exc.message}", extra={"details": exc.details})
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.message,
                "details": exc.details
            }
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        log.error(f"未处理的异常: {str(exc)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": "内部服务器错误",
                "detail": str(exc) if settings.DEBUG else "请联系管理员"
            }
        )

    # 启动事件
    @app.on_event("startup")
    async def startup_event():
        log.info(f"应用启动: {settings.APP_NAME} v{settings.APP_VERSION}")
        log.info(f"API文档: http://{settings.API_HOST}:{settings.API_PORT}/docs")
        # 初始化向量记忆服务
        from app.services.vector_memory_service import vector_memory_service
        await vector_memory_service.initialize()

    # 关闭事件
    @app.on_event("shutdown")
    async def shutdown_event():
        log.info("应用关闭")

    return app

# 创建应用实例
app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        log_level="info"
    )
