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
