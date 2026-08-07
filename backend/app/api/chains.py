"""链条管理 API — 全部使用 ORM 访问 agent.db。"""

import asyncio
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.core.chain_engine import reload_chains, reload_chains_async
from app.agents.agent_config import AGENT_DEFINITIONS, reload as reload_agents
from app.repositories.chain_repo import ChainRepository
from app.repositories.namespace_config_repo import NamespaceConfigRepository
from app.repositories.api_log_repo import ApiLogRepository

router = APIRouter(prefix="/chains", tags=["链条管理"])


def _safe_str(s) -> str:
    """清理非法 Unicode 代理字符。"""
    if not s:
        return ""
    try:
        return s.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
    except Exception:
        return str(s)


# ── Pydantic 模型 ─────────────────────────────────────────────────

class ChainStepIn(BaseModel):
    step_order: int = 0
    step_id: str = ""
    description: str = ""
    agent_name: str = "analysis_monitor"
    prompt_template: str = ""
    output_key: str = ""
    focus_concepts: str = ""  # 该步骤查询的概念，逗号分隔
    action_name: str = ""
    action_params: str = "{}"
    precondition: str = ""
    on_failure: str = "abort"


class ChainIn(BaseModel):
    chain_id: str
    name: str = ""
    description: str = ""
    triggers: list[str] = []
    final_prompt_template: str = ""
    focus_concepts: str = ""
    mode: str = "merged"
    enabled: bool = True
    verify_target: str = ""
    steps: list[ChainStepIn] = []


class ChainOut(BaseModel):
    chain_id: str
    name: str
    description: str
    triggers: list[str]
    final_prompt_template: str
    focus_concepts: str = ""
    mode: str = "merged"
    enabled: bool
    verify_target: str = ""
    created_at: str = ""
    updated_at: str = ""
    steps: list[ChainStepIn] = []


# ── 路由 ──────────────────────────────────────────────────────────

@router.get("", summary="获取所有链条")
async def list_chains(db: AsyncSession = Depends(get_db)):
    repo = ChainRepository(db)
    chains = await repo.list_all()
    return [
        ChainOut(
            chain_id=_safe_str(c.chain_id), name=_safe_str(c.name), description=_safe_str(c.description),
            triggers=json.loads(_safe_str(c.triggers) or "[]"),
            final_prompt_template=_safe_str(c.final_prompt_template or ""),
            focus_concepts=_safe_str(c.focus_concepts or ""),
            mode=_safe_str(c.mode or "merged"),
            enabled=bool(c.enabled),
            verify_target=_safe_str(c.verify_target or ""),
            created_at=str(c.created_at) if c.created_at else "",
            updated_at=str(c.updated_at) if c.updated_at else "",
            steps=[ChainStepIn(
                step_order=s.step_order, step_id=_safe_str(s.step_id),
                description=_safe_str(s.description), agent_name=_safe_str(s.agent_name),
                prompt_template=_safe_str(s.prompt_template), output_key=_safe_str(s.output_key),
                focus_concepts=_safe_str(s.focus_concepts),
                action_name=_safe_str(s.action_name or ""),
                action_params=_safe_str(s.action_params or "{}"),
                precondition=_safe_str(s.precondition or ""),
                on_failure=_safe_str(s.on_failure or "abort"),
            ) for s in (c.steps or [])],
        )
        for c in chains
    ]


@router.get("/concepts", summary="获取本体概念列表（供链条配置引用）")
async def list_concepts():
    from app.services.ontology_service import ontology_service
    return ontology_service.get_concepts()


@router.get("/concept-entities/{concept_name}", summary="获取概念实体列表（供 ref 参数下拉选择）")
async def list_concept_entities(concept_name: str, keyword: str = ""):
    """查询概念的实体列表，支持 keyword 搜索。返回 code+name 供下拉选择。"""
    from app.services.action_executor import action_executor
    action_executor._ensure_loaded()
    try:
        filters = {}
        if keyword:
            from app.services.ontology_service import ontology_service
            concept = ontology_service.get_concept(concept_name) or {}
            pk = next((p["name"] for p in concept.get("properties", []) if p.get("isPrimary")), "code")
            filters[pk] = keyword
        sig = action_executor._sigs.get(f"{concept_name}_query",
                {"conceptName": concept_name, "outputType": "list", "params": []})
        result_text = await action_executor._execute_query(sig, filters)
        entities = []
        for line in result_text.split('\n'):
            if line.startswith('|') and not line.startswith('|---') and '---' not in line:
                parts = [p.strip() for p in line.split('|') if p.strip()]
                if len(parts) >= 2 and parts[0] not in ('工单Id', '编码', 'code', 'id', 'name'):
                    code = parts[0] if parts[0] != '-' else ''
                    label = parts[1] if len(parts) > 1 and parts[1] != '-' else ''
                    if code:
                        entities.append({'value': code, 'label': f'{code} - {label}' if label else code})
        return entities[:50]
    except Exception as e:
        from app.core.logger import log
        log.warning(f"[ConceptEntities] {concept_name} 查询失败: {e}")
        return []


@router.get("/actions", summary="获取可用 Action 列表（供执行链配置引用）")
async def list_actions():
    """返回本体中所有可用 Action，供执行链 action_name 字段下拉选择。"""
    from app.services.action_executor import action_executor
    from app.services.ontology_service import ontology_service

    sigs = ontology_service.get_action_signatures()
    try:
        from app.mcp import mcp_registry
        mcp_names = set(mcp_registry.get_tool_names())
    except Exception:
        mcp_names = set()

    actions = []
    seen = set()
    for s in sigs:
        name = s.get("functionName", "")
        if not name or name in seen:
            continue
        seen.add(name)
        # actionLabel 有中文名，优先使用；否则用 description；都不行才用 functionName
        func_label = s.get("actionLabel", "") or s.get("description", "") or name
        actions.append({
            "name": name,
            "label": func_label,
            "conceptName": s.get("conceptName", ""),
            "conceptLabel": s.get("conceptLabel", ""),
            "description": s.get("description", ""),
            "outputType": s.get("outputType", "write"),
            "source": s.get("source", "ontology"),
            "params": s.get("params", []),
        })

    for name in sorted(mcp_names - seen):
        actions.append({
            "name": name,
            "label": name,
            "conceptName": "",
            "description": "MCP 工具",
            "outputType": "mcp",
            "source": "mcp",
        })

    return sorted(actions, key=lambda a: (a["source"] != "ontology", a["name"]))


@router.get("/api-logs", summary="获取 API 调用日志")
async def get_api_logs(
    page: int = 1, page_size: int = 50,
    user_id: str = "", concept: str = "", keyword: str = "",
    date_from: str = "", date_to: str = "",
    db: AsyncSession = Depends(get_db),
):
    """查询 API 调用日志，支持分页、搜索、筛选。"""
    try:
        repo = ApiLogRepository(db)
        rows, total = await repo.query_logs(page, page_size, user_id, concept, keyword, date_from, date_to)

        # 补充概念中文标签
        label_map = {}
        try:
            from app.services.ontology_service import ontology_service
            for c in (ontology_service.get_concepts() or []):
                if c.get("label") and c["label"] != c["name"]:
                    label_map[c["name"]] = c["label"]
        except Exception:
            pass

        # 查询会话标题
        from sqlalchemy import select
        from app.models.conversation import Conversation
        titles = {}
        cids = [r.conversation_id for r in rows if r.conversation_id]
        if cids:
            result = await db.execute(
                select(Conversation.id, Conversation.title).where(Conversation.id.in_(cids))
            )
            titles = {row[0]: row[1] or "" for row in result.fetchall()}

        # 构建响应
        logs = []
        for r in rows:
            cn = r.concept or ""
            cid = r.conversation_id or ""
            logs.append({
                "id": r.id,
                "timestamp": r.timestamp,
                "user_id": r.user_id,
                "conversation_id": cid,
                "message": r.message,
                "concept": cn,
                "concept_label": label_map.get(cn, cn),
                "conversation_title": titles.get(cid, ""),
                "method": r.method,
                "url": r.url,
                "status": r.status,
                "elapsed_ms": r.elapsed_ms,
                "error": r.error,
                "request_body": r.request_body,
                "response_body": r.response_body,
                "context": r.context,
            })
        return {"ok": True, "logs": logs, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/api-logs/stats", summary="API 调用统计")
async def get_api_logs_stats(days: int = 7, db: AsyncSession = Depends(get_db)):
    """行为数据聚合统计：高频概念、路由方式、日均查询量。"""
    try:
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(days=days)).isoformat()

        # 概念类型映射（entity=业务 / dictionary=字典 / role=角色），只统计业务概念
        concept_types = {}
        try:
            from app.services.ontology_service import ontology_service
            await ontology_service.load()
            concept_types = {
                c.get("name"): (c.get("conceptType") or "entity")
                for c in ontology_service.get_concepts()
            }
        except Exception:
            concept_types = {}

        repo = ApiLogRepository(db)
        rows, total = await repo.query_logs(
            page=1, page_size=10000, date_from=since,
        )

        method_count = {}
        concept_count = {}
        daily_count = {}
        total_elapsed = 0
        followup_count = 0
        for log in rows:
            m = log.method or "other"
            # HTTP 方法（REST 直查）归为 rest，不是路由方式
            if m.upper() in ("GET", "POST", "PUT", "DELETE", "OPTIONS"):
                m = "rest"
            method_count[m] = method_count.get(m, 0) + 1
            c = log.concept or "(未分类)"
            # 工具名归一：WorkOrder_query / WorkOrder_create → 概念 WorkOrder
            if c in concept_types:
                pass
            else:
                base = c.split("_")[0]
                if concept_types.get(base) == "entity":
                    c = base
            # 只统计业务概念（entity）——依赖本体 conceptType（dictionary/role 排除），不写死后缀
            ct = concept_types.get(c, "")
            if c == "dynamic_plan" or c == "NONE" or c == "(未分类)" or (ct and ct != "entity"):
                continue
            concept_count[c] = concept_count.get(c, 0) + 1
            date_key = (log.timestamp or "")[:10]
            daily_count[date_key] = daily_count.get(date_key, 0) + 1
            total_elapsed += log.elapsed_ms or 0
            try:
                import json
                ctx = json.loads(log.context or "{}")
                if ctx.get("is_followup"):
                    followup_count += 1
            except Exception:
                pass

        avg_ms = round(total_elapsed / max(total, 1))
        dynamic_rate = round(method_count.get("dynamic", 0) / max(total, 1) * 100, 1)
        trigger_rate = round(method_count.get("trigger", 0) / max(total, 1) * 100, 1)
        followup_rate = round(followup_count / max(total, 1) * 100, 1)

        top_concepts = sorted(concept_count.items(), key=lambda x: -x[1])[:10]

        return {
            "ok": True,
            "total": total,
            "days": days,
            "avgMs": avg_ms,
            "methodDistribution": method_count,
            "topConcepts": [{"concept": c, "count": n} for c, n in top_concepts],
            "dailyTrend": [{"date": d, "count": n} for d, n in sorted(daily_count.items())],
            "dynamicRate": dynamic_rate,
            "triggerRate": trigger_rate,
            "followupRate": followup_rate,
        }
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/{chain_id}", summary="获取单条链条")
async def get_chain(chain_id: str, db: AsyncSession = Depends(get_db)):
    repo = ChainRepository(db)
    chain = await repo.get_by_id(chain_id)
    if not chain:
        raise HTTPException(404, f"链条不存在: {chain_id}")
    return ChainOut(
        chain_id=chain.chain_id,
        name=chain.name or "",
        description=chain.description or "",
        triggers=json.loads(chain.triggers or "[]"),
        final_prompt_template=chain.final_prompt_template or "",
        focus_concepts=chain.focus_concepts or "",
        mode=chain.mode or "merged",
        enabled=bool(chain.enabled),
        verify_target=chain.verify_target or "",
        created_at=str(chain.created_at) if chain.created_at else "",
        updated_at=str(chain.updated_at) if chain.updated_at else "",
        steps=[ChainStepIn(
            step_order=s.step_order,
            step_id=s.step_id or "",
            description=s.description or "",
            agent_name=s.agent_name or "",
            prompt_template=s.prompt_template or "",
            output_key=s.output_key or "",
            focus_concepts=s.focus_concepts or "",
            action_name=s.action_name or "",
            action_params=s.action_params or "{}",
            precondition=s.precondition or "",
            on_failure=s.on_failure or "abort",
        ) for s in (chain.steps or [])],
    )


@router.post("", summary="创建链条")
async def create_chain(chain: ChainIn, db: AsyncSession = Depends(get_db)):
    repo = ChainRepository(db)
    existing = await repo.get_by_id(chain.chain_id)
    if existing:
        raise HTTPException(409, f"链条已存在: {chain.chain_id}")
    await repo.create(
        chain_id=chain.chain_id, name=chain.name, description=chain.description,
        triggers=chain.triggers, final_prompt_template=chain.final_prompt_template,
        focus_concepts=chain.focus_concepts, enabled=chain.enabled, source="manual",
        mode=chain.mode, verify_target=chain.verify_target or "",
        steps=[s.model_dump() for s in chain.steps],
    )
    reload_chains()
    return {"ok": True, "chain_id": chain.chain_id}


@router.put("/{chain_id}", summary="更新链条")
async def update_chain(chain_id: str, chain: ChainIn, db: AsyncSession = Depends(get_db)):
    repo = ChainRepository(db)
    existing = await repo.get_by_id(chain_id)
    if not existing:
        raise HTTPException(404, f"链条不存在: {chain_id}")
    await repo.update(
        chain_id=chain_id, name=chain.name, description=chain.description,
        triggers=chain.triggers, final_prompt_template=chain.final_prompt_template,
        focus_concepts=chain.focus_concepts, enabled=chain.enabled,
        mode=chain.mode, verify_target=chain.verify_target or "",
        steps=[s.model_dump() for s in chain.steps],
    )
    reload_chains()
    return {"ok": True, "chain_id": chain_id}


@router.delete("/{chain_id}", summary="删除链条")
async def delete_chain(chain_id: str, db: AsyncSession = Depends(get_db)):
    repo = ChainRepository(db)
    existing = await repo.get_by_id(chain_id)
    if not existing:
        raise HTTPException(404, f"链条不存在: {chain_id}")
    await repo.delete(chain_id)
    reload_chains()
    return {"ok": True, "chain_id": chain_id}


@router.post("/reload", summary="重新加载链条缓存")
async def reload():
    await reload_chains_async()
    return {"ok": True, "message": "链引擎缓存已刷新"}


@router.get("/compile/auto-rollback", summary="获取自动回滚开关")
async def get_auto_rollback():
    """读取验证未通过时是否自动回滚（DB 配置优先，回退 .env）。"""
    from app.services.neo4j_service import _get_sys_cfg
    try:
        cfg = await _get_sys_cfg("auto_rollback_on_verify_fail")
        enabled = (cfg or "").lower() == "true"
    except Exception:
        enabled = False
    return {"enabled": enabled}


@router.post("/compile/auto-rollback", summary="设置自动回滚开关")
async def set_auto_rollback(data: dict):
    """设置验证未通过时是否自动执行回滚链（存 DB，优先级高于 .env）。

    高风险操作，默认关闭（仅标记需人工复核）。
    """
    from app.models.system_config import SystemConfig
    from sqlalchemy import select
    enabled = bool(data.get("enabled"))
    val = "true" if enabled else "false"
    try:
        async for session in get_db():
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == "auto_rollback_on_verify_fail")
            )
            cfg = result.scalar_one_or_none()
            if cfg:
                cfg.value = val
            else:
                session.add(SystemConfig(
                    key="auto_rollback_on_verify_fail", value=val,
                    description="验证未通过时是否自动执行回滚链",
                ))
            await session.commit()
        return {"enabled": enabled}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/compile/config", summary="获取编译器领域配置")
async def get_compile_config():
    """从 DB 读取当前 namespace 的业务域配置。"""
    try:
        ns = await _get_active_namespace()
        config = await _load_config(ns, "domains")
        dirty = not config.pop("_applied", True)
        return {"ok": True, "config": config, "dirty": dirty}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.put("/compile/config", summary="更新编译器领域配置")
async def update_compile_config(data: dict):
    """写入当前 namespace 的业务域配置到 DB。"""
    try:
        ns = await _get_active_namespace()
        config = data.get("config", {})
        config["_applied"] = False
        await _save_config(ns, "domains", config)
        return {"ok": True, "message": "已保存"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/compile/systems", summary="获取 API 系统配置")
async def get_system_config():
    """从 DB 读取当前 namespace 的系统配置，含应用状态 + 运行时路由表。"""
    try:
        ns = await _get_active_namespace()
        config = await _load_config(ns, "systems")
        dirty = not config.get("_applied", True) if config else False
        # 附上 multi_system_backend 实时路由表
        routing = {}
        try:
            from app.services.multi_system_backend import multi_system_backend
            routing = dict(multi_system_backend._concept_system)
        except Exception:
            pass
        return {"ok": True, "config": config, "dirty": dirty, "routing": routing}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.put("/compile/systems", summary="更新 API 系统配置")
async def update_system_config(data: dict):
    """保存配置到 DB，保留传入的 _applied 标记。

    安全保护：仅传 _applied 时合并到已有配置，防止误覆盖。
    """
    try:
        ns = await _get_active_namespace()
        config = data.get("config", {})
        # 安全保护: 如果传入的 config 没有实质性内容（无 systems 或 systems 为空），合并已有配置
        existing = await _load_config(ns, "systems")
        if existing:
            incoming_systems = config.get("systems", {})
            if not incoming_systems or all(not v for v in incoming_systems.values()):
                # 传入的是空/占位 systems，用已有数据，只更新 _applied
                existing["_applied"] = config.get("_applied", existing.get("_applied", True))
                config = existing
        await _save_config(ns, "systems", config)
        # 如果标记为已应用，立即刷新路由表
        if config.get("_applied", False):
            try:
                from app.services.multi_system_backend import multi_system_backend
                await multi_system_backend.load_configs()
            except Exception:
                pass
        return {"ok": True, "message": "已保存"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/compile/systems/toggle", summary="应用 API 系统配置")
async def toggle_applied():
    """标记系统配置为已应用并刷新 multi_system_backend，不重编译。"""
    try:
        ns = await _get_active_namespace()
        config = await _load_config(ns, "systems")
        if not config:
            return {"ok": False, "message": "无配置"}

        config["_applied"] = not config.get("_applied", True)
        await _save_config(ns, "systems", config)

        from app.services.multi_system_backend import multi_system_backend
        await multi_system_backend.load_configs()

        state = "已应用" if config["_applied"] else "未应用"
        return {"ok": True, "message": f"API 配置已切换为「{state}」", "applied": config["_applied"]}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/compile/systems/{system_name}/test", summary="测试系统连接")
async def test_system_connection(system_name: str):
    """测试 API 系统的连通性。使用草稿配置。"""
    try:
        from app.services.multi_system_backend import multi_system_backend
        await multi_system_backend.load_configs(force=True)  # 测试时忽略 _applied
        result = await multi_system_backend.test_connection(system_name)
        return result
    except Exception as e:
        return {"ok": False, "message": _translate_conn_error(str(e), "")}


@router.post("/compile/systems/{system_name}/test-endpoint", summary="测试单个接口")
async def test_endpoint(system_name: str, data: dict):
    """测试单个 API 接口，忽略应用状态。"""
    try:
        from app.services.multi_system_backend import multi_system_backend
        await multi_system_backend.load_configs(force=True)  # 测试时忽略 _applied
        concept = data.get("concept", "")
        ep_idx = data.get("ep_idx", 0)

        # 获取系统配置
        result = {"ok": False, "message": "", "raw": None, "fields": []}
        if system_name not in multi_system_backend._systems:
            result["message"] = f"系统 {system_name} 不存在"
            return result

        system = multi_system_backend._systems[system_name]
        if not system.is_api:
            result["message"] = "非 API 系统"
            return result

        client = await multi_system_backend._get_client(system)
        ep = multi_system_backend._resolve_endpoint(concept, system)
        path = ep.get("path", f"/api/{concept.lower()}")
        method = ep.get("method", "GET").upper()
        fmt = ep.get("format", "json")

        import time
        t0 = time.time()
        try:
            if method == "POST":
                resp = await client.post(path, json={} if fmt == "json" else {})
            else:
                resp = await client.get(path)
            elapsed = int((time.time() - t0) * 1000)
            ct = resp.headers.get("content-type", "")
            if "xml" in ct:
                import xml.etree.ElementTree as ET
                root_el = ET.fromstring(resp.text)
                data = {"_raw": resp.text}
            else:
                data = resp.json()
            multi_system_backend._log_request(method, f"{system.base_url}{path}", resp.status_code, elapsed, None)

            # 提取字段列表
            fields = []
            if isinstance(data, dict) and "_raw" not in data:
                root = ep.get("response", {}).get("root", "")
                items = data
                if root:
                    for part in root.split("."):
                        if isinstance(items, dict) and part in items:
                            items = items[part]
                        else:
                            items = {}
                            break
                if isinstance(items, list) and items:
                    items = items[0]
                if isinstance(items, dict):
                    fields = list(items.keys())
            elif isinstance(data, list) and data:
                fields = list(data[0].keys())

            result.update({"ok": True, "status": resp.status_code, "elapsed_ms": elapsed, "raw": data, "fields": fields})
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            multi_system_backend._log_request(method, f"{system.base_url}{path}", 0, elapsed, str(e))
            result["message"] = _translate_conn_error(str(e), system.base_url)

        return result
    except Exception as e:
        return {"ok": False, "message": _translate_conn_error(str(e), "")}


def _translate_conn_error(msg: str, url: str = "") -> str:
    """将 httpx 连接错误翻译为中文。"""
    if "connection" in msg.lower() or "connect" in msg.lower():
        return f"无法连接{'到 ' + url if url else ''}，请检查地址和网络"
    if "timeout" in msg.lower():
        return f"连接{' ' + url if url else ''}超时"
    return msg


@router.get("/compile/debug", summary="调试: 查看概念的映射数据")
def compile_debug():
    """临时调试端点: 检查概念是否有 mappings。"""
    try:
        from app.services.ontology_service import ontology_service
        concepts = ontology_service.get_concepts() or []
        samples = []
        for c in concepts[:10]:
            name = c["name"]
            props = c.get("properties", [])
            has_map = any(m for p in props for m in p.get("mappings", []))
            has_pri = any(p.get("isPrimary") for p in props)
            samples.append({"name": name, "props": len(props), "has_mapping": has_map, "has_primary": has_pri})
        return {"ok": True, "total": len(concepts), "samples": samples}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def _get_active_namespace() -> str:
    """从 DB 读取活跃 namespace，fallback 到文件再 fallback 到默认值。"""
    config = await _load_config("_system", "active_namespace")
    if config and config.get("namespace"):
        return config["namespace"]
    try:
        import os
        ns_file = os.path.join(os.path.dirname(__file__), "..", "..", "config", "active_namespace.txt")
        if os.path.exists(ns_file):
            with open(ns_file, encoding="utf-8") as f:
                ns = f.read().strip()
                if ns: return ns
    except Exception:
        pass
    return "manufacturing"

async def _set_active_namespace(ns: str):
    """写入 DB，同时同步 ontology_service 缓存和文件。"""
    await _save_config("_system", "active_namespace", {"namespace": ns})
    try:
        from app.services.ontology_service import OntologyService, ontology_service
        OntologyService._cached_ns = ns
        # 强制刷新缓存，加载新 namespace 的概念数据
        ontology_service._data = None
        ontology_service._loaded_at = None
    except Exception:
        pass
    try:
        import os
        ns_file = os.path.join(os.path.dirname(__file__), "..", "..", "config", "active_namespace.txt")
        os.makedirs(os.path.dirname(ns_file), exist_ok=True)
        with open(ns_file, "w", encoding="utf-8") as f:
            f.write(ns)
    except Exception:
        pass


async def _load_config_async(db: AsyncSession, namespace: str, config_type: str) -> dict:
    """从 DB 读取配置（异步版本）。"""
    repo = NamespaceConfigRepository(db)
    return await repo.get(namespace, config_type)


async def _save_config_async(db: AsyncSession, namespace: str, config_type: str, config: dict):
    """写入配置到 DB（异步版本）。"""
    repo = NamespaceConfigRepository(db)
    await repo.save(namespace, config_type, config)


async def _load_config(namespace: str, config_type: str) -> dict:
    """从 DB 读取配置。"""
    from app.db import get_db
    async for session in get_db():
        repo = NamespaceConfigRepository(session)
        return await repo.get(namespace, config_type)
    return {}

async def _save_config(namespace: str, config_type: str, config: dict):
    """写入配置到 DB。"""
    from app.db import get_db
    async for session in get_db():
        repo = NamespaceConfigRepository(session)
        await repo.save(namespace, config_type, config)

def _get_domains_path(ns: str = None) -> str:
    """兼容旧调用, 实际已走 DB。"""
    return ""


@router.get("/compile/namespaces", summary="获取可用的行业命名空间")
async def list_namespaces():
    """从 Neo4j Concept 元数据查询所有 namespace。"""
    try:
        from app.services.neo4j_service import neo4j_service
        if not neo4j_service.connected:
            await neo4j_service.connect()
        if neo4j_service.connected:
            records = await neo4j_service.execute_read(
                "MATCH (c:Concept) WHERE c.namespace IS NOT NULL AND c.namespace <> '' "
                "RETURN DISTINCT c.namespace AS ns ORDER BY ns", {}
            )
            namespaces = [r["ns"] for r in records] if records else []
            # 从 Neo4j Project 节点获取项目名称作为 label
            labels = {}
            try:
                proj_records = await neo4j_service.execute_read(
                    "MATCH (p:Project) WHERE p.namespace IN $ns_list RETURN p.namespace AS ns, p.name AS name",
                    {"ns_list": namespaces}
                )
                for r in (proj_records or []):
                    labels[r["ns"]] = r["name"] or r["ns"]
            except Exception:
                pass
            return {"ok": True, "active": await _get_active_namespace(), "namespaces": namespaces, "labels": labels}
    except Exception as e:
        return {"ok": False, "message": str(e), "namespaces": ["manufacturing"]}


@router.post("/compile/derive", summary="推导业务域（不编译，只生成域配置）")
async def derive_domains(mode: str = "rule", db: AsyncSession = Depends(get_db)):
    try:
        from app.agents.compiler import OntologyCompiler
        compiler = OntologyCompiler()
        from app.services.ontology_service import ontology_service, OntologyService
        ns = await _get_active_namespace()
        OntologyService._cached_ns = ns
        await ontology_service.reload()
        await compiler._load_ontology()
        # 推导只用当前 namespace 的概念（从 Neo4j 直接过滤）
        if ns:
            active_cm = await _load_concept_map_from_neo4j(ns)
            compiler._concepts = [c for c in compiler._concepts if c["name"] in active_cm]
            compiler._concept_map = {c["name"]: c for c in compiler._concepts}
        if mode == "llm":
            result = await compiler._llm_derive_domains()
        else:
            result = compiler._derive_domains_from_ontology()
        if not result:
            return {"ok": False, "message": "推导完成: 0 个域"}
        result["_applied"] = False
        await _save_config_async(db, ns, "domains", result)
        return {"ok": True, "message": f"推导完成: {len(result)} 个域", "domains": len(result)}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/compile/derive/stream", summary="流式推导业务域（LLM思考过程可见）")
async def derive_domains_stream(mode: str = "rule", db: AsyncSession = Depends(get_db)):
    """SSE 流式输出 LLM 推导的思考过程和结果。"""
    from fastapi.responses import StreamingResponse
    import json as _json

    async def generate():
        try:
            from app.agents.compiler import OntologyCompiler
            compiler = OntologyCompiler()
            from app.services.ontology_service import ontology_service, OntologyService
            ns = await _get_active_namespace()
            OntologyService._cached_ns = ns
            await ontology_service.reload()
            await compiler._load_ontology()
            # 推导只用当前 namespace 的概念
            if ns:
                active_cm = await _load_concept_map_from_neo4j(ns)
                compiler._concepts = [c for c in compiler._concepts if c["name"] in active_cm]
                compiler._concept_map = {c["name"]: c for c in compiler._concepts}

            if mode == "rule":
                result = compiler._derive_domains_from_ontology()
                if not result:
                    yield f"data: {_json.dumps({'type': 'error', 'message': '推导完成: 0 个域'})}\n\n"
                    return
                result["_applied"] = False
                await _save_config_async(db, ns, "domains", result)
                yield f"data: {_json.dumps({'type': 'done', 'domains': len(result), 'message': f'推导完成: {len(result)} 个域'})}\n\n"
                return

            # LLM 流式推导
            from app.services.llm_service import llm_service
            import json
            concepts_info = []
            for c in compiler._concepts:
                label = c.get("label", "") or c["name"]
                concepts_info.append({
                    "name": c["name"], "label": label,
                    "description": c.get("description", ""),
                    "parents": c.get("parents", []),
                })
            if len(concepts_info) < 3:
                yield f"data: {_json.dumps({'type': 'error', 'message': '概念数不足，至少需要3个'})}\n\n"
                return

            # 补充关系信息
            relations_info = []
            for c in compiler._concepts:
                cn = c["name"]
                for rel in c.get("relations", []):
                    target = rel.get("target", "")
                    if target:
                        relations_info.append(f"{cn} → {target} ({rel.get('label', '')})")

            prompt = f"""你是企业业务架构师。请根据以下本体概念和它们之间的关系，将概念分组为 3-8 个业务域。

## 分组原则
- 概念之间有 HasMany/HasOne 关系的，尽量放在同一个域
- 同一父概念下的子概念放在同一个域
- 语义相近的概念（如都属于"质量"范畴）放在一起
- 每个域的概念数尽量均匀（5-15个为宜）
- 域名称、描述、icon 全部用中文，域名用英文key（如 quality_management）

## 概念列表
{json.dumps(concepts_info, ensure_ascii=False, indent=2)}

## 概念间关系
{chr(10).join(relations_info[:50]) if relations_info else "（无显式关系）"}

## 输出格式 (严格JSON，不要markdown包裹)
{{
  "domain_key": {{
    "display_name": "域中文名",
    "description": "该域涵盖的业务范围描述",
    "icon": "emoji",
    "concepts": ["ConceptA", "ConceptB"]
  }}
}}"""

            from app.agents.settings.model import MODEL_CONFIG
            response = ""
            async for chunk_type, chunk_content in llm_service.chat_stream(
                message=prompt, session_id="compiler_domains",
                system_prompt="你是企业业务架构师。所有输出必须严格使用中文。根据概念语义和关系进行业务域划分，确保域内高内聚、域间低耦合。只输出JSON。",
                model_name=MODEL_CONFIG.get("decision_model"), enable_thinking=True, tools=None,
            ):
                if chunk_type == 'thinking':
                    yield f"data: {_json.dumps({'type': 'thinking', 'text': chunk_content})}\n\n"
                elif chunk_type == 'content':
                    response += chunk_content
                    yield f"data: {_json.dumps({'type': 'content', 'text': chunk_content})}\n\n"

            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1].rsplit("\n", 1)[0]
            result = json.loads(response)
            if isinstance(result, dict) and len(result) >= 2:
                result["_applied"] = False
                await _save_config_async(db, ns, "domains", result)
                yield f"data: {_json.dumps({'type': 'done', 'domains': len(result), 'message': f'推导完成: {len(result)} 个域'})}\n\n"
            else:
                yield f"data: {_json.dumps({'type': 'error', 'message': 'LLM返回格式无效'})}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/compile/config/undo", summary="撤销应用，禁用Agent并标记未应用")
async def undo_config(db: AsyncSession = Depends(get_db)):
    ns = await _get_active_namespace()
    repo = NamespaceConfigRepository(db)
    config = await repo.get(ns, "domains")
    config["_applied"] = False
    await repo.save(ns, "domains", config)
    from app.repositories.agent_repository import AgentRepository
    agent_repo = AgentRepository(db)
    for a in await agent_repo.get_all():
        await agent_repo.update(a.name, enabled=False)
    return {"ok": True, "message": "已撤销"}


@router.get("/compile/config/history", summary="获取配置版本历史")
async def config_history(db: AsyncSession = Depends(get_db)):
    """列出当前 namespace 的配置备份版本，含详情。"""
    ns = await _get_active_namespace()
    # 加载概念标签映射
    label_map = {}
    try:
        from app.services.ontology_service import ontology_service
        for c in (ontology_service.get_concepts() or []):
            if c.get("label") and c.get("label") != c.get("name"):
                label_map[c["name"]] = c["label"]
    except Exception:
        pass

    import json as _json
    from sqlalchemy import select
    from app.models.namespace_config import NamespaceConfig

    repo = NamespaceConfigRepository(db)

    # 当前活跃配置
    active = await repo.get(ns, "domains")
    active_cfg = active if active else {}
    active_is_empty = not any(k != "mode" for k in active_cfg)

    # 备份列表（含 updated_at）
    backups_result = await db.execute(
        select(NamespaceConfig).where(
            NamespaceConfig.namespace == ns,
            NamespaceConfig.config_type.like("domains_backup_%"),
        ).order_by(NamespaceConfig.updated_at.desc()).limit(50)
    )
    backup_rows = backups_result.scalars().all()
    total = len(backup_rows)

    # 查询活跃配置的 updated_at
    active_row_result = await db.execute(
        select(NamespaceConfig.updated_at).where(
            NamespaceConfig.namespace == ns,
            NamespaceConfig.config_type == "domains",
        )
    )
    active_updated_at = active_row_result.scalar_one_or_none() or ""

    versions = []
    # 当前活跃版本
    if not active_is_empty:
        versions.append({
            "version": "current",
            "version_no": f"V{total + 1}",
            "is_active": True,
            "updated_at": active_updated_at,
            "domain_count": len([k for k in active_cfg if k != "mode"]),
            "concept_count": sum(len(v.get("concepts",[])) for v in active_cfg.values() if isinstance(v, dict)),
            "domains": [{"name": k, "display_name": v.get("display_name",""), "concept_count": len(v.get("concepts",[])), "icon": v.get("icon",""), "concepts": [(label_map.get(cn, cn)) for cn in v.get("concepts",[])[:15]]} for k,v in active_cfg.items() if isinstance(v, dict)],
        })
    for i, r in enumerate(backup_rows):
        try:
            cfg = _json.loads(r.config_data) if r.config_data else {}
            # 跳过纯标记备份（只含 mode/_applied，无实际域数据）
            if not any(k not in ("mode", "_applied") for k in cfg):
                continue
            domain_count = len([k for k in cfg if k != "mode"])
            concept_count = sum(len(v.get("concepts",[])) for v in cfg.values() if isinstance(v, dict))
            versions.append({
                "version": r.config_type.replace("domains_backup_", ""),
                "version_no": f"V{total - i}",
                "is_active": False,
                "updated_at": r.updated_at or "",
                "domain_count": domain_count,
                "concept_count": concept_count,
                "domains": [{"name": k, "display_name": v.get("display_name",""), "concept_count": len(v.get("concepts",[])), "icon": v.get("icon",""), "concepts": [(label_map.get(cn, cn)) for cn in v.get("concepts",[])[:15]]} for k,v in cfg.items() if isinstance(v, dict)],
            })
        except Exception:
            pass
    return {"ok": True, "versions": versions}


@router.delete("/compile/config/history/{version}", summary="删除配置版本")
async def delete_config_version(version: str, db: AsyncSession = Depends(get_db)):
    """删除指定版本的历史配置。"""
    ns = await _get_active_namespace()
    repo = NamespaceConfigRepository(db)
    await repo.delete(ns, f"domains_backup_{version}")
    return {"ok": True, "message": f"已删除版本 {version}"}


@router.post("/compile/config/restore/{version}", summary="恢复配置版本")
async def restore_config(version: str, db: AsyncSession = Depends(get_db)):
    """从备份恢复域配置 (不产生新备份)。"""
    ns = await _get_active_namespace()
    repo = NamespaceConfigRepository(db)
    backup = await repo.get(ns, f"domains_backup_{version}")
    if backup:
        await repo.save(ns, "domains", backup)
        return {"ok": True, "message": f"已恢复版本 {version}"}
    return {"ok": False, "message": "版本不存在"}


@router.post("/compile/namespace/{name}", summary="切换行业命名空间")
async def switch_namespace(name: str):
    """切换活跃命名空间 → 编译器自动从本体推导领域分组。

    仅编译预览（sync_to_db=False），不写入 agent.db、不刷新 AGENT_DEFINITIONS——
    对话路由保持旧业务域，直到用户点击「全部应用」才真正切换。
    """
    await _set_active_namespace(name)
    from app.services.ontology_service import ontology_service
    await ontology_service.reload()

    # 切换后标记目标 namespace 的配置为「未应用」，前端业务域配置区显示「● 未应用」，
    # 提示用户需点「全部应用」才真正生效（对话路由等）。
    try:
        _dconfig = await _load_config(name, "domains")
        if isinstance(_dconfig, dict) and any(k not in ("mode", "_applied") for k in _dconfig):
            _dconfig["_applied"] = False
            await _save_config(name, "domains", _dconfig)
        _sconfig = await _load_config(name, "systems")
        if isinstance(_sconfig, dict) and _sconfig:
            _sconfig["_applied"] = False
            await _save_config(name, "systems", _sconfig)
    except Exception:
        pass

    from app.agents import compile_and_register
    from app.core.chain_engine import reload_chains
    runtime = await compile_and_register(sync_to_db=False)
    reload_chains()
    if runtime:
        active_cm = await _load_concept_map_from_neo4j(name)
        return {"ok": True, "message": f"已切换至 {name}: {len(active_cm)}概念 {len(runtime.agents)}业务域（请点击「全部应用」生效）", "has_agents": True}
    return {"ok": True, "message": f"已切换至 {name}，该本体暂无业务域配置，请在业务域配置中点击规则推导", "has_agents": False}


async def _load_concept_map_from_neo4j(ns: str) -> dict:
    """从 Neo4j 实时查询概念树，不走缓存。"""
    try:
        from app.services.neo4j_service import neo4j_service
        if not neo4j_service.connected:
            await neo4j_service.connect()
        if not neo4j_service.connected:
            return {}
        ns_filter = " {namespace: $ns}" if ns else ""
        records = await neo4j_service.execute_read(
            f"MATCH (c:Concept{ns_filter}) RETURN c.name, c.label, c.parents, c.seq, c.namespace",
            {"ns": ns} if ns else None,
        )
        concept_map = {}
        for r in records:
            parents = r.get("c.parents", "[]")
            if isinstance(parents, str):
                try:
                    parents = json.loads(parents)
                except (json.JSONDecodeError, TypeError):
                    parents = []
            concept_map[r["c.name"]] = {
                "label": r.get("c.label") or r["c.name"],
                "parents": parents if isinstance(parents, list) else [],
                "seq": r.get("c.seq", 999),
                # 不传 ns 时从节点取 namespace（多本体并存场景）
                "namespace": r.get("c.namespace") or ns or "",
            }
        # 补充 Action 信息（去重保序，排除 query）
        action_records = await neo4j_service.execute_read(
            f"MATCH (c:Concept{ns_filter})-[:HAS_ACTION]->(a:Action{ns_filter}) "
            "RETURN c.name, a.name, a.label ORDER BY c.name, a.name",
            {"ns": ns} if ns else None,
        )
        for ar in action_records:
            cn = ar["c.name"]
            if cn in concept_map:
                an = ar.get("a.name", "")
                concept_map[cn].setdefault("actions", []).append({
                    "name": an,
                    "label": ar.get("a.label", an) or an,
                })
        return concept_map
    except Exception:
        return {}


@router.get("/compile/status", summary="获取编译器状态")
async def compile_status():
    """返回最近一次编译的统计信息。"""
    try:
        from app.agents import get_compiled_runtime
        runtime = get_compiled_runtime()
        if runtime:
            active_ns = await _get_active_namespace()
            concept_map = await _load_concept_map_from_neo4j(active_ns)
            return {
                "ok": True,
                "concept_map": concept_map,
                "active_concepts": list(concept_map.keys()),
                "compiled_at": runtime.compiled_at,
                "concept_count": runtime.concept_count,
                "skill_count": len(runtime.skills),
                "chain_count": len(runtime.chains),
                "agent_count": len(runtime.agents),
                "agents": [
                    {
                        "name": a.name,
                        "display_name": a.display_name,
                        "icon": a.icon,
                        "skill_count": len(a.skill_names),
                        "chain_count": len(a.chain_names),
                        "chains": [
                            {"name": c.name, "display_name": c.display_name,
                             "path": c.path, "description": c.description}
                            for c in runtime.chains
                            if c.name in a.chain_names
                        ][:5],  # 最多显示 5 条链
                    }
                    for a in runtime.agents
                ],
                "skills": [
                    {"name": s.name, "display_name": s.display_name,
                     "concept": s.concept, "concept_label": s.concept_label,
                     "data_source_type": s.data_source.type if s.data_source else "neo4j",
                     "triggers": s.triggers,
                     "agent": _find_agent_for_concept(runtime, s.concept),
                     "output_fields": [{"name": f.name, "label": f.label, "type": f.type} for f in s.output_fields]}
                    for s in runtime.skills[:50]
                    if s.concept in concept_map
                ],
            }
        active_ns = await _get_active_namespace()
        concept_map = await _load_concept_map_from_neo4j(active_ns)
        return {"ok": False, "message": "编译器尚未运行", "concept_map": concept_map, "active_concepts": list(concept_map.keys())}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _find_agent_for_concept(runtime, concept: str) -> str:
    for a in runtime.agents:
        for sn in a.skill_names:
            if sn.startswith(f"{concept}_"):
                return a.display_name
    return ""


@router.post("/compile/reload", summary="重新编译本体 → 刷新 Skill + Agent + 链")
async def compile_reload(db: AsyncSession = Depends(get_db)):
    """标记配置为已应用并触发编译器重新运行。"""
    try:
        ns = await _get_active_namespace()

        # 标记为已应用（编译器据此判断是否生效）
        for config_type in ("systems", "domains"):
            config = await _load_config_async(db, ns, config_type)
            if config and not config.get("_applied", True):
                config["_applied"] = True
                await _save_config_async(db, ns, config_type, config)
                from app.core.logger import log
                log.info(f"[API] 应用配置: {config_type}")

        from app.agents import compile_and_register
        from app.core.chain_engine import reload_chains as reload_chain_engine

        runtime = await compile_and_register(sync_to_db=True)
        # 应用后立即刷新 AGENT_DEFINITIONS，对话路由切换到新业务域
        reload_agents()
        # 刷新多系统后端配置，使新增/变更的 API 系统立即生效
        try:
            from app.services.multi_system_backend import multi_system_backend
            await multi_system_backend.load_configs()
        except Exception:
            pass
        if runtime:
            reload_chain_engine()
            active_ns = await _get_active_namespace()
            active_cm = await _load_concept_map_from_neo4j(active_ns)
            return {
                "ok": True,
                "message": f"应用完成: {len(active_cm)}概念, "
                           f"{len(runtime.skills)}Skill, "
                           f"{len(runtime.agents)}Agent, "
                           f"{len(runtime.chains)}链",
                "skills": len(runtime.skills),
                "agents": len(runtime.agents),
                "chains": len(runtime.chains),
            }
        else:
            reload_chain_engine()
            return {"ok": True, "message": "应用完成: 无业务域配置, Agent 列表已清空", "agents": 0}
    except Exception as e:
        from app.core.logger import log
        log.error(f"[API] 应用失败: {e}")
        return {"ok": False, "message": f"应用失败: {e}"}


@router.get("/compile/skill-overrides", summary="获取 Skill 覆盖配置")
async def get_skill_overrides():
    """从 DB 读取当前 namespace 的 Skill 覆盖（触发词、启用状态）。"""
    try:
        ns = await _get_active_namespace()
        return {"ok": True, "overrides": await _load_config(ns, "skill_overrides")}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.put("/compile/skill-overrides", summary="保存 Skill 覆盖配置")
async def save_skill_overrides(data: dict):
    """写入当前 namespace 的 Skill 覆盖到 DB。"""
    try:
        ns = await _get_active_namespace()
        await _save_config(ns, "skill_overrides", data.get("overrides", {}))
        return {"ok": True, "message": "已保存"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/agents/list", summary="获取可用 Agent 列表（供链条配置引用）")
async def list_agents():
    from app.db import get_db
    from app.repositories.agent_repository import AgentRepository
    agents = {}
    async for session in get_db():
        repo = AgentRepository(session)
        for a in await repo.get_enabled_agents():
            agents[a.name] = {
                "name": a.name,
                "display_name": a.display_name,
                "description": a.description,
                "icon": a.icon,
            }
    return list(agents.values())


