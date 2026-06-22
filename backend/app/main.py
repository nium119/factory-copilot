import asyncio
import os
import shutil
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import a2a as a2a_api
from app.api import a2a_agents as a2a_agents_api

from app.api import alerts as alerts_api
from app.api import approval as approval_api
from app.api import agents, auth, chains, chat, concept_backends, conversations, health, memory, messages, kpi_admin, explorer_rules_admin, resource_admin
from app.api import eval as eval_api
from app.api import explorer as explorer_api
from app.api import mcp as mcp_api
from app.api import mcp_servers as mcp_servers_api
from app.api import ontology as ontology_api
from app.api import system as system_api
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logger import log
from app.core.middleware import AuthMiddleware, LoggingMiddleware


def _validate_command(command: str) -> str:
    """验证命令路径安全：必须是绝对路径或可通过 PATH 解析"""
    if os.path.isabs(command):
        if os.path.exists(command):
            return command
        raise ValueError(f"命令不存在: {command}")
    resolved = shutil.which(command)
    if resolved:
        return resolved
    raise ValueError(f"命令未找到: {command}")


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

    # 添加鉴权中间件（在 CORS 之后、日志之前）
    app.add_middleware(AuthMiddleware)

    # 添加日志中间件
    app.add_middleware(LoggingMiddleware)

    # 注册路由
    app.include_router(health.router)
    app.include_router(chat.router, prefix=settings.API_PREFIX)
    app.include_router(conversations.router, prefix=f"{settings.API_PREFIX}/conversations")
    app.include_router(messages.router, prefix=settings.API_PREFIX)
    app.include_router(memory.router, prefix=settings.API_PREFIX)
    app.include_router(eval_api.router, prefix=f"{settings.API_PREFIX}/eval")
    app.include_router(approval_api.router, prefix=settings.API_PREFIX)
    app.include_router(explorer_api.router, prefix=settings.API_PREFIX)
    app.include_router(alerts_api.router, prefix=settings.API_PREFIX)
    app.include_router(mcp_api.router, prefix=settings.API_PREFIX)
    app.include_router(mcp_servers_api.router, prefix=settings.API_PREFIX)
    app.include_router(a2a_api.router, prefix=settings.API_PREFIX)
    app.include_router(a2a_agents_api.router, prefix=settings.API_PREFIX)

    app.include_router(system_api.router, prefix=settings.API_PREFIX)
    app.include_router(ontology_api.router, prefix=settings.API_PREFIX)
    app.include_router(chains.router, prefix=settings.API_PREFIX)
    app.include_router(agents.router, prefix=settings.API_PREFIX)
    app.include_router(concept_backends.router, prefix=settings.API_PREFIX)
    app.include_router(kpi_admin.router, prefix=settings.API_PREFIX)
    app.include_router(explorer_rules_admin.router, prefix=settings.API_PREFIX)
    app.include_router(resource_admin.router, prefix=settings.API_PREFIX)

    app.include_router(auth.router, prefix=settings.API_PREFIX)

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
        # 自动初始化数据库（建表 + Agent 种子数据）
        from app.core.startup import ensure_database
        await ensure_database()
        # 初始化 KPI 种子数据（从 YAML → DB）
        from app.api.kpi_admin import seed_from_yaml, reload_kpi_module
        seed_from_yaml()
        reload_kpi_module()
        from app.api.explorer_rules_admin import seed_from_defaults, reload_explorer_rules
        seed_from_defaults()
        reload_explorer_rules()
        # 初始化向量记忆服务
        from app.services.vector_memory_service import vector_memory_service
        await vector_memory_service.initialize()
        # 初始化 Neo4j 连接（带重试，处理启动时序问题）
        neo4j_ok = False
        from app.services.neo4j_service import neo4j_service
        for attempt in range(1, 4):
            try:
                neo4j_ok = await neo4j_service.connect()
                if neo4j_ok:
                    log.info("[Neo4j] 连接成功")
                    break
            except Exception as e:
                log.warning(f"[Neo4j] 连接失败 (尝试 {attempt}/3): {e}")
            if attempt < 3:
                await asyncio.sleep(2)

        # 加载本体模型（从 Neo4j 或 YAML fallback）
        try:
            from app.services.ontology_service import ontology_service
            await ontology_service.load()
            log.info(f"本体加载完成: source={ontology_service.source}, loaded={ontology_service.loaded}")
            # Set domain description in prompts from ontology project meta
            try:
                from app.core.prompts import set_prompt_domain
                meta = ontology_service.meta
                domain_desc = meta.get("description") or meta.get("projectName")
                if domain_desc:
                    set_prompt_domain(domain_desc)
                    log.info(f"Prompt domain set: {domain_desc}")
            except Exception:
                pass
        except Exception as e:
            log.warning(f"本体加载失败（非致命）: {e}")

        # 初始化 DataBackend（业务数据后端，降级链）
        try:
            from app.services.data_backend import data_backend
            await data_backend.initialize()
        except Exception as e:
            log.warning(f"[DataBackend] 初始化失败（非致命）: {e}")

        # 注册概念 Adapter（自定义外部集成逻辑）
        try:
            from app.services.concept_backend_config_service import auto_register_adapters
            auto_register_adapters()
        except Exception as e:
            log.warning(f"[AdapterRegistry] 注册失败（非致命）: {e}")
        # 初始化 MCP Server 连接（优先从 DB，首次从 .env 种子）
        try:
            import json as _json
            import sqlite3 as _sqlite3

            from app.mcp import mcp_registry
            from app.api.mcp_servers import _ensure_table, _DB_PATH as _MCP_DB

            _ensure_table()
            db_conn = _sqlite3.connect(_MCP_DB)
            db_conn.row_factory = _sqlite3.Row

            # 首次：将 .env 中的 MCP_SERVERS 种子写入 DB
            env_servers = _json.loads(settings.MCP_SERVERS)
            for cfg in env_servers:
                existing = db_conn.execute("SELECT 1 FROM mcp_servers WHERE name=?", (cfg["name"],)).fetchone()
                if not existing:
                    db_conn.execute(
                        "INSERT INTO mcp_servers (name, command, args, enabled) VALUES (?,?,?,1)",
                        (cfg["name"], cfg["command"], _json.dumps(cfg.get("args", []))),
                    )
            db_conn.commit()

            # 从 DB 加载启用的 MCP 服务器
            rows = db_conn.execute("SELECT * FROM mcp_servers WHERE enabled=1").fetchall()
            db_conn.close()
            for row in rows:
                try:
                    cmd = _validate_command(row["command"])
                    args = _json.loads(row["args"])
                    await mcp_registry.connect_server(row["name"], cmd, args)
                    log.info(f"[MCP] Server 已连接: {row['name']} ({cmd} {' '.join(args)})")
                except Exception as e:
                    log.warning(f"[MCP] Server 连接失败 {row['name']}: {e}")
            if not rows:
                log.info("[MCP] 未配置 MCP Server")
        except Exception as e:
            log.warning(f"[MCP] Server 连接失败（非致命）: {e}")

        # 初始化 A2A 外部 Agent（优先从 DB，首次从 .env 种子）
        try:
            import json as _json
            import sqlite3 as _sqlite3

            from app.agents.external_agents import register as register_external
            from app.api.a2a_agents import _ensure_table as _a2a_table, _DB_PATH as _A2A_DB

            _a2a_table()
            db_conn = _sqlite3.connect(_A2A_DB)
            db_conn.row_factory = _sqlite3.Row

            # 首次：将 .env 中的 A2A_EXTERNAL_AGENTS 种子写入 DB
            env_agents = _json.loads(settings.A2A_EXTERNAL_AGENTS)
            for cfg in env_agents:
                existing = db_conn.execute("SELECT 1 FROM a2a_agents WHERE name=?", (cfg["name"],)).fetchone()
                if not existing:
                    db_conn.execute(
                        "INSERT INTO a2a_agents (name, display_name, command, args, enabled) VALUES (?,?,?,?,1)",
                        (cfg["name"], cfg.get("display_name", ""), cfg["command"], _json.dumps(cfg.get("args", []))),
                    )
            db_conn.commit()

            # 从 DB 加载启用的 A2A Agent
            rows = db_conn.execute("SELECT * FROM a2a_agents WHERE enabled=1").fetchall()
            db_conn.close()
            for row in rows:
                try:
                    validated_cmd = _validate_command(row["command"])
                    register_external(row["name"], None, "external", {
                        "display_name": row.get("display_name", ""),
                        "command": validated_cmd,
                        "args": _json.loads(row["args"]),
                    })
                    log.info(f"[A2A] 外部 Agent 已注册: {row['name']}")
                except Exception as e:
                    log.warning(f"[A2A] 外部 Agent 注册失败 {row['name']}: {e}")
            if not rows:
                log.info("[A2A] 未配置外部 Agent")
        except Exception as e:
            log.warning(f"[A2A] 外部 Agent 注册失败（非致命）: {e}")

        # 启动后台监控调度器（周期性扫描告警）
        try:
            from app.services.monitor_scheduler import monitor_scheduler
            await monitor_scheduler.start()
        except Exception as e:
            log.warning(f"[MonitorScheduler] 启动失败（非致命）: {e}")

    # 关闭事件
    @app.on_event("shutdown")
    async def shutdown_event():
        from app.services.monitor_scheduler import monitor_scheduler
        await monitor_scheduler.stop()

        from app.mcp import mcp_registry
        await mcp_registry.close_all()

        from app.services.neo4j_service import neo4j_service
        await neo4j_service.disconnect()

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
