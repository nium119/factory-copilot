"""通用助手 Agent — 迁移自原 agent_service"""
import json as _json
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.agents.base import BaseAgent
from app.agents.settings import COLLAB_DOMAIN_QUERIES, ENTERPRISE_QUERY_PATTERNS
from app.core.logger import log
from app.core.parallel_executor import ParallelTask, parallel_executor
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
        """解析参与协作的 Agent 列表并按优先级排序，返回 (collab_list, priority_map)"""
        from app.agents import _AGENT_REGISTRY
        from app.agents.prioritization import prioritize_agents

        if matched_agents:
            collab_list = [
                (name, COLLAB_DOMAIN_QUERIES.get(name, f"查询{name}相关情况"))
                for name in matched_agents
            ]
            log.info(f"[协作] 动态选择 Agent: {matched_agents}")
        else:
            core_agents = set(COLLAB_DOMAIN_QUERIES.keys())
            collab_list = [
                (name, COLLAB_DOMAIN_QUERIES[name])
                for name in _AGENT_REGISTRY
                if name in core_agents
            ]
            log.info("[协作] 使用全部 Agent")

        agent_priorities = prioritize_agents(message, [name for name, _ in collab_list])
        priority_map = {name: (priority, score) for name, priority, score, _ in agent_priorities}
        priority_order = {name: i for i, (name, _, _, _) in enumerate(agent_priorities)}
        collab_list.sort(key=lambda x: priority_order.get(x[0], 99))
        log.info(f"[协作] 优先级排序: {[(n, priority_map[n][0]) for n, _ in collab_list]}")
        return collab_list, priority_map

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
        """执行多 Agent 协作流程 — 使用 ParallelExecutor 支持超时和降级"""
        import time
        t0 = time.time()
        log.info(f"GeneralAgent 触发协作模式: {message}")

        from app.agents import get_agent
        from app.agents.settings import COLLAB_TIMEOUT

        collab_list, priority_map = await self._resolve_collab_agents(message, matched_agents)

        total_count = len(collab_list)
        batch_id = f"collab_{int(t0*1000)}"

        # 构建 ParallelTask 列表（含优先级元数据）
        per_task_timeout = getattr(COLLAB_TIMEOUT, 'per_task', 10.0) if hasattr(COLLAB_TIMEOUT, 'per_task') else 10.0
        tasks = [
            ParallelTask(
                task_id=f"task_{name}",
                agent_name=name,
                display_name=get_agent(name).display_name,
                query=query,
                timeout=per_task_timeout,
            )
            for name, query in collab_list
        ]

        # 使用 ParallelExecutor 并发执行（带超时保护）
        parallel_executor.default_timeout = per_task_timeout
        parallel_executor.degrade_on_timeout = True
        parallel_executor.set_agent_resolver(get_agent)

        all_results = {}
        success_count = 0
        collab_agents_info: List[Dict[str, Any]] = []  # 收集各 Agent 信息用于持久化

        async for event_type, event_data in parallel_executor.execute_with_events(
            tasks=tasks,
            agent_resolver=get_agent,
            batch_id=batch_id,
        ):
            if event_type == "parallel_start":
                yield event_type, event_data
            elif event_type == "parallel_task":
                task_info = _json.loads(event_data) if isinstance(event_data, str) else event_data
                agent_name = task_info["agent_name"]
                display_name = task_info["display_name"]
                status = task_info["status"]
                result_data = task_info.get("data")
                all_results[agent_name] = result_data

                if status == "success":
                    success_count += 1
                elif status == "timeout":
                    all_results[agent_name] = f"[{display_name} 查询超时]"
                elif status == "error":
                    all_results[agent_name] = f"[{display_name} 查询失败: {task_info.get('error', '')}]"
                else:
                    all_results[agent_name] = None

                collab_agents_info.append({
                    "name": agent_name,
                    "display_name": display_name,
                    "status": status,
                    "data": result_data,
                    "elapsed": task_info.get("elapsed", 0),
                    "priority": priority_map.get(agent_name, ("low", 30))[0],
                })

                # 附加优先级到事件数据
                enriched_data = _json.loads(event_data) if isinstance(event_data, str) else event_data
                enriched_data["priority"] = priority_map.get(agent_name, ("low", 30))[0]
                yield event_type, _json.dumps(enriched_data, ensure_ascii=False)

            elif event_type == "parallel_done":
                yield event_type, event_data  # 透传 parallel_done

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
            message=collab_prompt,
            session_id=session_id,
            system_prompt=system_prompt,
            model_name=effective_model,
            use_agent=False,
            web_search=False,
            history_messages=history_messages,
            enable_thinking=enable_thinking,
        ):
            yield chunk_type, chunk_content

        # 产出协作用户信息（用于持久化 metadata.collab_agents）
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
