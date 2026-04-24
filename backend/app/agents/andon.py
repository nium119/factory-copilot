"""安灯(Andon)助手 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List

from app.agents.base import BaseAgent
from app.core.logger import log
from app.core.prompts import ANDON_SYSTEM_PROMPT
from app.agents.tools.andon_tools import (
    create_andon_alert, query_active_andons, query_andon_history,
    escalate_andon, get_andon_stats, handle_line_stop,
    format_andon_report, format_stats_report,
)
from app.services.llm_service import llm_service


class AndonAgent(BaseAgent):
    """安灯异常响应助手 - 异常呼叫、停线处理、问题上报、响应跟踪"""

    name = "andon"
    display_name = "安灯助手"
    icon = "🚨"
    color = "#eb3b5a"
    description = "安灯异常响应助手，支持异常呼叫、停线处理、问题上报、响应跟踪、异常分类（物料/设备/质量/工艺）、工单异常与应急响应管理"
    keywords = ["安灯", "异常", "停线", "报警", "呼叫", "问题上报", "应急响应", "故障报警", "产线异常", "andon"]
    system_prompt = ANDON_SYSTEM_PROMPT

    async def process(
        self,
        message: str,
        session_id: str = "default",
        model_name: Optional[str] = None,
        use_agent: bool = False,
        web_search: bool = False,
        enable_thinking: bool = False,
        context: Optional[Dict[str, Any]] = None,
        history_messages: Optional[List] = None,
    ) -> AsyncGenerator[tuple, None]:
        tool_result = await self.call_tools(message)
        enhanced = f"{message}\n\n参考数据:\n{tool_result}" if tool_result else message
        async for t, c in llm_service.chat_stream(
            message=enhanced, session_id=session_id,
            system_prompt=context.get("system_prompt", self.system_prompt) if context else self.system_prompt,
            model_name=model_name,
            use_agent=use_agent, web_search=web_search,
            history_messages=history_messages,
            enable_thinking=enable_thinking,
        ):
            yield t, c

    async def call_tools(self, message: str) -> Optional[str]:
        # 创建安灯报警
        if any(k in message for k in ["创建", "报警", "报警", "呼叫", "上报"]):
            alert_type = _extract_alert_type(message)
            line = _extract_line(message)
            desc = _extract_description(message)
            if desc:
                data = await create_andon_alert(alert_type, desc, line)
                return f"安灯报警已创建\n- ID: {data['andon_id']}\n- 类型: {data['type']}\n- 产线: {data['line']}\n- 描述: {data['description']}\n- 状态: {data['status']}\n- 级别: {data['level']}"
            return "请描述异常情况，例如：'创建安灯报警，SMT-01产线设备故障'"

        # 停线处理
        if "停线" in message:
            line = _extract_line(message)
            reason = _extract_description(message)
            if line:
                data = await handle_line_stop(line, reason or "未指定")
                return f"停线记录已创建\n- 产线: {data['line']}\n- 原因: {data['reason']}\n- 时间: {data['start_time']}\n- 状态: {data['status']}"
            return "请指定产线，例如：'SMT-01 停线，设备故障'"

        # 查询活跃安灯
        if any(k in message for k in ["活跃", "当前", "待处理", "处理中"]):
            line = _extract_line(message)
            data = await query_active_andons(line)
            return format_andon_report(data)

        # 安灯历史
        if "历史" in message:
            data = await query_andon_history()
            return format_andon_report(data)

        # 安灯升级
        if "升级" in message:
            andon_id = _extract_andon_id(message)
            level = "manager" if "经理" in message else ("director" if "总监" in message else "线长")
            if andon_id:
                data = await escalate_andon(andon_id, level)
                return f"安灯升级: {data.get('andon_id')} -> {data.get('new_level')} ({data.get('status')})" if "error" not in data else data["error"]
            return "请提供安灯ID，例如：'升级 AN-2026-043 到生产经理'"

        # 安灯统计
        if any(k in message for k in ["统计", "概况", "分析", "报表"]):
            data = await get_andon_stats()
            return format_stats_report(data)

        return None


def _extract_alert_type(message: str) -> str:
    for t in ["物料", "设备", "质量", "工艺"]:
        if t in message:
            return t
    return "设备"  # default


def _extract_line(message: str) -> Optional[str]:
    import re
    match = re.search(r'(SMT-\d+|DIP-\d+|组装-\d+)', message)
    return match.group(0) if match else None


def _extract_andon_id(message: str) -> Optional[str]:
    import re
    match = re.search(r'AN[-\s]?\d{4}[-\s]?\d+', message, re.IGNORECASE)
    return match.group(0).replace(" ", "") if match else None


def _extract_description(message: str) -> str:
    # 简单提取：取关键词后的内容
    for kw in ["报警", "呼叫", "停线", "上报", "异常", "故障"]:
        idx = message.find(kw)
        if idx >= 0:
            rest = message[idx + len(kw):].strip(" ，,：:")
            if rest:
                return rest
    return message


andon_agent = AndonAgent()
