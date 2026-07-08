"""链条管理 API — agent.db 中 chains 和 chain_steps 表的增删改查。"""

import json
import os
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.chain_engine import reload_chains
from app.agents.agent_config import AGENT_DEFINITIONS, reload as reload_agents

router = APIRouter(prefix="/chains", tags=["链条管理"])

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agent.db")


def _get_conn():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── Pydantic 模型 ─────────────────────────────────────────────────

class ChainStepIn(BaseModel):
    step_order: int = 0
    step_id: str = ""
    description: str = ""
    agent_name: str = "analysis_monitor"
    prompt_template: str = ""
    output_key: str = ""
    focus_concepts: str = ""  # 该步骤查询的概念，逗号分隔


class ChainIn(BaseModel):
    chain_id: str
    name: str = ""
    description: str = ""
    triggers: list[str] = []
    final_prompt_template: str = ""
    focus_concepts: str = ""
    enabled: bool = True
    steps: list[ChainStepIn] = []


class ChainOut(BaseModel):
    chain_id: str
    name: str
    description: str
    triggers: list[str]
    final_prompt_template: str
    focus_concepts: str = ""
    enabled: bool
    created_at: str = ""
    updated_at: str = ""
    steps: list[ChainStepIn] = []


# ── 路由 ──────────────────────────────────────────────────────────

@router.get("", summary="获取所有链条")
def list_chains():
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM chains ORDER BY chain_id")
        chains = []
        for row in c.fetchall():
            r = dict(row)
            chain_id = r["chain_id"]
            c.execute("SELECT * FROM chain_steps WHERE chain_id=? ORDER BY step_order", (chain_id,))
            steps = [
                ChainStepIn(
                    step_order=s["step_order"],
                    step_id=s["step_id"],
                    description=s.get("description", ""),
                    agent_name=s["agent_name"],
                    prompt_template=s.get("prompt_template", ""),
                    output_key=s.get("output_key", ""),
                    focus_concepts=s.get("focus_concepts", ""),
                )
                for s in (dict(sr) for sr in c.fetchall())
            ]
            chains.append(ChainOut(
                chain_id=chain_id,
                name=r.get("name", ""),
                description=r.get("description", ""),
                triggers=json.loads(r.get("triggers", "[]")),
                final_prompt_template=r.get("final_prompt_template", ""),
                focus_concepts=r.get("focus_concepts", ""),
                enabled=bool(r.get("enabled", 1)),
                created_at=r.get("created_at", ""),
                updated_at=r.get("updated_at", ""),
                steps=steps,
            ))
        return chains
    finally:
        conn.close()


@router.get("/concepts", summary="获取本体概念列表（供链条配置引用）")
def list_concepts():
    from app.services.ontology_service import ontology_service
    return ontology_service.get_concepts()


@router.get("/{chain_id}", summary="获取单条链条")
def get_chain(chain_id: str):
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM chains WHERE chain_id=?", (chain_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(404, f"链条不存在: {chain_id}")
        r = dict(row)
        c.execute("SELECT * FROM chain_steps WHERE chain_id=? ORDER BY step_order", (chain_id,))
        steps = [
            ChainStepIn(
                step_order=s["step_order"],
                step_id=s["step_id"],
                description=s.get("description", ""),
                agent_name=s["agent_name"],
                prompt_template=s.get("prompt_template", ""),
                output_key=s.get("output_key", ""),
                focus_concepts=s.get("focus_concepts", ""),
            )
            for s in (dict(sr) for sr in c.fetchall())
        ]
        return ChainOut(
            chain_id=chain_id,
            name=r.get("name", ""),
            description=r.get("description", ""),
            triggers=json.loads(r.get("triggers", "[]")),
            final_prompt_template=r.get("final_prompt_template", ""),
            focus_concepts=r.get("focus_concepts", ""),
            enabled=bool(r.get("enabled", 1)),
            created_at=r.get("created_at", ""),
            updated_at=r.get("updated_at", ""),
            steps=steps,
        )
    finally:
        conn.close()


@router.post("", summary="创建链条")
def create_chain(chain: ChainIn):
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM chains WHERE chain_id=?", (chain.chain_id,))
        if c.fetchone():
            raise HTTPException(409, f"链条已存在: {chain.chain_id}")

        c.execute(
            "INSERT INTO chains (chain_id, name, description, triggers, final_prompt_template, focus_concepts, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chain.chain_id,
                chain.name,
                chain.description,
                json.dumps(chain.triggers, ensure_ascii=False),
                chain.final_prompt_template,
                chain.focus_concepts,
                int(chain.enabled),
            ),
        )
        _upsert_steps(c, chain.chain_id, chain.steps)
        conn.commit()
        reload_chains()
        return {"ok": True, "chain_id": chain.chain_id}
    finally:
        conn.close()


@router.put("/{chain_id}", summary="更新链条")
def update_chain(chain_id: str, chain: ChainIn):
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM chains WHERE chain_id=?", (chain_id,))
        if not c.fetchone():
            raise HTTPException(404, f"链条不存在: {chain_id}")

        c.execute(
            "UPDATE chains SET name=?, description=?, triggers=?, "
            "final_prompt_template=?, focus_concepts=?, enabled=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE chain_id=?",
            (
                chain.name,
                chain.description,
                json.dumps(chain.triggers, ensure_ascii=False),
                chain.final_prompt_template,
                chain.focus_concepts,
                int(chain.enabled),
                chain_id,
            ),
        )
        c.execute("DELETE FROM chain_steps WHERE chain_id=?", (chain_id,))
        _upsert_steps(c, chain_id, chain.steps)
        conn.commit()
        reload_chains()
        return {"ok": True, "chain_id": chain_id}
    finally:
        conn.close()


@router.delete("/{chain_id}", summary="删除链条")
def delete_chain(chain_id: str):
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM chains WHERE chain_id=?", (chain_id,))
        if not c.fetchone():
            raise HTTPException(404, f"链条不存在: {chain_id}")
        c.execute("DELETE FROM chain_steps WHERE chain_id=?", (chain_id,))
        c.execute("DELETE FROM chains WHERE chain_id=?", (chain_id,))
        conn.commit()
        reload_chains()
        return {"ok": True, "chain_id": chain_id}
    finally:
        conn.close()


@router.post("/reload", summary="重新加载链条缓存")
def reload():
    reload_chains()
    return {"ok": True, "message": "链引擎缓存已刷新"}


@router.get("/compile/config", summary="获取编译器领域配置")
def get_compile_config():
    """从 DB 读取当前 namespace 的业务域配置。"""
    try:
        ns = _get_active_namespace()
        config = _load_config(ns, "domains")
        return {"ok": True, "config": config}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.put("/compile/config", summary="更新编译器领域配置")
def update_compile_config(data: dict):
    """写入当前 namespace 的业务域配置到 DB。"""
    try:
        ns = _get_active_namespace()
        config = data.get("config", {})
        _save_config(ns, "domains", config)
        return {"ok": True, "message": "配置已保存"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/compile/systems", summary="获取 API 系统配置")
def get_system_config():
    """从 DB 读取当前 namespace 的系统配置。"""
    try:
        ns = _get_active_namespace()
        return {"ok": True, "config": _load_config(ns, "systems")}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.put("/compile/systems", summary="更新 API 系统配置")
def update_system_config(data: dict):
    """写入当前 namespace 的系统配置到 DB。"""
    try:
        ns = _get_active_namespace()
        _save_config(ns, "systems", data.get("config", {}))
        return {"ok": True, "message": "配置已保存"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/compile/systems/{system_name}/test", summary="测试系统连接")
async def test_system_connection(system_name: str):
    """测试 API 系统的连通性。"""
    try:
        from app.services.multi_system_backend import multi_system_backend
        result = await multi_system_backend.test_connection(system_name)
        return result
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/compile/systems/{system_name}/test-endpoint", summary="测试单个接口")
async def test_endpoint(system_name: str, data: dict):
    """测试单个 API 接口，返回原始响应供配置响应映射。"""
    try:
        from app.services.multi_system_backend import multi_system_backend
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
            result["message"] = str(e)

        return result
    except Exception as e:
        return {"ok": False, "message": str(e)}


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


_NAMESPACE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "config", "active_namespace.txt")

def _get_active_namespace() -> str:
    try:
        with open(_NAMESPACE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "manufacturing"

def _set_active_namespace(ns: str):
    os.makedirs(os.path.dirname(_NAMESPACE_FILE), exist_ok=True)
    with open(_NAMESPACE_FILE, "w", encoding="utf-8") as f:
        f.write(ns)

def _ensure_config_table():
    """确保 namespace_configs 表存在。"""
    conn = _get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS namespace_configs (
        namespace TEXT NOT NULL,
        config_type TEXT NOT NULL,
        config_data TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT,
        PRIMARY KEY (namespace, config_type)
    )""")
    conn.commit()
    conn.close()

def _load_config(namespace: str, config_type: str) -> dict:
    """从 DB 读取配置。"""
    _ensure_config_table()
    import json as _json
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT config_data FROM namespace_configs WHERE namespace=? AND config_type=?", (namespace, config_type))
        row = c.fetchone()
        if row and row["config_data"]:
            return _json.loads(row["config_data"])
    except Exception:
        pass
    finally:
        conn.close()
    return {}

def _save_config(namespace: str, config_type: str, config: dict):
    """写入配置到 DB，自动备份旧版本。"""
    _ensure_config_table()
    import json as _json, datetime as _dt
    conn = _get_conn()
    try:
        c = conn.cursor()
        # 备份旧配置
        c.execute("SELECT config_data FROM namespace_configs WHERE namespace=? AND config_type=?", (namespace, config_type))
        old = c.fetchone()
        if old and old["config_data"]:
            old_config = _json.loads(old["config_data"])
            if old_config and old_config != config:
                ts = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
                c.execute(
                    "INSERT INTO namespace_configs (namespace, config_type, config_data, updated_at) VALUES (?, ?, ?, datetime('now'))",
                    (namespace, f"{config_type}_backup_{ts}", old["config_data"])
                )
        # 写入新配置
        c.execute(
            "INSERT OR REPLACE INTO namespace_configs (namespace, config_type, config_data, updated_at) VALUES (?, ?, ?, datetime('now'))",
            (namespace, config_type, _json.dumps(config, ensure_ascii=False))
        )
        conn.commit()
    finally:
        conn.close()

def _get_domains_path(ns: str = None) -> str:
    """兼容旧调用, 实际已走 DB。"""
    return ""


@router.get("/compile/namespaces", summary="获取可用的行业命名空间")
async def list_namespaces():
    """从 Neo4j 查询所有业务数据的 namespace (排除 Schema 元数据节点)。"""
    try:
        from app.services.neo4j_service import neo4j_service
        if not neo4j_service.connected:
            await neo4j_service.connect()
        if neo4j_service.connected:
            records = await neo4j_service.execute_read(
                "MATCH (n) WHERE n._namespace IS NOT NULL AND NOT n:Concept AND NOT n:Property "
                "AND NOT n:Action AND NOT n:Rule AND NOT n:Relation AND NOT n:DataFilter "
                "AND NOT n:Mapping AND NOT n:Project AND NOT n:SchemaVersion "
                "RETURN DISTINCT n._namespace AS ns ORDER BY ns", {}
            )
            namespaces = [r["ns"] for r in records] if records else []
            return {"ok": True, "active": _get_active_namespace(), "namespaces": namespaces}
    except Exception as e:
        return {"ok": False, "message": str(e), "namespaces": ["manufacturing"]}


@router.get("/compile/config/history", summary="获取配置版本历史")
def config_history():
    """列出当前 namespace 的配置备份版本，含详情。"""
    ns = _get_active_namespace()
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
    conn = _get_conn()
    try:
        c = conn.cursor()
        # 先读当前活跃配置
        c.execute("SELECT config_data, updated_at FROM namespace_configs WHERE namespace=? AND config_type='domains'", (ns,))
        active = c.fetchone()
        active_cfg = _json.loads(active["config_data"]) if active and active["config_data"] else {}
        active_is_empty = not any(k != "mode" for k in active_cfg)

        c.execute("SELECT config_type, config_data, updated_at FROM namespace_configs WHERE namespace=? AND config_type LIKE 'domains_backup_%' ORDER BY updated_at DESC LIMIT 50", (ns,))
        rows = c.fetchall()
        total = len(rows)
        versions = []
        # 当前活跃版本
        if not active_is_empty:
            versions.append({
                "version": "current",
                "version_no": f"V{total + 1}",
                "is_active": True,
                "updated_at": active["updated_at"] if active else "",
                "domain_count": len([k for k in active_cfg if k != "mode"]),
                "concept_count": sum(len(v.get("concepts",[])) for v in active_cfg.values() if isinstance(v, dict)),
                "domains": [{"name": k, "display_name": v.get("display_name",""), "concept_count": len(v.get("concepts",[])), "icon": v.get("icon",""), "concepts": [(label_map.get(cn, cn)) for cn in v.get("concepts",[])[:15]]} for k,v in active_cfg.items() if isinstance(v, dict)],
            })
        for i, r in enumerate(rows):
            try:
                cfg = _json.loads(r["config_data"])
                domain_count = len([k for k in cfg if k != "mode"])
                concept_count = sum(len(v.get("concepts",[])) for v in cfg.values() if isinstance(v, dict))
                versions.append({
                    "version": r["config_type"].replace("domains_backup_", ""),
                    "version_no": f"V{total - i}",
                    "is_active": False,
                    "updated_at": r["updated_at"],
                    "domain_count": domain_count,
                    "concept_count": concept_count,
                    "domains": [{"name": k, "display_name": v.get("display_name",""), "concept_count": len(v.get("concepts",[])), "icon": v.get("icon",""), "concepts": [(label_map.get(cn, cn)) for cn in v.get("concepts",[])[:15]]} for k,v in cfg.items() if isinstance(v, dict)],
                })
            except Exception:
                pass
        return {"ok": True, "versions": versions}
    finally:
        conn.close()


@router.delete("/compile/config/history/{version}", summary="删除配置版本")
def delete_config_version(version: str):
    """删除指定版本的历史配置。"""
    ns = _get_active_namespace()
    conn = _get_conn()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM namespace_configs WHERE namespace=? AND config_type=?", (ns, f"domains_backup_{version}"))
        conn.commit()
        return {"ok": True, "message": f"已删除版本 {version}"}
    finally:
        conn.close()


@router.post("/compile/config/restore/{version}", summary="恢复配置版本")
def restore_config(version: str):
    """从备份恢复域配置 (不产生新备份)。"""
    ns = _get_active_namespace()
    conn = _get_conn()
    try:
        import json as _json
        c = conn.cursor()
        c.execute("SELECT config_data FROM namespace_configs WHERE namespace=? AND config_type=?", (ns, f"domains_backup_{version}"))
        row = c.fetchone()
        if row:
            config = _json.loads(row["config_data"])
            c.execute(
                "INSERT OR REPLACE INTO namespace_configs (namespace, config_type, config_data, updated_at) VALUES (?, ?, ?, datetime('now'))",
                (ns, "domains", _json.dumps(config, ensure_ascii=False))
            )
            conn.commit()
            return {"ok": True, "message": f"已恢复版本 {version}"}
        return {"ok": False, "message": "版本不存在"}
    finally:
        conn.close()


@router.post("/compile/namespace/{name}", summary="切换行业命名空间")
async def switch_namespace(name: str):
    """切换活跃命名空间 → 编译器自动从本体推导领域分组。"""
    import shutil
    _set_active_namespace(name)

    # 保存旧配置 → 加载 namespace 专属配置 (如果存在)
    config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "config")
    ns_domains = os.path.join(config_dir, f"{name}_domains.yaml")
    default_domains = os.path.join(config_dir, "compiler_domains.yaml")
    if os.path.exists(default_domains):
        shutil.move(default_domains, os.path.join(config_dir, f"_backup_domains.yaml"))
    if os.path.exists(ns_domains):
        shutil.copy(ns_domains, default_domains)

    from app.agents import compile_and_register
    from app.core.chain_engine import reload_chains
    runtime = await compile_and_register()
    reload_chains()
    if runtime:
        return {"ok": True, "message": f"已切换至 {name}: {runtime.concept_count}概念 {len(runtime.agents)}Agent"}
    return {"ok": False, "message": "编译无产出"}


@router.get("/compile/status", summary="获取编译器状态")
def compile_status():
    """返回最近一次编译的统计信息。"""
    try:
        from app.agents import get_compiled_runtime
        runtime = get_compiled_runtime()
        if runtime:
            return {
                "ok": True,
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
                     "agent": _find_agent_for_concept(runtime, s.concept),
                     "output_fields": [{"name": f.name, "label": f.label, "type": f.type} for f in s.output_fields]}
                    for s in runtime.skills[:50]
                ],
            }
        return {"ok": False, "message": "编译器尚未运行"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _find_agent_for_concept(runtime, concept: str) -> str:
    for a in runtime.agents:
        for sn in a.skill_names:
            if sn.startswith(f"{concept}_"):
                return a.display_name
    return ""


@router.post("/compile/reload", summary="重新编译本体 → 刷新 Skill + Agent + 链")
async def compile_reload():
    """触发编译器重新运行, 产出 Skill/Agent/链并同步到 DB。

    本体在 OntoStudio 中更新并 push 到 Neo4j 后调用此端点。
    """
    try:
        from app.agents import compile_and_register
        from app.core.chain_engine import reload_chains as reload_chain_engine

        runtime = await compile_and_register()
        if runtime:
            reload_chain_engine()
            return {
                "ok": True,
                "message": f"编译完成: {runtime.concept_count}概念, "
                           f"{len(runtime.skills)}Skill, "
                           f"{len(runtime.agents)}Agent, "
                           f"{len(runtime.chains)}链",
                "skills": len(runtime.skills),
                "agents": len(runtime.agents),
                "chains": len(runtime.chains),
            }
        else:
            return {"ok": False, "message": "编译无产出 (Neo4j 是否已连接?)"}
    except Exception as e:
        from app.core.logger import log
        log.error(f"[API] 编译失败: {e}")
        return {"ok": False, "message": f"编译失败: {e}"}


@router.get("/agents/list", summary="获取可用 Agent 列表（供链条配置引用）")
def list_agents():
    reload_agents()
    return [
        {
            "name": name,
            "display_name": info.get("display_name", name),
            "description": info.get("description", ""),
            "icon": info.get("icon", ""),
        }
        for name, info in AGENT_DEFINITIONS.items()
    ]


# ── 辅助函数 ──────────────────────────────────────────────────────

def _upsert_steps(c, chain_id: str, steps: list[ChainStepIn]):
    for s in steps:
        c.execute(
            "INSERT INTO chain_steps (chain_id, step_order, step_id, description, agent_name, prompt_template, output_key, focus_concepts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chain_id, s.step_order, s.step_id, s.description, s.agent_name, s.prompt_template, s.output_key, s.focus_concepts),
        )
