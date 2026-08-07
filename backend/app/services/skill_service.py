"""动态 Skill 服务 — 声明式工具注册表与执行器

- 只读：cypher_template / aggregate → Neo4j 只读查询（参数化 + validate_readonly）
- 写：map_to_action → 映射到已建模 action，走 action_executor 统一治理（RBAC + rule_engine + 审批）
"""
import json
from typing import Any, Dict, Optional

from loguru import logger


class SkillService:
    """动态 skill 注册表 + 声明式执行器"""

    def __init__(self):
        self._skills: Dict[str, dict] = {}

    def all(self) -> list[dict]:
        return list(self._skills.values())

    def get(self, name: str) -> Optional[dict]:
        return self._skills.get(name)

    def has(self, name: str) -> bool:
        return name in self._skills

    async def reload(self):
        """从 DB 加载启用 skill 到内存（配置变更后调用）"""
        from app.db import get_db
        from app.repositories.skill_repo import SkillRepository

        loaded = {}
        async for session in get_db():
            repo = SkillRepository(session)
            for s in await repo.list_enabled():
                loaded[s.name] = {
                    "name": s.name,
                    "display_name": s.display_name,
                    "description": s.description,
                    "type": s.type,
                    "concept": s.concept,
                    "param_schema": json.loads(s.param_schema or "[]"),
                    "implementation": json.loads(s.implementation or "{}"),
                    "risk": s.risk,
                }
            break
        self._skills = loaded
        logger.info(f"[Skill] 加载 {len(loaded)} 个动态 skill")

    async def execute(self, name: str, params: dict) -> dict:
        """执行 skill，返回 {ok, result, rowCount, source, needs_approval, approval_id}"""
        skill = self._skills.get(name)
        if not skill:
            return {"ok": False, "result": f"skill [{name}] 不存在或未启用", "source": "skill"}
        # 参数校验
        errors = self._validate_params(skill, params)
        if errors:
            return {"ok": False, "result": "参数错误: " + "; ".join(errors), "source": "skill"}
        impl = skill.get("implementation") or {}
        kind = impl.get("kind", "")
        if kind == "map_to_action":
            return await self._exec_action(impl, params)
        if kind in ("cypher_template", "aggregate"):
            template = impl.get("template", "")
            if not template:
                return {"ok": False, "result": f"skill [{name}] 未配置查询模板", "source": "skill"}
            return await self._exec_cypher(template, params)
        return {"ok": False, "result": f"skill [{name}] 不支持的实现类型: {kind}", "source": "skill"}

    # ── 内部 ──

    def _validate_params(self, skill: dict, params: dict) -> list[str]:
        errors = []
        for p in skill.get("param_schema", []) or []:
            pname = p.get("name", "")
            if p.get("required") and not params.get(pname):
                errors.append(f"缺少必填参数 {p.get('label') or pname}")
        return errors

    async def _exec_action(self, impl: dict, params: dict) -> dict:
        """写类：映射到已建模 action，走 action_executor 统一治理"""
        action_name = impl.get("action_name", "")
        if not action_name:
            return {"ok": False, "result": "map_to_action 未配置 action_name", "source": "skill"}
        from app.services.action_executor import action_executor
        r = await action_executor.execute_structured_async(action_name, dict(params or {}))
        result_str = str(r.get("result", ""))
        return {
            "ok": not r.get("needs_approval") and "权限不足" not in result_str,
            "result": result_str,
            "rowCount": r.get("rowCount", 0),
            "source": "action",
            "needs_approval": bool(r.get("needs_approval")),
            "approval_id": r.get("approval_id", ""),
        }

    async def _exec_cypher(self, template: str, params: dict) -> dict:
        """只读 Cypher：validate_readonly + execute_read（参数化，拒绝写语句）"""
        from app.services.neo4j_service import Neo4jService, neo4j_service

        ok, err = Neo4jService.validate_readonly(template)
        if not ok:
            return {"ok": False, "result": f"模板校验失败: {err}", "source": "skill"}
        try:
            records = await neo4j_service.execute_read(template, dict(params or {}))
            return {"ok": True, "result": records, "rowCount": len(records), "source": "skill"}
        except Exception as e:
            return {"ok": False, "result": f"查询失败: {e}", "source": "skill"}


skill_service = SkillService()
