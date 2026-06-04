"""概念后端配置 API — 概念适配器路由信息查询。"""

from fastapi import APIRouter

from app.services.concept_backend_config_service import get_all_backends

router = APIRouter(prefix="/concept-backends", tags=["概念后端配置"])


@router.get("/full")
async def get_full_config():
    """返回所有本体概念与后端配置的合并结果。

    供前端配置页展示每个概念的当前路由信息。
    """
    from app.services.ontology_service import ontology_service

    config_backends = get_all_backends()
    concepts = ontology_service.get_concepts()

    rows = []
    for c in concepts:
        name = c.get("name", "")
        cfg = config_backends.get(name, {})
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
            "backend": cfg.get("backend", "default"),
            "baseUrl": cfg.get("baseUrl", ""),
        })

    return {"concepts": rows}


@router.get("/concepts")
async def list_configured_concepts():
    """返回所有已注册适配器的概念列表（名称 → 配置映射）。"""
    return {"concept_backends": get_all_backends()}
