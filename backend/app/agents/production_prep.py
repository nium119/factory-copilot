"""生产准备助手 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List

from app.agents.base import BaseAgent
from app.core.logger import log
from app.core.prompts import PRODUCTION_PREP_SYSTEM_PROMPT
from app.agents.tools.production_prep_tools import (
    check_material_readiness, check_equipment_readiness, check_mold_readiness,
    query_quality_standard, query_sop, query_process_card,
    check_work_order_readiness, format_readiness_report,
)
from app.services.llm_service import llm_service


class ProductionPrepAgent(BaseAgent):
    """生产准备助手 - 工单投产前的物料/设备/模具/质检/SOP/工艺卡准备"""

    name = "production_prep"
    display_name = "生产准备助手"
    icon = "📋"
    color = "#20bf6b"
    description = "生产准备管理助手，支持工序工单的物料齐套检查、设备状态确认、模具准备、质检标准查询、SOP查看、工艺卡配置与工单投产前准备"
    system_prompt = PRODUCTION_PREP_SYSTEM_PROMPT

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
        # 工单齐套检查（跨维度聚合）
        if any(k in message for k in ["齐套检查", "准备检查", "投产准备", "工单准备"]):
            # 提取工单号
            wo = _extract_wo_id(message)
            if wo:
                data = await check_work_order_readiness(wo)
                return format_readiness_report(data)
            return "请提供工单号，例如：'WO-2026-001 的齐套检查'"

        # 物料齐套
        if any(k in message for k in ["物料齐套", "物料准备", "缺料"]):
            wo = _extract_wo_id(message)
            data = await check_material_readiness(wo)
            return _format_material_report(data)

        # 设备确认
        if any(k in message for k in ["设备确认", "设备准备", "设备状态"]):
            wo = _extract_wo_id(message)
            data = await check_equipment_readiness(wo)
            return _format_equipment_report(data)

        # 模具准备
        if any(k in message for k in ["模具准备", "模具", "治具"]):
            wo = _extract_wo_id(message)
            data = await check_mold_readiness(wo)
            return _format_mold_report(data)

        # 质检标准
        if "质检标准" in message or "检验标准" in message:
            product = _extract_product(message)
            data = await query_quality_standard(product)
            return _format_quality_report(data)

        # SOP
        if "SOP" in message or "作业指导" in message:
            process = _extract_process(message)
            data = await query_sop(process)
            return _format_sop_report(data)

        # 工艺卡
        if "工艺卡" in message:
            wo = _extract_wo_id(message)
            data = await query_process_card(wo)
            return _format_card_report(data)

        return None


def _extract_wo_id(message: str) -> Optional[str]:
    import re
    match = re.search(r'WO[-\s]?\d{4}[-\s]?\d+', message, re.IGNORECASE)
    return match.group(0).replace(" ", "") if match else None


def _extract_product(message: str) -> Optional[str]:
    import re
    match = re.search(r'["“”]([^"“”]+)["“”]', message)
    return match.group(1) if match else None


def _extract_process(message: str) -> Optional[str]:
    for p in ["SMT", "DIP", "组装", "贴片", "插件", "波峰焊", "回流焊"]:
        if p in message:
            return p
    return None


def _format_material_report(data: dict) -> str:
    lines = ["## 物料齐套检查\n"]
    for wo, info in data.items():
        lines.append(f"**{wo}**: 状态={info['status']} | 物料 {info['ready_items']}/{info['total_items']} 项齐套")
        if info.get("shortage_items"):
            for s in info["shortage_items"]:
                lines.append(f"  ⚠ {s['name']}: 需求 {s['required']}, 可用 {s['available']}")
    return "\n".join(lines)


def _format_equipment_report(data: dict) -> str:
    lines = ["## 设备状态确认\n"]
    for line, equips in data.items():
        lines.append(f"**{line}**:")
        for e in equips:
            lines.append(f"  {e['equip']}: {e['status']} (OEE {e['oee']}%)")
    return "\n".join(lines)


def _format_mold_report(data: dict) -> str:
    lines = ["## 模具准备\n"]
    for wo, info in data.items():
        lines.append(f"**{wo}**: {'就绪' if info['ready'] else '存在异常'}")
        for m in info["molds"]:
            lines.append(f"  {m['mold']}: {m['status']} ({m['location']})")
    return "\n".join(lines)


def _format_quality_report(data: dict) -> str:
    lines = ["## 质检标准\n"]
    for prod, qs in data.items():
        lines.append(f"**{prod}**: 标准={qs['standard']} | 检验项 {qs['inspect_items']} 项 | 目标良率 {qs['pass_rate_target']}%")
    return "\n".join(lines)


def _format_sop_report(data: list) -> str:
    if not data:
        return "未找到匹配的 SOP。"
    lines = ["## SOP 列表\n"]
    for s in data:
        lines.append(f"**{s['sop_id']}** ({s['version']}): {s['title']} - {s['status']}")
    return "\n".join(lines)


def _format_card_report(data: dict) -> str:
    lines = ["## 工艺卡配置\n"]
    for wo, card in data.items():
        lines.append(f"**{wo}** ({card.get('card_id', 'N/A')}):")
        lines.append(f"  工序: {' → '.join(card.get('processes', []))}")
        params = card.get("parameters", {})
        if params:
            lines.append(f"  参数: {', '.join(f'{k}={v}' for k, v in params.items())}")
    return "\n".join(lines)


production_prep_agent = ProductionPrepAgent()
