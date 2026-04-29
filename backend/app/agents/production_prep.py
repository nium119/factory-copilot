"""生产准备助手 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List
import json
import re

from app.agents.base import BaseAgent
from app.agents.planner import plan_tasks, execute_task
from app.agents.settings import PROCESS_KEYWORDS
from app.core.logger import log
from app.core.prompts import PRODUCTION_PREP_SYSTEM_PROMPT
from app.agents.tools.production_prep_tools import (
    check_material_readiness, check_equipment_readiness, check_mold_readiness,
    query_quality_standard, query_sop, query_process_card,
    check_work_order_readiness, format_readiness_report,
)
from app.services.llm_service import llm_service


class ProductionPrepAgent(BaseAgent):
    name = "production_prep"
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
        matched_agents: Optional[List[str]] = None,
    ) -> AsyncGenerator[tuple, None]:
        # 1. 工单综合齐套检查（不走 Planner，直接返回报告）
        if any(k in message for k in ["齐套检查", "准备检查", "投产准备", "工单准备"]):
            wo = _extract_wo_id(message)
            if wo:
                data = await check_work_order_readiness(wo)
                enhanced = f"{message}\n\n参考数据:\n{format_readiness_report(data)}"
            else:
                enhanced = f"{message}\n\n参考数据:\n请提供工单号，例如：'WO-2026-001 的齐套检查'"
            async for t, c in self._llm_stream(enhanced, session_id, model_name, use_agent, web_search, enable_thinking, context):
                yield t, c
            return

        # 2. Planner 动态规划 — emit SSE 事件
        wo = _extract_wo_id(message)
        tasks = plan_tasks(message, wo)
        if tasks:
            log.info(f"[Planner] 开始动态规划，{len(tasks)} 个子任务")
            yield "plan_start", json.dumps({"total": len(tasks), "title": "工单综合检查"})
            results = []
            for task in tasks:
                yield "plan_step", json.dumps({"key": task["key"], "name": task["name"], "status": "running"})
                result = await execute_task(task)
                status = result["status"]
                results.append(result)
                step_data = result.get("data")
                if step_data and isinstance(step_data, dict):
                    brief = {}
                    for k, v in step_data.items():
                        if isinstance(v, dict):
                            brief[k] = {"status": v.get("status", "未知")}
                            if v.get("shortage_items"):
                                brief[k]["shortages"] = [
                                    {"name": s["name"], "required": s["required"], "available": s["available"]}
                                    for s in v["shortage_items"]
                                ]
                        elif isinstance(v, str):
                            brief[k] = v
                    yield "plan_step", json.dumps({"key": task["key"], "name": task["name"], "status": status, "data": brief})
                else:
                    yield "plan_step", json.dumps({"key": task["key"], "name": task["name"], "status": status})
            success_count = sum(1 for r in results if r["status"] == "success")
            yield "plan_done", json.dumps({"success": success_count, "total": len(tasks)})
            enhanced = f"{message}\n\n参考数据:\n{_format_planned_results(results)}"
            async for t, c in self._llm_stream(enhanced, session_id, model_name, use_agent, web_search, enable_thinking, context):
                yield t, c
            return

        # 3. 兜底简单查询
        tool_result = await self.call_tools(message)
        enhanced = f"{message}\n\n参考数据:\n{tool_result}" if tool_result else message
        async for t, c in self._llm_stream(enhanced, session_id, model_name, use_agent, web_search, enable_thinking, context):
            yield t, c

    async def _llm_stream(self, enhanced_msg, session_id, model_name, use_agent, web_search, enable_thinking, context):
        """统一 LLM 流式调用"""
        async for t, c in llm_service.chat_stream(
            message=enhanced_msg, session_id=session_id,
            system_prompt=context.get("system_prompt", self.system_prompt) if context else self.system_prompt,
            model_name=model_name,
            use_agent=use_agent, web_search=web_search,
            enable_thinking=enable_thinking,
        ):
            yield t, c

    async def call_tools(self, message: str) -> Optional[str]:
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
    match = re.search(r'WO[-\s]?\d{4}[-\s]?\d+', message, re.IGNORECASE)
    return match.group(0).replace(" ", "") if match else None


def _extract_product(message: str) -> Optional[str]:
    match = re.search(r'["""]([^"""]+)["""]', message)
    return match.group(1) if match else None


def _extract_process(message: str) -> Optional[str]:
    for p in PROCESS_KEYWORDS:
        if p in message:
            return p
    return None


def _format_planned_results(results: list) -> str:
    """格式化 Planner 动态规划的执行结果"""
    lines = ["## 生产准备检查结果\n"]
    success_count = 0
    for r in results:
        if r["status"] == "success" and r.get("data"):
            success_count += 1
            lines.append(f"### {r['name']}")
            data = r["data"]
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict):
                        status = v.get("status", "未知")
                        lines.append(f"  **{k}**: {status}")
                        if v.get("shortage_items"):
                            for s in v["shortage_items"]:
                                lines.append(f"    [!] {s['name']}: 需求 {s['required']}, 可用 {s['available']}")
                    elif isinstance(v, list) and v:
                        for item in v:
                            if isinstance(item, dict):
                                name = item.get("name", item.get("equip", ""))
                                status = item.get("status", "")
                                lines.append(f"  - {name}: {status}")
            elif isinstance(data, list) and data:
                for item in data:
                    if isinstance(item, dict):
                        lines.append(f"  - {item.get('name', item.get('sop_id', ''))}: {item.get('status', '')}")
            lines.append("")
        elif r["status"] == "failed":
            lines.append(f"### {r['name']}: 执行失败 - {r.get('error', '未知错误')}\n")
    lines.append(f"**执行总结**: {success_count}/{len(results)} 项检查完成")
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
