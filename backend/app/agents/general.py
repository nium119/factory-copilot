"""通用助手 Agent — 迁移自原 agent_service"""
import asyncio
import json as _json
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.agents.base import BaseAgent
from app.agents.settings import COLLAB_DOMAIN_QUERIES, ENTERPRISE_QUERY_PATTERNS
from app.core.logger import log
from app.core.prompts import DEFAULT_SYSTEM_PROMPT
from app.core.resource_monitor import resource_monitor
from app.services.llm_service import llm_service
from app.tools.enterprise_tool import enterprise_tool


class GeneralAgent(BaseAgent):
    """通用 AI 助手"""

    name = "general"
    system_prompt = DEFAULT_SYSTEM_PROMPT

    async def process(
        self,
        message: str,
        session_id: str = "default",
        model_name: Optional[str] = None,
        use_agent: bool = False,
        web_search: bool = False,
        enable_thinking: Optional[bool] = None,
        context: Optional[Dict[str, Any]] = None,
        history_messages: Optional[List] = None,
        matched_agents: Optional[List[str]] = None,
    ) -> AsyncGenerator[tuple, None]:
        if use_agent:
            log.info("进入 _collaborate 流程")
            system_prompt = context.get("system_prompt", self.system_prompt) if context else self.system_prompt
            async for chunk_type, chunk_content in self._collaborate(
                message, session_id, model_name, web_search, system_prompt, history_messages, enable_thinking, matched_agents,
            ):
                yield chunk_type, chunk_content
            return

        async for evt in self._standard_process(
            message, session_id, model_name, use_agent, web_search,
            enable_thinking, context, history_messages, matched_agents,
        ):
            yield evt

    async def _resolve_collab_agents(self, message: str, matched_agents: Optional[List[str]] = None):
        """解析参与协作的 Agent 列表并按优先级排序，返回 (agent_names, priority_map)。

        不返回模板查询 — 每个 Agent 通过本体链路 (_call_tools_via_ontology)
        处理用户原始消息，路由和参数提取全部从 ontology 自动生成。
        """
        from app.agents import _AGENT_REGISTRY
        from app.agents.prioritization import prioritize_agents

        if matched_agents:
            agent_names = list(matched_agents)
            log.info(f"[协作] 动态选择 Agent: {agent_names}")
        else:
            core_agents = set(COLLAB_DOMAIN_QUERIES.keys())
            agent_names = [n for n in _AGENT_REGISTRY if n in core_agents]
            log.info("[协作] 使用全部 Agent")

        agent_priorities = prioritize_agents(message, agent_names)
        priority_map = {name: (priority, score) for name, priority, score, _ in agent_priorities}
        priority_order = {name: i for i, (name, _, _, _) in enumerate(agent_priorities)}
        agent_names.sort(key=lambda n: priority_order.get(n, 99))
        log.info(f"[协作] 优先级排序: {[(n, priority_map[n][0]) for n in agent_names]}")
        return agent_names, priority_map

    async def _collaborate(
        self,
        message: str,
        session_id: str,
        model_name: Optional[str],
        web_search: bool,
        system_prompt: str,
        history_messages: Optional[List],
        enable_thinking: Optional[bool],
        matched_agents: Optional[List[str]] = None,
    ) -> AsyncGenerator[tuple, None]:
        """多 Agent 协作 — 每个子 Agent 走本体链路 (_call_tools_via_ontology)。

        路由关键词、参数提取器、代码模式全部从 ontology Action/Concept 定义自动生成，
        零硬编码。
        """
        import time
        t0 = time.time()
        log.info(f"GeneralAgent 触发协作模式 (本体链路): {message}")

        from app.agents import get_agent
        from app.agents.settings import COLLAB_TIMEOUT

        agent_names, priority_map = await self._resolve_collab_agents(message, matched_agents)
        total_count = len(agent_names)
        batch_id = f"collab_{int(t0*1000)}"
        per_task_timeout = getattr(COLLAB_TIMEOUT, 'per_task', 10.0) if hasattr(COLLAB_TIMEOUT, 'per_task') else 10.0

        # Emit parallel_start
        tasks_summary = [
            {"task_id": f"task_{n}", "agent_name": n, "display_name": get_agent(n).display_name}
            for n in agent_names
        ]
        yield ("parallel_start", _json.dumps({"batch_id": batch_id, "total": total_count, "tasks": tasks_summary}, ensure_ascii=False))

        # ── 每个 Agent 走本体链路，asyncio.gather 并发 ──
        async def run_agent_ontology(agent_name: str) -> Dict[str, Any]:
            agent = get_agent(agent_name)
            t_start = time.time()
            try:
                data = await agent._call_tools_via_ontology(message)
                elapsed = time.time() - t_start
                status = "success" if data else "empty"
                log.info(f"[协作] {agent_name} 本体路由 → {status} ({elapsed:.2f}s)")
                return {"agent_name": agent_name, "display_name": agent.display_name,
                        "status": status, "data": data, "elapsed": round(elapsed, 3)}
            except Exception as e:
                elapsed = time.time() - t_start
                log.warning(f"[协作] {agent_name} 本体路由异常: {e}")
                return {"agent_name": agent_name, "display_name": agent.display_name,
                        "status": "error", "data": None, "elapsed": round(elapsed, 3), "error": str(e)}

        coros = [asyncio.wait_for(run_agent_ontology(n), timeout=per_task_timeout) for n in agent_names]
        gathered = await asyncio.gather(*coros, return_exceptions=True)

        all_results: Dict[str, Any] = {}
        success_count = 0
        collab_agents_info: List[Dict[str, Any]] = []

        for i, result in enumerate(gathered):
            agent_name = agent_names[i]
            agent = get_agent(agent_name)
            if isinstance(result, Exception):
                task_info = {
                    "agent_name": agent_name, "display_name": agent.display_name,
                    "status": "timeout" if isinstance(result, asyncio.TimeoutError) else "error",
                    "data": None, "elapsed": per_task_timeout,
                    "error": str(result),
                }
            else:
                task_info = result

            all_results[task_info["agent_name"]] = task_info["data"]
            if task_info["status"] == "success":
                success_count += 1
            elif task_info["status"] == "timeout":
                all_results[agent_name] = f"[{task_info['display_name']} 查询超时]"
            elif task_info["status"] == "error":
                all_results[agent_name] = f"[{task_info['display_name']} 查询失败: {task_info.get('error', '')}]"

            collab_agents_info.append({
                "name": task_info["agent_name"],
                "display_name": task_info["display_name"],
                "status": task_info["status"],
                "data": task_info["data"],
                "elapsed": task_info.get("elapsed", 0),
                "priority": priority_map.get(agent_name, ("low", 30))[0],
            })

            yield ("parallel_task", _json.dumps({
                "batch_id": batch_id,
                "agent_name": task_info["agent_name"],
                "display_name": task_info["display_name"],
                "status": task_info["status"],
                "data": task_info["data"][:800] if task_info["data"] else None,
                "elapsed": task_info.get("elapsed", 0),
                "completed": i + 1, "total": total_count,
            }, ensure_ascii=False))

        yield ("parallel_done", _json.dumps({"batch_id": batch_id, "success": success_count, "total": total_count}, ensure_ascii=False))

        # ── LLM 综合报告 ──
        data_context = self._build_collab_data_context(all_results, success_count, total_count)
        collab_prompt = f"{system_prompt}\n\n## 协作数据\n{data_context}\n\n请基于以上各模块的数据，以自然、简洁的方式生成一份综合分析报告，回答用户的问题：「{message}」。"

        effective_model = model_name
        if resource_monitor.enabled:
            tier = resource_monitor.current_tier
            if tier.value in ("constrained", "critical"):
                effective_model = resource_monitor.get_recommended_model(model_name)
                log.info(f"[协作] 资源 {tier.value}, 降级模型: {model_name} → {effective_model}")

        log.info(f"[协作] 开始调用 LLM 生成综合报告 (t+{time.time()-t0:.2f}s)")
        async for chunk_type, chunk_content in llm_service.chat_stream(
            message=collab_prompt, session_id=session_id,
            system_prompt=system_prompt, model_name=effective_model,
            use_agent=False, web_search=False,
            history_messages=history_messages, enable_thinking=enable_thinking,
        ):
            yield chunk_type, chunk_content

        yield "metadata", _json.dumps({"collab_agents": collab_agents_info}, ensure_ascii=False)
        log.info(f"GeneralAgent 协作完成 (总耗时: {time.time()-t0:.2f}s)")

    def _build_collab_data_context(self, all_results: dict, success_count: int, total_count: int) -> str:
        """将协作结果组装为 LLM 可读的数据上下文"""
        from app.agents.agent_config import get_agent_metadata
        lines = [f"查询状态: {success_count}/{total_count} 个模块返回数据\n"]
        for agent_name, result in all_results.items():
            info = get_agent_metadata(agent_name)
            display_name = info["display_name"]
            if result:
                lines.append(f"### {display_name}({agent_name})")
                lines.append(result)
            else:
                lines.append(f"### {display_name}({agent_name}): 无匹配数据")
            lines.append("")
        return "\n".join(lines)

    async def call_tools(self, message: str) -> Optional[str]:
        """覆盖 BaseAgent.call_tools — 企业信息查询"""
        for pattern in ENTERPRISE_QUERY_PATTERNS:
            match = re.search(pattern, message)
            if match:
                try:
                    company_name = match.group(1).strip()
                except IndexError:
                    company_name = message.strip()
                if not company_name:
                    company_name = message
                log.info(f"检测到企业信息查询意图: {company_name}")
                result = await enterprise_tool.query(company_name)
                return enterprise_tool.format_result(result)
        return None


general_agent = GeneralAgent()
