"""本体管理 API — 状态、重载、健康检查、实体搜索。"""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.ontology_service import ontology_service

router = APIRouter(prefix="/ontology", tags=["本体管理"])


class EntitySearchRequest(BaseModel):
    concept: str
    keyword: str = ""


@router.post("/entities/search")
async def search_entities(req: EntitySearchRequest):
    """搜索指定概念下的实体，返回 entityOptions 格式，供前端下拉搜索使用。

    同时搜索 id 和 name 字段（部分概念用 id 做主键如 Material，
    部分用 name 如 Priority）。
    """
    from app.services.neo4j_service import neo4j_service
    from app.core.config import settings

    keyword = (req.keyword or "").strip()
    ns = settings.NEO4J_NAMESPACE
    options = []

    try:
        if not neo4j_service.connected:
            await neo4j_service.connect()
    except Exception:
        return {"options": []}

    try:
        if keyword:
            cypher = (
                f"MATCH (n:`{req.concept}`) WHERE "
                f"(n.id CONTAINS $kw OR n.name CONTAINS $kw)"
            )
        else:
            cypher = f"MATCH (n:`{req.concept}`)"
        if ns:
            cypher += " AND n._namespace = $ns"
        cypher += " RETURN n ORDER BY n.id LIMIT 50"
        params = {"kw": keyword} if keyword else {}
        if ns:
            params["ns"] = ns
        records = await neo4j_service.execute_read(cypher, params)
        options = [
            {"value": r["n"].get("id", r["n"].get("name", "")),
             "label": r["n"].get("name", r["n"].get("id", ""))}
            for r in records
        ]
    except Exception:
        pass

    return {"options": options}


@router.get("/status")
async def get_status():
    """返回当前本体加载状态和元数据。"""
    return ontology_service.status()


@router.get("/health")
async def get_health():
    """供负载均衡器/监控使用的健康检查。

    当本体缓存新鲜且 Neo4j 可达时返回 200。
    当缓存过期超过 ONTOLOGY_MAX_STALENESS 或熔断器
    触发（连续 Neo4j 失败）时返回 503。

    负载均衡器应将流量从返回 503 的实例上移走。
    """
    h = ontology_service.health()
    return JSONResponse(content=h, status_code=h["suggestedHttpStatus"])


@router.post("/reload")
async def reload():
    """从 Neo4j 重新加载本体。"""
    ok = await ontology_service.reload()
    # 使下游缓存失效，确保 action_executor 和 rule_engine 获取到变更
    from app.services.action_executor import action_executor
    from app.services.rule_engine import rule_engine
    action_executor.invalidate_cache()
    rule_engine.invalidate_cache()
    # 重建意图路由器，确保 requires_confirmation 等配置是最新的
    from app.services.intent_router import intent_router
    intent_router.rebuild(ontology_service, action_executor)
    return {"success": ok, "status": ontology_service.status()}


@router.post("/reconnect")
async def reconnect():
    """强制 Neo4j 重连 + 本体重载。返回调试信息。"""
    from app.services.neo4j_service import neo4j_service

    steps = []
    try:
        ok = await neo4j_service.connect()
        steps.append({"step": "neo4j_connect", "ok": ok, "connected": neo4j_service.connected})
    except Exception as e:
        steps.append({"step": "neo4j_connect", "ok": False, "error": str(e)})

    if neo4j_service.connected:
        try:
            from app.services.neo4j_service import neo4j_service as ns
            records = await ns.execute_read("MATCH (c:Concept) RETURN count(c) AS cnt")
            steps.append({"step": "query_concepts", "count": records[0]["cnt"] if records else 0})
        except Exception as e:
            steps.append({"step": "query_concepts", "error": str(e)})

        try:
            loaded = await ontology_service.load()
            steps.append({"step": "ontology_load", "ok": loaded})
        except Exception as e:
            steps.append({"step": "ontology_load", "error": str(e)})

    return {"steps": steps, "status": ontology_service.status()}
