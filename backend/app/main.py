import asyncio
import json
import os
import shutil
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import a2a as a2a_api
from app.api import a2a_admin
from app.api import a2a_agents as a2a_agents_api
from app.a2a import server as a2a_server

from app.api import alerts as alerts_api
from app.api import approval as approval_api
from app.api import agents, auth, chains, chat, concept_backends, conversations, health, memory, messages, model_config, resource_admin, vectorization
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
    from app.api.notifications import router as notif_router
    app.include_router(notif_router, prefix=settings.API_PREFIX)
    app.include_router(memory.router, prefix=settings.API_PREFIX)
    app.include_router(eval_api.router, prefix=f"{settings.API_PREFIX}/eval")
    app.include_router(approval_api.router, prefix=settings.API_PREFIX)
    app.include_router(explorer_api.router, prefix=settings.API_PREFIX)
    app.include_router(alerts_api.router, prefix=settings.API_PREFIX)
    app.include_router(mcp_api.router, prefix=settings.API_PREFIX)
    app.include_router(mcp_servers_api.router, prefix=settings.API_PREFIX)
    app.include_router(a2a_api.router, prefix=settings.API_PREFIX)
    app.include_router(a2a_agents_api.router, prefix=settings.API_PREFIX)
    app.include_router(vectorization.router, prefix=settings.API_PREFIX)

    app.include_router(system_api.router, prefix=settings.API_PREFIX)
    app.include_router(ontology_api.router, prefix=settings.API_PREFIX)
    app.include_router(chains.router, prefix=settings.API_PREFIX)
    app.include_router(agents.router, prefix=settings.API_PREFIX)
    app.include_router(concept_backends.router, prefix=settings.API_PREFIX)
    app.include_router(model_config.router, prefix=settings.API_PREFIX)
    app.include_router(resource_admin.router, prefix=settings.API_PREFIX)

    app.include_router(auth.router, prefix=settings.API_PREFIX)

    # A2A 服务端：root 级端点（Agent Card + tasks/*），必须在 SPA 兜底 @app.get("/{full_path:path}") 前挂载
    app.include_router(a2a_server.router)
    # A2A 服务端管理（API Key + 业务域列表），挂 /api 前缀
    app.include_router(a2a_admin.keys_router, prefix=settings.API_PREFIX)
    app.include_router(a2a_admin.domains_router, prefix=settings.API_PREFIX)

    # SysWebApi 反向代理 → MES OAuth 认证服务器
    @app.api_route("/SysWebApi/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
    async def proxy_syswebapi(request: Request, path: str):
        import httpx
        target = f"http://172.21.10.18:99/SysWebApi/{path}"
        body = await request.body() if request.method in ("POST", "PUT") else None
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(request.method, target, headers=headers, content=body, params=request.query_params)
            return JSONResponse(
                content=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
                status_code=resp.status_code,
                headers={k: v for k, v in resp.headers.items() if k.lower() not in ("content-encoding", "transfer-encoding")},
            )

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
        # 恢复用户选择的活跃 namespace：进程重启后 OntologyService._cached_ns
        # 丢失，若不恢复会回落 settings.NEO4J_NAMESPACE（.env），导致加载错误的
        # 业务域（如 knowledgeagent 项目却按 manufacturing 编译，出现幽灵概念 ESOP、
        # EquipmentManual 缺失）。必须在本体加载之前恢复。
        try:
            from app.api.chains import _get_active_namespace
            from app.services.ontology_service import OntologyService
            ns = await _get_active_namespace()
            OntologyService._cached_ns = ns
            log.info(f"[Namespace] 已恢复活跃 namespace: {ns}")
        except Exception as e:
            log.warning(f"[Namespace] 恢复活跃 namespace 失败: {e}")
        # 加载模型选择配置到内存
        from app.agents.settings.model import MODEL_CONFIG
        from app.api.model_config import _load_config, DEFAULT_SELECTION
        cfg = await _load_config()
        MODEL_CONFIG.update({**DEFAULT_SELECTION, **cfg.get("selection", {})})
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

        # 编译器: 启动时自动编译（有域配置时 1-2 秒即可完成）
        try:
            from app.agents import compile_and_register
            await compile_and_register()
        except Exception as e:
            log.warning(f"[Compiler] 状态检查失败: {e}")

        # 初始化 MultiSystemBackend（多系统数据路由）
        try:
            from app.services.multi_system_backend import multi_system_backend
            await multi_system_backend.load_configs()
            log.info(f"[MultiSystemBackend] 初始化完成")
        except Exception as e:
            log.warning(f"[MultiSystemBackend] 初始化失败（非致命）: {e}")

        # 初始化 DataBackend（业务数据后端，降级链）
        try:
            from app.services.data_backend import data_backend
            await data_backend.initialize()
        except Exception as e:
            log.warning(f"[DataBackend] 初始化失败（非致命）: {e}")

        # 概念 API 查询走系统配置端点（multi_system_backend），不再需要适配器注册
        # 初始化 MCP Server 连接（优先从 DB，首次从 .env 种子）
        try:
            from app.mcp import mcp_registry
            from app.db import get_db
            from app.repositories.mcp_server_repo import McpServerRepository

            async for session in get_db():
                repo = McpServerRepository(session)

                # 首次：将 .env 中的 MCP_SERVERS 种子写入 DB
                env_servers = json.loads(settings.MCP_SERVERS)
                for cfg in env_servers:
                    existing = await repo.get_by_name(cfg["name"])
                    if not existing:
                        await repo.create(
                            name=cfg["name"],
                            command=cfg["command"],
                            args=json.dumps(cfg.get("args", [])),
                            enabled=True,
                        )

                # 从 DB 加载启用的 MCP 服务器
                servers = await repo.list_enabled()
                for s in servers:
                    try:
                        cmd = _validate_command(s.command)
                        args_list = json.loads(s.args) if s.args else []
                        # 工具风险声明 {tool_name: risk}，写入 TOOL_SAFETY / REQUIRES_APPROVAL
                        tool_risks = json.loads(s.tool_risks) if getattr(s, "tool_risks", "") else {}
                        await mcp_registry.connect_server(s.name, cmd, args_list, tool_risks)
                        log.info(f"[MCP] Server 已连接: {s.name} ({cmd} {' '.join(args_list)})")
                    except Exception as e:
                        log.warning(f"[MCP] Server 连接失败 {s.name}: {e}")
                if not servers:
                    log.info("[MCP] 未配置 MCP Server")
        except Exception as e:
            log.warning(f"[MCP] Server 连接失败（非致命）: {e}")

        # 初始化 A2A 外部 Agent（HTTP 连接，拉取 Agent Card；优先 DB，首次从 .env 种子）
        try:
            from app.a2a import a2a_registry
            from app.db import get_db
            from app.repositories.a2a_agent_repo import A2aAgentRepository

            async for session in get_db():
                repo = A2aAgentRepository(session)

                # 首次：将 .env 中的 A2A_EXTERNAL_AGENTS 种子写入 DB
                env_agents = json.loads(settings.A2A_EXTERNAL_AGENTS)
                for cfg in env_agents:
                    existing = await repo.get_by_name(cfg["name"])
                    if not existing:
                        await repo.create(
                            name=cfg["name"],
                            display_name=cfg.get("display_name", ""),
                            url=cfg.get("url", ""),
                            enabled=cfg.get("enabled", True),
                        )

                # 从 DB 加载启用 + url 非空的外部 Agent 并连接
                agents = await repo.list_enabled()
                loaded = 0
                for a in agents:
                    if not a.url or not a.url.strip():
                        log.info(f"[A2A] {a.name} 未配置 URL，跳过")
                        continue
                    try:
                        await a2a_registry.connect_agent(a.name, a.url.strip(), display_name=a.display_name, auto_collab=a.auto_collab)
                        loaded += 1
                    except Exception as e:
                        log.warning(f"[A2A] 外部 Agent 连接失败 {a.name}: {e}")
                log.info(f"[A2A] 外部 Agent 已连接 {loaded} 个")
                if not agents:
                    log.info("[A2A] 未配置外部 Agent")
        except Exception as e:
            log.warning(f"[A2A] 外部 Agent 初始化失败（非致命）: {e}")

        # 启动后台监控调度器（周期性扫描告警）
        try:
            from app.services.monitor_scheduler import monitor_scheduler
            await monitor_scheduler.start()
        except Exception as e:
            log.warning(f"[MonitorScheduler] 启动失败（非致命）: {e}")

        # 启动事件分发 worker
        try:
            if settings.EVENT_DISPATCHER_ENABLED:
                from app.services.event_dispatcher import event_dispatcher
                await event_dispatcher.start()
        except Exception as e:
            log.warning(f"[EventDispatcher] 启动失败（非致命）: {e}")

        # 重新加载链引擎缓存（async 上下文，DB 此时已就绪）
        try:
            from app.core.chain_engine import reload_chains_async
            await reload_chains_async()
        except Exception as e:
            log.warning(f"链缓存加载失败: {e}")

        # 启动向量索引后台维护（周期性补全未索引节点）
        try:
            from app.services.vector_search_engine import vector_search_engine
            await vector_search_engine.start_maintenance()
        except Exception as e:
            log.warning(f"[VectorSearch] 后台维护启动失败（非致命）: {e}")

        # Seed 默认通知规则
        try:
            from app.services.notification_seed import seed_default_rules
            await seed_default_rules()
        except Exception as e:
            log.warning(f"[NotificationSeed] 失败（非致命）: {e}")

    # 关闭事件
    @app.on_event("shutdown")
    async def shutdown_event():
        from app.services.monitor_scheduler import monitor_scheduler
        await monitor_scheduler.stop()

        from app.services.event_dispatcher import event_dispatcher
        await event_dispatcher.stop()

        from app.services.vector_search_engine import vector_search_engine
        await vector_search_engine.stop_maintenance()

        from app.mcp import mcp_registry
        await mcp_registry.close_all()

        from app.a2a import a2a_registry
        await a2a_registry.close_all()

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
