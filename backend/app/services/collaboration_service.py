"""多 Agent 协作服务 — 主 agent 拆解子任务 → 并行派发各领域 agent → 聚合结果

复用 ParallelExecutor（已实现未接线的基础设施）+ get_agent 解析器。
子 agent 只查询；写操作由主流程走统一治理（本服务仅只读协作）。
"""
import json

from loguru import logger

from app.core.parallel_executor import ParallelExecutor, ParallelTask


class CollaborationService:
    """多 agent 并行协作：拆解 → 派发 → 聚合"""

    async def plan(self, message: str) -> dict:
        """LLM 拆解：根据消息 + 可用 agent 领域，输出 {agent_name: 子任务查询}"""
        try:
            from app.agents import get_agents_from_db
            from app.services.llm_service import llm_service

            agents = get_agents_from_db()
            available = [a.name for a in agents if getattr(a, "enabled", True)]
            if not available:
                return {}
            prompt = (
                f"把以下任务拆解为若干子任务，交给对应领域的 agent 并行执行。\n"
                f"可选 agent: {available}\n"
                f"规则：只拆解为各 agent 能独立查询的子任务（只读），不要含写操作；"
                f"若任务不涉及多领域协作，输出空数组。\n"
                f"任务: {message}\n"
                f"只输出 JSON: {{\"subtasks\": [{{\"agent\": \"agent名\", \"query\": \"给该 agent 的查询指令\"}}]}}"
            )
            raw = await llm_service.chat_sync(
                message=prompt,
                system_prompt="你是多 agent 协作拆解器，只输出 JSON。",
                model_name="decision_model",
            )
            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
            parsed = json.loads(raw)
            assignments = {}
            for st in parsed.get("subtasks", []) or []:
                agent = str(st.get("agent", "")).strip()
                query = str(st.get("query", "")).strip()
                if agent and query:
                    assignments[agent] = query
            return assignments
        except Exception as e:
            logger.warning(f"[Collaboration] 拆解失败: {e}")
            return {}

    async def collaborate(self, message: str, assignments: dict = None, timeout: float = 15.0) -> dict:
        """拆解（未提供时 LLM 拆解）+ 并行执行 + 聚合结果"""
        if not assignments:
            assignments = await self.plan(message)
        if not assignments:
            return {"ok": False, "reason": "未拆解出可协作的子任务（可能不涉及多领域）", "results": {}, "assignments": {}}

        from app.agents import get_agent

        tasks = [
            ParallelTask(
                task_id=f"sub_{i}", agent_name=name, display_name=name,
                query=query, timeout=timeout,
            )
            for i, (name, query) in enumerate(assignments.items())
        ]
        results = {}
        executor = ParallelExecutor()
        async for evt_type, content in executor.execute_with_events(tasks, agent_resolver=get_agent):
            if evt_type == "parallel_task":
                try:
                    d = json.loads(content)
                    results[d.get("agent_name", "")] = d
                except Exception:
                    pass
        success = [r for r in results.values() if r.get("status") == "success"]
        return {
            "ok": len(success) > 0,
            "success_count": len(success),
            "total": len(tasks),
            "results": results,
            "assignments": assignments,
        }


collaboration_service = CollaborationService()
