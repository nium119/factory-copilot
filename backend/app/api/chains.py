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
                    }
                    for a in runtime.agents
                ],
                "skills": [
                    {"name": s.name, "display_name": s.display_name, "concept": s.concept,
                     "data_source_type": s.data_source.type if s.data_source else "neo4j",
                     "agent": _find_agent_for_concept(runtime, s.concept)}
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
