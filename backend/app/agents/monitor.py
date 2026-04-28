"""KPI 目标监控 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List

from app.agents.base import BaseAgent
from app.agents.agent_config import AGENT_DEFINITIONS
from app.agents.settings import MANUFACTURING_KPIS
from app.core.logger import log
from app.core.prompts import MONITOR_SYSTEM_PROMPT
from app.agents.tools.monitor_tools import (
    query_kpi_targets, query_kpi_actuals, query_kpi_summary,
    query_kpi_trend, format_goal_report, format_trend_report,
)
from app.services.llm_service import llm_service


class MonitorAgent(BaseAgent):
    _meta = AGENT_DEFINITIONS["monitor"]
    name = "monitor"
    display_name = _meta["display_name"]
    icon = _meta["icon"]
    color = _meta["color"]
    description = _meta["description"]
    system_prompt = MONITOR_SYSTEM_PROMPT

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
        domain = _extract_domain(message)

        if any(k in message for k in ["趋势", "变化", "走势", "改善", "恶化"]):
            kpi_key = _extract_kpi_key(message)
            if kpi_key:
                data = await query_kpi_trend(kpi_key)
                return format_trend_report(data)
            # 趋势未指定具体指标，返回首个匹配领域的主指标趋势
            if domain:
                primary_kpis = {
                    "equipment": "oee",
                    "quality": "yield_rate",
                    "scheduling": "delivery_rate",
                    "inventory": "inventory_turnover",
                    "andon": "andon_response_time",
                    "production": "production_output",
                }
                kpi_key = primary_kpis.get(domain, "oee")
                data = await query_kpi_trend(kpi_key)
                return format_trend_report(data)
            return "请指定具体 KPI 指标，例如：'OEE 趋势'、'合格率变化'"

        if any(k in message for k in ["目标", "指标", "KPI", "标准"]):
            data = await query_kpi_targets(domain)
            targets = data.get("targets", {})
            lines = ["## KPI 目标值\n"]
            for k, v in targets.items():
                lines.append(f"- **{v['name']}**: {v['target']}{v['unit']} ({'越高越好' if v['direction'] == 'higher_better' else '越低越好'})")
            return "\n".join(lines)

        if any(k in message for k in ["实际", "当前值", "现状", "现在"]):
            data = await query_kpi_actuals(domain)
            actuals = data.get("actuals", {})
            lines = [f"## 当前 KPI 实际值 ({data['fetched_at']})\n"]
            for k, v in actuals.items():
                kpi_def = MANUFACTURING_KPIS.get(k, {})
                status = kpi_def.get("status", "")
                icon = {"on_track": "✅", "warning": "⚠️", "critical": "🔴"}.get(status, "")
                lines.append(f"- {icon} **{kpi_def.get('name', k)}**: {v}{kpi_def.get('unit', '')}")
            return "\n".join(lines)

        if any(k in message for k in ["对比", "达成", "偏差", "达标", "差距", "概览", "概况", "整体", "报告", "总结"]):
            data = await query_kpi_summary(domain)
            return format_goal_report(data)

        # 默认：返回 KPI 对比概览
        data = await query_kpi_summary(domain)
        return format_goal_report(data)


def _extract_domain(message: str) -> Optional[str]:
    domain_map = {
        "设备": "equipment", "OEE": "equipment", "开机": "equipment",
        "质量": "quality", "合格率": "quality", "不良": "quality", "缺陷": "quality",
        "排产": "scheduling", "交期": "scheduling", "换线": "scheduling",
        "库存": "inventory", "物料": "inventory", "缺料": "inventory",
        "安灯": "andon", "响应": "andon",
        "产出": "production", "产量": "production",
    }
    for keyword, d in domain_map.items():
        if keyword in message:
            return d
    return None


def _extract_kpi_key(message: str) -> Optional[str]:
    kpi_map = {
        "OEE": "oee", "设备效率": "oee",
        "开机率": "equipment_uptime", "开机": "equipment_uptime",
        "MTBF": "mtbf", "故障间隔": "mtbf",
        "MTTR": "mttr", "修复时间": "mttr",
        "合格率": "yield_rate", "一次合格": "yield_rate",
        "不良率": "defect_rate", "不良": "defect_rate",
        "Cpk": "cpk", "cpk": "cpk", "过程能力": "cpk",
        "交期": "delivery_rate", "交期达成": "delivery_rate",
        "平衡率": "balance_rate", "产线平衡": "balance_rate",
        "换线": "changeover_time", "换线时间": "changeover_time",
        "库存周转": "inventory_turnover", "周转率": "inventory_turnover",
        "缺料率": "shortage_rate",
        "响应时间": "andon_response_time", "安灯响应": "andon_response_time",
        "解决时间": "andon_resolve_time", "安灯解决": "andon_resolve_time",
        "产出": "production_output", "产量": "production_output", "日产出": "production_output",
    }
    for keyword, kpi_key in kpi_map.items():
        if keyword in message:
            return kpi_key
    return None


monitor_agent = MonitorAgent()
