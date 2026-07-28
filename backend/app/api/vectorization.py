"""向量化配置 API — 管理概念级向量化启停 + 指纹属性选择。

配置存储在 Neo4j Concept 节点的 vectorization 属性上，
由 Factory Copilot 读写，OntoStudio 不感知。
"""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/admin/vectorization", tags=["向量化配置"])


class FingerprintConfig(BaseModel):
    properties: list[str] = []
    relationProperties: list[dict] = []


class VectorizationConfig(BaseModel):
    enabled: bool = False
    fingerprint: FingerprintConfig = FingerprintConfig()


# ── 列表：所有概念 + 向量化状态 ─────────────────────────

@router.get("/concepts", summary="获取指定业务域的概念向量化配置状态")
async def list_concepts(namespace: str = "manufacturing"):
    """返回概念列表，含向量化启停状态、指纹配置、索引进度。"""
    from app.services.neo4j_service import neo4j_service
    from app.services.ontology_service import ontology_service

    concepts = ontology_service.get_concepts()
    # 按 namespace 过滤
    if namespace:
        concepts = [c for c in concepts if c.get("namespace", "") == namespace]
    result = []
    for c in concepts:
        name = c.get("name", "")
        label = c.get("label", "")
        if not label or label == name:
            continue  # 跳过字典/纯枚举概念
        if not c.get("properties"):
            continue  # 跳过无属性容器

        vec_cfg = c.get("vectorization")
        if isinstance(vec_cfg, str):
            try:
                vec_cfg = json.loads(vec_cfg)
            except Exception:
                vec_cfg = None

        enabled = bool(vec_cfg and vec_cfg.get("enabled"))
        fingerprint = (vec_cfg or {}).get("fingerprint", {})

        # ── 指纹属性：补全中文标签 ──
        prop_label_map = {p.get("name", ""): p.get("label", p.get("name")) for p in c.get("properties", [])}
        # 构建概念名→中文标签映射（用于关系属性）
        concept_label_map = {cc.get("name", ""): cc.get("label", cc.get("name")) for cc in concepts}
        # 构建概念名→属性列表映射（用于子概念属性标签）
        concept_props_map = {cc.get("name", ""): cc.get("properties", []) for cc in concepts}

        enriched_fp = {
            "properties": [
                {"name": p, "label": prop_label_map.get(p, p)}
                for p in fingerprint.get("properties", [])
            ],
            "relationProperties": [
                {
                    "relation": rp.get("relation", ""),
                    "relationLabel": concept_label_map.get(rp.get("relation", ""), rp.get("relation", "")),
                    "properties": [
                        {"name": p, "label": next(
                            (cp.get("label", cp.get("name", p))
                             for cp in concept_props_map.get(rp.get("relation", ""), [])
                             if cp.get("name") == p
                        ), p)}
                        for p in rp.get("properties", [])
                    ],
                    "separator": rp.get("separator", "×"),
                }
                for rp in fingerprint.get("relationProperties", [])
            ],
        } if enabled else {}

        # 查询索引进度
        indexed = 0
        total = 0
        if enabled and neo4j_service.connected:
            try:
                pk = next(
                    (p.get("name", "name") for p in c.get("properties", [])
                     if p.get("isPrimary")), "name"
                )
                namespace_q = ""
                ns_val = ""
                from app.core.config import settings
                namespace_val = settings.NEO4J_NAMESPACE or ""
                if namespace_val:
                    namespace_q = "AND (n._namespace = $ns OR $ns = '')"
                    ns_val = namespace_val
                records = await neo4j_service.execute_read(f"""
                    MATCH (n:{name})
                    WHERE n.{pk} IS NOT NULL {namespace_q}
                    RETURN count(n) AS total,
                           count(n.embedding) AS indexed
                """, {"ns": namespace_val} if namespace_val else {})
                if records:
                    total = records[0].get("total", 0) or 0
                    indexed = records[0].get("indexed", 0) or 0
            except Exception:
                pass

        result.append({
            "conceptName": name,
            "conceptLabel": label,
            "enabled": enabled,
            "fingerprint": enriched_fp,
            "properties": [
                {"name": p.get("name", ""), "label": p.get("label", ""), "type": p.get("type", "")}
                for p in c.get("properties", [])
            ],
            "relations": [
                {"target": r.get("target", ""), "label": r.get("label", "")}
                for r in c.get("relations", [])
            ],
            "indexedCount": indexed,
            "totalCount": total,
        })

    return {"concepts": result}


# ── 全局设置 ───────────────────────────────────────────

@router.get("/settings", summary="获取向量化全局设置")
async def get_settings():
    from app.services.vector_search_engine import get_vectorization_settings
    return {"settings": get_vectorization_settings().to_dict()}


@router.put("/settings", summary="更新向量化全局设置")
async def update_settings(body: dict):
    from app.services.vector_search_engine import get_vectorization_settings, vector_search_engine
    get_vectorization_settings().update(body)
    # 维护间隔变更时重启循环
    if "maintenanceInterval" in body:
        await vector_search_engine.stop_maintenance()
        await vector_search_engine.start_maintenance()
    return {"ok": True, "settings": get_vectorization_settings().to_dict()}


# ── 保存：写入单个概念的向量化配置 ───────────────────────

class SaveVectorizationRequest(BaseModel):
    enabled: bool = False
    fingerprint: FingerprintConfig = FingerprintConfig()


@router.put("/concepts/{concept_name}", summary="更新概念的向量化配置")
async def save_concept_config(concept_name: str, body: SaveVectorizationRequest):
    """写入 Neo4j Concept 节点的 vectorization 属性。"""
    from app.services.neo4j_service import neo4j_service
    from app.services.ontology_service import ontology_service
    from app.services.action_executor import action_executor
    from app.services.vector_search_engine import vector_search_engine

    if not neo4j_service.connected:
        raise HTTPException(status_code=503, detail="Neo4j 未连接")

    # 验证概念存在
    concepts = ontology_service.get_concepts()
    found = any(c.get("name") == concept_name for c in concepts)
    if not found:
        raise HTTPException(status_code=404, detail=f"概念 {concept_name} 不存在")

    cfg = {
        "enabled": body.enabled,
        "fingerprint": {
            "properties": body.fingerprint.properties,
            "relationProperties": body.fingerprint.relationProperties,
        },
    }
    cfg_json = json.dumps(cfg, ensure_ascii=False)

    await neo4j_service.execute_write(
        "MATCH (c:Concept {name: $name}) SET c.vectorization = $cfg",
        {"name": concept_name, "cfg": cfg_json},
    )

    # 指纹变更 → 清空旧嵌入，维护循环自动重建
    if body.enabled:
        from app.core.config import settings
        ns = settings.NEO4J_NAMESPACE or ""
        await neo4j_service.execute_write(f"""
            MATCH (n:{concept_name})
            WHERE (n._namespace = $ns OR $ns = '')
            SET n.embedding = NULL
        """, {"ns": ns})

    # 强制重载本体数据，让新配置立即生效
    await ontology_service.reload()
    action_executor.invalidate_cache()
    vector_search_engine.invalidate_cache()

    return {"ok": True, "conceptName": concept_name, "config": cfg}


# ── 重建索引 ─────────────────────────────────────────────

@router.post("/concepts/{concept_name}/reindex/stream", summary="重建概念的向量化数据（流式进度）")
async def reindex_concept_stream(concept_name: str):
    """清空 + 逐条重建，SSE 流式返回进度。"""
    from starlette.responses import StreamingResponse
    from app.services.neo4j_service import neo4j_service
    from app.services.vector_search_engine import vector_search_engine as vse
    from app.core.config import settings

    if not neo4j_service.connected:
        raise HTTPException(status_code=503, detail="Neo4j 未连接")

    ns = settings.NEO4J_NAMESPACE or ""

    async def generate():
        yield f"data: {json.dumps({'phase': 'clear', 'message': '正在清空旧向量化数据...'}, ensure_ascii=False)}\n\n"
        await neo4j_service.execute_write(f"""
            MATCH (n:{concept_name})
            WHERE (n._namespace = $ns OR $ns = '')
            SET n.embedding = NULL
        """, {"ns": ns})
        yield f"data: {json.dumps({'phase': 'start', 'message': '开始重建...'}, ensure_ascii=False)}\n\n"

        # 直接用列表收集进度，避免 asyncio.Queue 跨任务问题
        progress = []
        def _cb(done, total):
            progress.append((done, total))
        await vse._rebuild_concept(concept_name, progress_cb=_cb)

        for done, total in progress:
            yield f"data: {json.dumps({'phase': 'progress', 'done': done, 'total': total}, ensure_ascii=False)}\n\n"
        done = progress[-1][0] if progress else 0
        yield f"data: {json.dumps({'phase': 'done', 'done': done, 'message': f'重建完成 {done} 条'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/concepts/{concept_name}/reindex", summary="重建概念的向量化数据")
async def reindex_concept(concept_name: str):
    """清空指定概念的全部 embedding，立即触发重建。"""
    from app.services.neo4j_service import neo4j_service
    from app.services.vector_search_engine import vector_search_engine as vse

    if not neo4j_service.connected:
        raise HTTPException(status_code=503, detail="Neo4j 未连接")

    from app.core.config import settings
    import asyncio

    ns = settings.NEO4J_NAMESPACE or ""
    await neo4j_service.execute_write(f"""
        MATCH (n:{concept_name})
        WHERE (n._namespace = $ns OR $ns = '')
        SET n.embedding = NULL
    """, {"ns": ns})

    await vse._rebuild_concept(concept_name)
    pk = await vse._get_primary_key(concept_name)
    done_rec = await neo4j_service.execute_read(f"""
        MATCH (n:{concept_name})
        WHERE n.embedding IS NOT NULL AND (n._namespace = $ns OR $ns = '')
        RETURN count(n) AS cnt
    """, {"ns": ns})
    done = done_rec[0]["cnt"] if done_rec else 0
    return {"ok": True, "conceptName": concept_name, "indexed": done}
