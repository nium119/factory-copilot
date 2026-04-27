"""通用助手 Agent — 迁移自原 agent_service"""
from typing import Optional, Dict, Any, AsyncGenerator, List
import re
import json

from app.agents.base import BaseAgent
from app.agents.agent_config import AGENT_DEFINITIONS
from app.agents.settings import ENTERPRISE_QUERY_PATTERNS, COLLAB_DISPLAY_LIMITS
from app.core.logger import log
from app.core.prompts import DEFAULT_SYSTEM_PROMPT
from app.services.llm_service import llm_service
from app.tools.enterprise_tool import enterprise_tool
from app.agents import collaborator


class GeneralAgent(BaseAgent):
    """通用 AI 助手"""

    _meta = AGENT_DEFINITIONS["general"]
    name = "general"
    display_name = _meta["display_name"]
    icon = _meta["icon"]
    color = _meta["color"]
    description = _meta["description"]
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
        system_prompt = context.get("system_prompt", self.system_prompt) if context else self.system_prompt

        if use_agent:
            log.info("进入 _collaborate 流程")
            collab_agents_data = []
            async for chunk_type, chunk_content in self._collaborate(
                message, session_id, model_name, web_search, system_prompt, history_messages, enable_thinking, matched_agents,
            ):
                if chunk_type == "collab_agent":
                    try:
                        agent_data = json.loads(chunk_content) if isinstance(chunk_content, str) else chunk_content
                        collab_agents_data.append(agent_data)
                    except Exception:
                        pass
                yield chunk_type, chunk_content
            if collab_agents_data:
                yield "metadata", json.dumps({"collab_agents": collab_agents_data}, ensure_ascii=False)
            return

        # 普通模式
        tool_result = await self._check_and_call_tools(message)
        enhanced_message = message
        if tool_result:
            enhanced_message = f"{message}\n\n工具调用结果:\n{tool_result}"

        async for chunk_type, chunk_content in llm_service.chat_stream(
            message=enhanced_message,
            session_id=session_id,
            system_prompt=system_prompt,
            model_name=model_name,
            use_agent=use_agent,
            web_search=web_search,
            history_messages=history_messages,
            enable_thinking=enable_thinking,
        ):
            yield chunk_type, chunk_content
        log.info(f"GeneralAgent process 完成")

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
        """执行多 Agent 协作流程"""
        import time
        t0 = time.time()
        log.info(f"GeneralAgent 触发协作模式: {message}")

        from app.agents import get_agent
        from app.agents.settings import COLLAB_DOMAIN_QUERIES
        import asyncio

        if matched_agents:
            collab_list = [
                (name, COLLAB_DOMAIN_QUERIES.get(name, f"查询{name}相关情况"))
                for name in matched_agents
            ]
            log.info(f"[协作] 动态选择 Agent: {matched_agents}")
        else:
            from app.agents.collaborator import get_collab_agents
            collab_list = get_collab_agents()
            log.info(f"[协作] 使用全部 Agent")

        total_count = len(collab_list)

        log.info(f"[协作] 发送 collab_start 事件 (t+{time.time()-t0:.1f}s)")
        yield "collab_start", json.dumps({"total": total_count})

        async def call_one_agent(agent_name, domain_query):
            start = time.time()
            try:
                agent = get_agent(agent_name)
                display = agent.display_name
                log.info(f"[协作] 开始调用 Agent {agent_name}({display}) (t+{start-t0:.1f}s)")
                raw = await agent.call_tools(domain_query)
                result = raw[0] if isinstance(raw, tuple) else raw
                elapsed = time.time() - start
                log.info(f"[协作] Agent {agent_name}({display}) 返回: {'有数据' if result else '无数据'} (耗时: {elapsed:.2f}s)")
                return agent_name, display, result
            except Exception as e:
                elapsed = time.time() - start
                log.warning(f"调用 Agent {agent_name} 工具失败: {e} (耗时: {elapsed:.2f}s)")
                return agent_name, agent_name, None

        tasks = [call_one_agent(name, query) for name, query in collab_list]

        all_results = {}
        success_count = 0
        max_preview = COLLAB_DISPLAY_LIMITS["max_result_preview"]
        for coro in asyncio.as_completed(tasks):
            agent_name, display_name, result = await coro
            all_results[agent_name] = result
            t_now = time.time() - t0
            if result:
                success_count += 1
                log.info(f"[协作] 发送 collab_agent({display_name}, success) (t+{t_now:.2f}s)")
                yield "collab_agent", json.dumps({
                    "name": agent_name,
                    "display_name": display_name,
                    "status": "success",
                    "data": result[:max_preview] if len(result) > max_preview else result,
                }, ensure_ascii=False)
            else:
                log.info(f"[协作] 发送 collab_agent({display_name}, empty) (t+{t_now:.2f}s)")
                yield "collab_agent", json.dumps({
                    "name": agent_name,
                    "display_name": display_name,
                    "status": "empty",
                    "data": None,
                }, ensure_ascii=False)

        log.info(f"[协作] 发送 collab_done (t+{time.time()-t0:.2f}s)")
        yield "collab_done", json.dumps({"success": success_count, "total": total_count})

        data_context = self._build_collab_data_context(all_results, success_count, total_count)
        collab_prompt = f"{system_prompt}\n\n## 协作数据\n{data_context}\n\n请基于以上各模块的数据，以自然、简洁的方式生成一份综合分析报告，回答用户的问题：「{message}」。"

        log.info(f"[协作] 开始调用 LLM 生成综合报告 (t+{time.time()-t0:.2f}s)")
        async for chunk_type, chunk_content in llm_service.chat_stream(
            message=collab_prompt,
            session_id=session_id,
            system_prompt=system_prompt,
            model_name=model_name,
            use_agent=False,
            web_search=False,
            history_messages=history_messages,
            enable_thinking=enable_thinking,
        ):
            yield chunk_type, chunk_content

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

    async def _check_and_call_tools(self, content: str) -> Optional[str]:
        """检查用户意图，调用对应工具（仅企业信息查询）"""
        for pattern in ENTERPRISE_QUERY_PATTERNS:
            match = re.search(pattern, content)
            if match:
                try:
                    company_name = match.group(1).strip()
                except IndexError:
                    company_name = content.strip()
                if not company_name:
                    company_name = content
                log.info(f"检测到企业信息查询意图: {company_name}")
                result = await enterprise_tool.query(company_name)
                return enterprise_tool.format_result(result)
        return None


general_agent = GeneralAgent()
