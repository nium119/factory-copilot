from fastapi import APIRouter

from app.core.resource_monitor import resource_monitor

router = APIRouter(tags=["系统状态"])


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
        from app.db import get_db
        async for session in get_db():
            await session.execute("SELECT 1")
            checks["db"] = {"ok": True}
            break
    except Exception as e:
        checks["db"] = {"ok": False, "error": str(e)}

    # 通知
    try:
        from app.services.event_dispatcher import event_dispatcher
        from app.db import get_db as _gdb
        from app.models.event import EventQueue
        from sqlalchemy import func, select
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
        checks["resources"] = resource_monitor.snapshot()
    except Exception:
        checks["resources"] = {"ok": False}

    all_ok = all(c.get("ok", False) for c in checks.values())
    return {"ok": all_ok, "checks": checks}
