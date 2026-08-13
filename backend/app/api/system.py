import asyncio
import os
import subprocess
import sys
import time

from fastapi import APIRouter

from app.core.config import settings
from app.core.logger import log
from app.core.resource_monitor import resource_monitor

router = APIRouter(tags=["系统状态"])

_start_time = time.time()


@router.get("/system/resources", summary="获取系统资源状态")
async def get_resource_status():
    """返回当前系统资源使用状况（并发/API频率/token预算/模型层级）"""
    return resource_monitor.snapshot()


@router.get("/system/rag-stats", summary="RAG 召回统计")
async def get_rag_stats():
    """返回 RAG 召回命中率、模式分布等统计"""
    from app.agents.base import BaseAgent
    return {"ok": True, "data": await BaseAgent.get_rag_stats()}


@router.get("/system/health", summary="系统健康总览")
async def get_system_health():
    """汇总 Neo4j / Ontology / DataBackend / DB 健康状态。"""
    checks = {}

    # Neo4j
    try:
        from app.services.neo4j_service import neo4j_service
        nh = await neo4j_service.health()
        checks["neo4j"] = {"ok": nh.get("ok", False), "uri": nh.get("uri", "")}
    except Exception as e:
        checks["neo4j"] = {"ok": False, "error": str(e)}

    # Ontology
    try:
        from app.services.ontology_service import ontology_service
        oh = ontology_service.status()
        checks["ontology"] = {
            "ok": oh.get("loaded", False),
            "source": oh.get("source", ""),
            "concepts": oh.get("conceptCount", 0),
            "actions": oh.get("actionCount", 0),
            "stale": oh.get("consecutiveFailures", 0) > 0,
        }
    except Exception as e:
        checks["ontology"] = {"ok": False, "error": str(e)}

    # DataBackend
    try:
        from app.services.data_backend import data_backend
        dbh = await data_backend.health()
        checks["data_backend"] = {"ok": dbh.get("ok", False), "primary": dbh.get("primary", ""),
                                  "backends": dbh.get("backends", {})}
    except Exception as e:
        checks["data_backend"] = {"ok": False, "error": str(e)}

    # DB (SQLite)
    try:
        from sqlalchemy import text

        from app.db import get_db
        async for session in get_db():
            await session.execute(text("SELECT 1"))
            checks["db"] = {"ok": True}
            break
    except Exception as e:
        checks["db"] = {"ok": False, "error": str(e)}

    # 通知
    try:
        from sqlalchemy import func, select

        from app.db import get_db as _gdb
        from app.models.event import EventQueue
        from app.services.event_dispatcher import event_dispatcher
        pending = 0
        async for sess in _gdb():
            r = await sess.execute(select(func.count()).where(EventQueue.status == 'pending'))
            pending = r.scalar() or 0
            break
        checks["notifications"] = {
            "ok": True, "dispatcher": event_dispatcher.is_running,
            "pending_events": pending, "counters": event_dispatcher.counters,
        }
    except Exception as e:
        checks["notifications"] = {"ok": False, "error": str(e)}

    # 资源
    try:
        snap = resource_monitor.snapshot()
        snap["ok"] = snap.get("tier", "normal") != "critical"
        checks["resources"] = snap
    except Exception:
        checks["resources"] = {"ok": False}

    # 运行时长 + 内存
    uptime_s = max(0, time.time() - _start_time)
    mem_mb = 0
    try:
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            psapi = ctypes.windll.psapi
            kernel32 = ctypes.windll.kernel32
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                mem_mb = pmc.WorkingSetSize / 1024 / 1024
        else:
            try:
                with open(f'/proc/{os.getpid()}/status', 'r') as f:
                    for line in f:
                        if line.startswith('VmRSS:'):
                            mem_mb = int(line.split()[1]) / 1024
                            break
            except Exception:
                pass
    except Exception:
        pass
    checks["uptime"] = {"ok": True, "uptime_s": round(uptime_s, 0), "memory_mb": round(mem_mb, 1)}

    all_ok = all(c.get("ok", False) for c in checks.values() if c is not None)
    return {"ok": all_ok, "checks": checks}


# ── 系统配置 CRUD ──

@router.get("/system/configs", summary="获取系统配置")
async def get_system_configs():
    """返回所有系统配置（key-value），DB 无值时用 .env 默认值填充"""
    try:
        from sqlalchemy import select

        from app.db import get_db
        from app.models.system_config import SystemConfig
        db_map = {}
        async for session in get_db():
            result = await session.execute(select(SystemConfig).order_by(SystemConfig.key))
            for r in result.scalars().all():
                db_map[r.key] = r.value
            break

        # 默认值映射（从 .env / settings 读取）
        defaults = {
            "neo4j_uri": settings.NEO4J_URI,
            "neo4j_user": settings.NEO4J_USER,
            "neo4j_password": settings.NEO4J_PASSWORD,
            "neo4j_database": settings.NEO4J_DATABASE,
            "neo4j_enabled": "true" if settings.NEO4J_ENABLED else "false",
            "db_type": settings.DB_TYPE,
            "db_sqlite_enabled": "true" if settings.DB_TYPE == "sqlite" else "false",
            "db_sqlite_path": settings.DB_PATH,
            "db_postgresql_enabled": "true" if settings.DB_TYPE == "postgresql" else "false",
            "db_postgresql_host": settings.DB_HOST,
            "db_postgresql_port": str(settings.DB_PORT),
            "db_postgresql_name": settings.DB_NAME,
            "db_postgresql_user": settings.DB_USER,
            "db_postgresql_password": settings.DB_PASSWORD,
            "db_mssql_enabled": "true" if settings.DB_TYPE == "mssql" else "false",
            "db_mssql_host": settings.DB_HOST,
            "db_mssql_port": str(settings.DB_PORT),
            "db_mssql_name": settings.DB_NAME,
            "db_mssql_user": settings.DB_USER,
            "db_mssql_password": settings.DB_PASSWORD,
        }
        # DB 有值时用 DB，否则用默认值
        data = []
        for key, default_val in defaults.items():
            value = db_map.get(key, "")
            if not value:
                value = default_val
            data.append({"key": key, "value": value, "fromDb": key in db_map})

        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": str(e), "data": []}


@router.put("/system/configs", summary="保存系统配置")
async def save_system_configs(body: dict):
    """批量保存系统配置，传入 {configs: [{key, value, description}]}"""
    try:
        from sqlalchemy import select

        from app.db import get_db
        from app.models.system_config import SystemConfig
        configs = body.get("configs", [])
        async for session in get_db():
            for item in configs:
                key = item.get("key", "")
                if not key:
                    continue
                result = await session.execute(
                    select(SystemConfig).where(SystemConfig.key == key)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.value = item.get("value", "")
                    if "description" in item:
                        existing.description = item.get("description", "")
                else:
                    session.add(SystemConfig(
                        key=key,
                        value=item.get("value", ""),
                        description=item.get("description", ""),
                    ))
            await session.commit()
            break
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _get_system_config(key: str) -> str:
    """从 DB 读取系统配置，不存在返回空字符串"""
    try:
        from sqlalchemy import select

        from app.db import get_db
        from app.models.system_config import SystemConfig
        async for session in get_db():
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == key)
            )
            cfg = result.scalar_one_or_none()
            if cfg:
                return cfg.value or ""
            return ""
    except Exception:
        return ""


@router.post("/system/configs/test-neo4j", summary="测试 Neo4j 连接")
async def test_neo4j_connection(body: dict):
    """使用当前配置测试 Neo4j 连接"""
    from neo4j import AsyncGraphDatabase
    uri = body.get("neo4j_uri", "") or await _get_system_config("neo4j_uri") or settings.NEO4J_URI
    user = body.get("neo4j_user", "") or await _get_system_config("neo4j_user") or settings.NEO4J_USER
    password = body.get("neo4j_password", "") or await _get_system_config("neo4j_password") or settings.NEO4J_PASSWORD
    database = body.get("neo4j_database", "") or await _get_system_config("neo4j_database") or settings.NEO4J_DATABASE

    if not uri:
        return {"ok": False, "error": "未配置 Neo4j 连接地址"}

    try:
        driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        async with driver.session(database=database) as session:
            result = await session.run("RETURN 1 AS ok")
            await result.consume()
        await driver.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/system/configs/test-db", summary="测试数据库连接")
async def test_db_connection(body: dict):
    """使用当前配置测试数据库连接（支持 SQLite/PostgreSQL/MSSQL）"""
    db_type = body.get("db_type", "") or await _get_system_config("db_type") or settings.DB_TYPE

    if db_type == "sqlite":
        db_path = body.get("db_path", "") or await _get_system_config("db_path") or settings.DB_PATH
        url = f"sqlite+aiosqlite:///{db_path}"
    elif db_type == "postgresql":
        host = body.get("db_host", "") or await _get_system_config("db_host") or settings.DB_HOST
        port = body.get("db_port", "") or await _get_system_config("db_port") or str(settings.DB_PORT)
        name = body.get("db_name", "") or await _get_system_config("db_name") or settings.DB_NAME
        user = body.get("db_user", "") or await _get_system_config("db_user") or settings.DB_USER
        pwd = body.get("db_password", "") or await _get_system_config("db_password") or settings.DB_PASSWORD
        url = f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{name}"
    elif db_type == "mssql":
        import urllib.parse
        host = body.get("db_host", "") or await _get_system_config("db_host") or settings.DB_HOST
        port = body.get("db_port", "") or await _get_system_config("db_port") or str(settings.DB_PORT)
        name = body.get("db_name", "") or await _get_system_config("db_name") or settings.DB_NAME
        user = body.get("db_user", "") or await _get_system_config("db_user") or settings.DB_USER
        pwd = body.get("db_password", "") or await _get_system_config("db_password") or settings.DB_PASSWORD
        pwd_enc = urllib.parse.quote_plus(pwd)
        url = f"mssql+aioodbc://{user}:{pwd_enc}@{host}:{port}/{name}?driver=ODBC+Driver+17+for+SQL+Server"
    else:
        return {"ok": False, "error": f"不支持的数据库类型: {db_type}"}

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        engine = create_async_engine(url, echo=False)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _is_docker() -> bool:
    """检测是否运行在 Docker 容器中"""
    try:
        with open('/proc/1/cgroup', 'r') as f:
            return 'docker' in f.read()
    except Exception:
        return os.environ.get('DOCKER_CONTAINER', '') == 'true'


@router.post("/system/restart", summary="重启后端服务")
async def restart_server():
    """保存配置后调用，根据运行环境选择重启方式：
    - Docker/systemd: 直接退出，由进程管理器拉起
    - Windows 手动: bat 脚本启动新进程
    - Linux 手动: execv 原地替换
    """
    if _is_docker():
        log.warning("[System] Docker 环境，exit 后由容器重启策略拉起")

        async def _exit():
            await asyncio.sleep(1.0)
            os._exit(0)
        asyncio.create_task(_exit())
        return {"ok": True, "message": "服务即将重启（Docker），请稍候刷新页面"}

    if sys.platform == 'win32':
        import tempfile
        from pathlib import Path as _Path
        port = settings.API_PORT
        host = settings.API_HOST
        cwd = str(_Path(__file__).resolve().parent.parent.parent)  # backend 目录
        bat_content = f'''@echo off
chcp 65001 >nul
timeout /t 2 /nobreak >nul
cd /d "{cwd}"
"{sys.executable}" -m uvicorn app.main:app --port {port} --host {host}
'''
        bat_path = os.path.join(tempfile.gettempdir(), '_fc_restart.bat')
        with open(bat_path, 'w', encoding='utf-8') as f:
            f.write(bat_content)
        log.warning(f"[System] Windows: 重启脚本 → {bat_path}")

        async def _restart_win():
            await asyncio.sleep(1.0)
            subprocess.Popen(
                ['cmd', '/c', bat_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
            )
            os._exit(0)
        asyncio.create_task(_restart_win())
        return {"ok": True, "message": "服务即将重启，请稍候刷新页面"}

    # Linux 手动: execv 替换当前进程
    log.warning("[System] Linux: execv 替换进程")
    async def _restart_linux():
        await asyncio.sleep(1.0)
        os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'app.main:app',
                                   '--port', str(settings.API_PORT), '--host', settings.API_HOST])
    asyncio.create_task(_restart_linux())
    return {"ok": True, "message": "服务即将重启，请稍候刷新页面"}
