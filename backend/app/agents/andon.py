"""安灯(Andon)助手 Agent"""
import re
from typing import Optional

from app.agents.base import BaseAgent
from app.agents.settings import ANDON_TYPE_MAP, DEFAULT_ANDON_TYPE, DEFAULT_ESCALATION_LEVEL, ESCALATION_LEVEL_MAP
from app.agents.tools.andon_tools import (
    create_andon_alert,
    escalate_andon,
    format_andon_report,
    format_stats_report,
    get_andon_stats,
    handle_line_stop,
    query_active_andons,
    query_andon_history,
)
from app.core.prompts import ANDON_SYSTEM_PROMPT


class AndonAgent(BaseAgent):
    name = "andon"
    system_prompt = ANDON_SYSTEM_PROMPT

    async def call_tools(self, message: str) -> Optional[str]:
        if any(k in message for k in ["创建", "报警", "呼叫", "上报"]):
            alert_type = _extract_alert_type(message)
            line = _extract_line(message)
            desc = _extract_description(message)
            if desc:
                data = await self._safe_call("create_andon_alert", create_andon_alert, alert_type, desc, line)
                if data.get("requires_approval"):
                    return f"⏳ {data['message']}\n审批通过后方可创建安灯报警。"
                return f"安灯报警已创建\n- ID: {data['andon_id']}\n- 类型: {data['type']}\n- 产线: {data['line']}\n- 描述: {data['description']}\n- 状态: {data['status']}\n- 级别: {data['level']}"
            return "请描述异常情况，例如：'创建安灯报警，SMT-01产线设备故障'"

        if "停线" in message:
            line = _extract_line(message)
            reason = _extract_description(message)
            if line:
                data = await self._safe_call("handle_line_stop", handle_line_stop, line, reason or "未指定")
                if data.get("requires_approval"):
                    return f"⏳ {data['message']}\n审批通过后方可执行停线操作。"
                return f"停线记录已创建\n- 产线: {data['line']}\n- 原因: {data['reason']}\n- 时间: {data['start_time']}\n- 状态: {data['status']}"
            return "请指定产线，例如：'SMT-01 停线，设备故障'"

        if any(k in message for k in ["活跃", "当前", "待处理", "处理中"]):
            line = _extract_line(message)
            data = await query_active_andons(line)
            return format_andon_report(data)

        if "历史" in message:
            data = await query_andon_history()
            return format_andon_report(data)

        if "升级" in message:
            andon_id = _extract_andon_id(message)
            level = _extract_escalation_level(message)
            if andon_id:
                data = await self._safe_call("escalate_andon", escalate_andon, andon_id, level)
                if data.get("requires_approval"):
                    return f"⏳ {data['message']}\n审批通过后方可执行升级操作。"
                return f"安灯升级: {data.get('andon_id')} -> {data.get('new_level')} ({data.get('status')})" if "error" not in data else data["error"]
            return "请提供安灯ID，例如：'升级 AN-2026-043 到生产经理'"

        if any(k in message for k in ["统计", "概况", "分析", "报表"]):
            data = await get_andon_stats()
            return format_stats_report(data)

        return None


def _extract_alert_type(message: str) -> str:
    for t in ANDON_TYPE_MAP:
        if t in message:
            return ANDON_TYPE_MAP[t]
    return DEFAULT_ANDON_TYPE


def _extract_line(message: str) -> Optional[str]:
    match = re.search(r'(SMT-\d+|DIP-\d+|组装-\d+)', message)
    return match.group(0) if match else None


def _extract_andon_id(message: str) -> Optional[str]:
    match = re.search(r'AN[-\s]?\d{4}[-\s]?\d+', message, re.IGNORECASE)
    return match.group(0).replace(" ", "") if match else None


def _extract_escalation_level(message: str) -> str:
    for keyword, level in ESCALATION_LEVEL_MAP.items():
        if keyword in message:
            return level
    return DEFAULT_ESCALATION_LEVEL


def _extract_description(message: str) -> str:
    for kw in ["报警", "呼叫", "停线", "上报", "异常", "故障"]:
        idx = message.find(kw)
        if idx >= 0:
            rest = message[idx + len(kw):].strip(" ，,：:")
            if rest:
                return rest
    return message


andon_agent = AndonAgent()
