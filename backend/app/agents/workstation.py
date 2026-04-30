"""工位终端助手 Agent"""
import re
from typing import Dict, Optional

from app.agents.base import BaseAgent
from app.agents.settings import (
    ABNORMAL_TYPES,
    DEFAULT_ABNORMAL_TYPE,
    INSPECTION_ITEMS_EQUIPMENT,
    INSPECTION_ITEMS_QUALITY,
    PROCESS_KEYWORDS,
    SHIFT_TYPES,
)
from app.agents.tools.workstation_tools import (
    check_material_status,
    complete_work_order,
    equipment_check,
    first_article_confirm,
    format_material_report,
    format_sop_report,
    format_work_order_report,
    format_workstation_report,
    get_current_work_order,
    get_workstation_info,
    operator_signin,
    query_process_params,
    query_sop,
    report_abnormal,
    report_production,
    request_material,
    self_inspection,
    start_work_order,
)
from app.core.prompts import WORKSTATION_SYSTEM_PROMPT


class WorkstationAgent(BaseAgent):
    name = "workstation"
    system_prompt = WORKSTATION_SYSTEM_PROMPT

    async def call_tools(self, message: str) -> Optional[str]:
        ws_id = _extract_ws_id(message)

        if any(k in message for k in ["工位状态", "工位列表", "工位信息", "所有工位"]):
            data = await get_workstation_info()
            return format_workstation_report(data)

        if any(k in message for k in ["当前工单", "工单信息", "在产工单"]):
            if ws_id:
                data = await get_current_work_order(ws_id)
                return format_work_order_report(data)
            return "请指定工位，例如：'WS-SMT-01-01 当前工单'"

        if "开工" in message:
            wo = _extract_wo_id(message)
            op = _extract_operator(message)
            if ws_id and wo:
                data = await self._safe_call("start_work_order", start_work_order, ws_id, wo, op or "系统")
                if data.get("requires_approval"):
                    return f"⏳ {data['message']}\n审批通过后方可执行开工操作。"
                return f"工单已开工\n- 工位: {data['ws_id']}\n- 工单: {data['wo_id']}\n- 操作人: {data['operator']}\n- 时间: {data['time']}"
            return "请提供工位、工单号和操作人，例如：'WS-SMT-01-01 开工 WO-2026-001 张三'"

        if any(k in message for k in ["完工", "完工报工", "报完工"]):
            good = _extract_qty(message, "良品", "良品数", "good")
            bad = _extract_qty(message, "不良", "不良数", "bad")
            op = _extract_operator(message)
            if ws_id and good is not None:
                data = await self._safe_call("complete_work_order", complete_work_order, ws_id, good, bad or 0, op or "系统")
                if data.get("requires_approval"):
                    return f"⏳ {data['message']}\n审批通过后方可执行完工操作。"
                return f"完工报工完成\n- 良品: {data['good_qty']} | 不良: {data['bad_qty']}\n- 良率: {data['yield_rate']}\n- 时间: {data['time']}"
            return "请提供工位数良品数，例如：'WS-SMT-01-01 完工 良品480 不良5 张三'"

        if any(k in message for k in ["产量上报", "报产量", "阶段报工"]):
            qty = _extract_number(message)
            op = _extract_operator(message)
            if ws_id and qty:
                data = await self._safe_call("report_production", report_production, ws_id, qty, op or "系统")
                return f"产量已上报\n- 工位: {data['ws_id']}\n- 数量: {data['qty']}\n- 时间: {data['time']}"
            return "请提供工位和数量，例如：'WS-SMT-01-01 产量上报 350 张三'"

        if any(k in message for k in ["SOP", "作业指导", "操作指导", "作业指导书"]):
            process = _extract_process(message)
            data = await query_sop(ws_id=ws_id, process=process)
            return format_sop_report(data)

        if any(k in message for k in ["工艺参数", "参数查询", "工位参数"]):
            process = _extract_process(message)
            data = await query_process_params(process)
            return _format_params_report(data)

        if any(k in message for k in ["物料状态", "工位物料", "物料查询"]):
            if ws_id:
                data = await check_material_status(ws_id)
                return format_material_report(data)
            return "请指定工位，例如：'WS-SMT-01-02 物料状态'"

        if any(k in message for k in ["领料", "缺料呼叫", "申请物料", "要料"]):
            material = _extract_material(message)
            qty = _extract_qty_word(message)
            if ws_id and material:
                data = await self._safe_call("request_material", request_material, ws_id, material, qty)
                return f"领料申请已提交\n- 申请号: {data['req_id']}\n- 物料: {data['material']}\n- 数量: {data['qty']}\n- 状态: {data['status']}"
            return "请提供工位和物料名称，例如：'WS-SMT-01-02 领料 电阻0402 2000个'"

        if any(k in message for k in ["异常上报", "异常报告", "上报异常", "工位异常"]):
            ab_type = _extract_ab_type(message)
            desc = _extract_description(message)
            op = _extract_operator(message)
            if ws_id:
                data = await self._safe_call("report_abnormal", report_abnormal, ws_id, ab_type, desc or "未描述", op or "系统")
                return f"异常已上报\n- 编号: {data['ab_id']}\n- 类型: {data['type']}\n- 描述: {data['description']}\n- 状态: {data['status']}"
            return "请指定工位和异常描述，例如：'WS-SMT-01-01 异常上报 质量异常 首件不合格'"

        if "首件" in message:
            result = "合格" if any(k in message for k in ["合格", "通过", "OK", "ok"]) else "不合格"
            op = _extract_operator(message)
            if ws_id:
                data = await self._safe_call("first_article_confirm", first_article_confirm, ws_id, result, op or "系统")
                if data.get("requires_approval"):
                    return f"⏳ {data['message']}\n审批通过后方可执行首件确认。"
                return f"首件确认已记录\n- 工位: {data['ws_id']}\n- 结果: {data['result']}\n- 操作人: {data['operator']}\n- 时间: {data['time']}"
            return "请指定工位和结果，例如：'WS-SMT-01-01 首件确认 合格 张三'"

        if any(k in message for k in ["自检", "自检记录", "质量自检"]):
            result = "合格" if any(k in message for k in ["合格", "通过", "OK", "ok"]) else "不合格"
            op = _extract_operator(message)
            if ws_id:
                data = await self._safe_call("self_inspection", self_inspection, ws_id, INSPECTION_ITEMS_QUALITY, result, op or "系统")
                if data.get("requires_approval"):
                    return f"⏳ {data['message']}\n审批通过后方可执行自检操作。"
                return f"自检记录已保存\n- 工位: {data['ws_id']}\n- 结果: {data['result']}\n- 操作人: {data['operator']}\n- 时间: {data['time']}"
            return "请指定工位，例如：'WS-SMT-01-01 自检 合格 张三'"

        if any(k in message for k in ["签到", "人员签到", "上班", "接班"]):
            op = _extract_operator(message)
            if ws_id and op:
                shift = _extract_shift(message)
                data = await self._safe_call("operator_signin", operator_signin, ws_id, op, shift)
                return f"签到成功\n- 工位: {data['ws_id']}\n- 操作人: {data['operator']}\n- 班次: {data['shift'] or '未指定'}\n- 时间: {data['time']}"
            return "请提供工位和操作人，例如：'WS-SMT-01-01 签到 张三 白班'"

        if any(k in message for k in ["点检", "设备点检", "点检确认"]):
            op = _extract_operator(message)
            if ws_id:
                data = await self._safe_call("equipment_check", equipment_check, ws_id, INSPECTION_ITEMS_EQUIPMENT, "正常", op or "系统")
                return f"设备点检已完成\n- 工位: {data['ws_id']}\n- 结果: {data['result']}\n- 操作人: {data['operator']}\n- 时间: {data['time']}"
            return "请指定工位，例如：'WS-SMT-01-01 设备点检 张三'"

        return None


def _extract_ws_id(message: str) -> Optional[str]:
    match = re.search(r'(WS-[A-Z0-9]+-\d+-\d+)', message)
    return match.group(0) if match else None


def _extract_wo_id(message: str) -> Optional[str]:
    match = re.search(r'(WO[-\s]?\d{4}[-\s]?\d+)', message, re.IGNORECASE)
    return match.group(0).replace(" ", "") if match else None


def _extract_operator(message: str) -> Optional[str]:
    match = re.search(r'([一-龥]{2,4})(?=\s*$|，|,|。|开工|完工|报工|签到|点检|自检|首件)', message)
    return match.group(0) if match else None


def _extract_process(message: str) -> Optional[str]:
    for p in PROCESS_KEYWORDS:
        if p in message:
            return p
    return None


def _extract_material(message: str) -> Optional[str]:
    for kw in ["领料", "物料", "要料"]:
        idx = message.find(kw)
        if idx >= 0:
            rest = re.sub(r'\d+\s*个|瓶|罐|件', '', message[idx + len(kw):].strip(" ，:：")).strip()
            if rest:
                return rest
    return None


def _extract_qty(message: str, *keywords: str) -> Optional[int]:
    for kw in keywords:
        match = re.search(rf'{kw}[:\s：]*(\d+)', message)
        if match:
            return int(match.group(1))
    match = re.search(r'(\d{1,5})', message)
    return int(match.group(1)) if match else None


def _extract_qty_word(message: str) -> str:
    match = re.search(r'(\d+)\s*(个|瓶|罐|件|kg)?', message)
    return match.group(0) if match else ""


def _extract_number(message: str) -> Optional[int]:
    match = re.search(r'(\d+)', message)
    return int(match.group(1)) if match else None


def _extract_ab_type(message: str) -> str:
    for t in ABNORMAL_TYPES:
        if t in message:
            return t
    return DEFAULT_ABNORMAL_TYPE


def _extract_description(message: str) -> Optional[str]:
    for kw in ["异常上报", "异常报告", "异常", "报警"]:
        idx = message.find(kw)
        if idx >= 0:
            rest = message[idx + len(kw):].strip(" ，:：")
            if rest:
                return rest
    return None


def _extract_shift(message: str) -> str:
    for s in SHIFT_TYPES:
        if s in message:
            return s
    if "晚班" in message:
        return "夜班"
    return ""


def _format_params_report(data: Dict) -> str:
    lines = ["## 工艺参数\n"]
    for process, params in data.items():
        lines.append(f"**{process}**:")
        if isinstance(params, dict):
            for k, v in params.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append(f"  {params}")
    return "\n".join(lines)


workstation_agent = WorkstationAgent()
