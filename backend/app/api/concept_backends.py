"""概念后端配置 API — 概念路由信息查询。"""

from fastapi import APIRouter

router = APIRouter(prefix="/concept-backends", tags=["概念后端配置"])


@router.get("/full")
async def get_full_config():
    """返回所有本体概念与后端配置的合并结果。
    后端路由由系统配置（multi_system_backend）决定，不再需要适配器。
    """
    from app.services.ontology_service import ontology_service
    from app.services.multi_system_backend import multi_system_backend

    concepts = ontology_service.get_concepts()

    rows = []
    for c in concepts:
        name = c.get("name", "")
        # 检查是否有 API 系统配置
        system = multi_system_backend._resolve_system(name)
        backend = "api" if system else "default"
        rows.append({
            "conceptName": name,
            "conceptLabel": c.get("label", name),
            "parents": c.get("parents", []) if isinstance(c.get("parents"), list) else [],
            "seq": c.get("seq", 999),
            "properties": [
                {"name": p.get("name", ""), "label": p.get("label", ""), "type": p.get("type", "string")}
                for p in c.get("properties", [])
            ],
            "actions": [
                {
                    "name": a.get("name", ""),
                    "label": a.get("label", ""),
                    "inputParams": [
                        {"name": p.get("name", ""), "label": p.get("label", ""), "type": p.get("paramType", p.get("type", "string"))}
                        for p in (a.get("inputParams") or [])
                    ],
                }
                for a in c.get("actions", [])
            ],
            "backend": backend,
            "baseUrl": "",
        })

    return {"concepts": rows}


@router.get("/concepts")
async def list_configured_concepts():
    """返回已配置 API 的概念列表。"""
    from app.services.multi_system_backend import multi_system_backend
    concept_backends = {}
    for concept_name in multi_system_backend._concept_system:
        concept_backends[concept_name] = {"backend": "api"}
    return {"concept_backends": concept_backends}
