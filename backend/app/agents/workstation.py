"""工位终端助手 Agent"""
from typing import Optional, Dict, Any, AsyncGenerator, List

from app.agents.base import BaseAgent
from app.core.logger import log
from app.core.prompts import WORKSTATION_SYSTEM_PROMPT
from app.agents.tools.workstation_tools import (
    get_workstation_info, get_current_work_order,
    start_work_order, complete_work_order, report_production,
    query_sop, query_process_params,
    check_material_status, request_material,
    report_abnormal, first_article_confirm, self_inspection,
    operator_signin, equipment_check,
    format_workstation_report, format_work_order_report,
    format_sop_report, format_material_report,
)
from app.services.llm_service import llm_service


class WorkstationAgent(BaseAgent):
    """工位终端助手 — 工位操作指导、生产报工、物料管理、异常上报、工位状态、质量自检"""

    name = "workstation"
    display_name = "工位终端助手"
    icon = "🖥️"
    color = "#45aaf2"
    description = "工位终端操作助手，支持工单开工/完工报工、SOP查看、工艺参数查询、物料状态、异常上报、首件确认、自检记录、人员签到与设备点检"
    keywords = ["工位", "终端", "报工", "开工", "完工", "SOP查看", "首件确认", "自检", "签到", "点检", "异常上报", "领料", "产量上报"]
    system_prompt = WORKSTATION_SYSTEM_PROMPT

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
        ws_id = _extract_ws_id(message)

        # 工位状态 / 工位列表
        if any(k in message for k in ["工位状态", "工位列表", "工位信息", "所有工位"]):
            data = await get_workstation_info()
            return format_workstation_report(data)

        # 当前工单
        if any(k in message for k in ["当前工单", "工单信息", "在产工单"]):
            if ws_id:
                data = await get_current_work_order(ws_id)
                return format_work_order_report(data)
            return "请指定工位，例如：'WS-SMT-01-01 当前工单'"

        # 工单开工
        if "开工" in message:
            wo = _extract_wo_id(message)
            op = _extract_operator(message)
            if ws_id and wo:
                data = await start_work_order(ws_id, wo, op or "系统")
                return f"工单已开工\n- 工位: {data['ws_id']}\n- 工单: {data['wo_id']}\n- 操作人: {data['operator']}\n- 时间: {data['time']}"
            return "请提供工位、工单号和操作人，例如：'WS-SMT-01-01 开工 WO-2026-001 张三'"

        # 完工报工
        if any(k in message for k in ["完工", "完工报工", "报完工"]):
            good = _extract_qty(message, "良品", "良品数", "good")
            bad = _extract_qty(message, "不良", "不良数", "bad")
            op = _extract_operator(message)
            if ws_id and good is not None:
                data = await complete_work_order(ws_id, good, bad or 0, op or "系统")
                return f"完工报工完成\n- 良品: {data['good_qty']} | 不良: {data['bad_qty']}\n- 良率: {data['yield_rate']}\n- 时间: {data['time']}"
            return "请提供工位数良品数，例如：'WS-SMT-01-01 完工 良品480 不良5 张三'"

        # 产量上报
        if any(k in message for k in ["产量上报", "报产量", "阶段报工"]):
            qty = _extract_number(message)
            op = _extract_operator(message)
            if ws_id and qty:
                data = await report_production(ws_id, qty, op or "系统")
                return f"产量已上报\n- 工位: {data['ws_id']}\n- 数量: {data['qty']}\n- 时间: {data['time']}"
            return "请提供工位和数量，例如：'WS-SMT-01-01 产量上报 350 张三'"

        # SOP / 作业指导
        if any(k in message for k in ["SOP", "作业指导", "操作指导", "作业指导书"]):
            process = _extract_process(message)
            data = await query_sop(ws_id=ws_id, process=process)
            return format_sop_report(data)

        # 工艺参数
        if any(k in message for k in ["工艺参数", "参数查询", "工位参数"]):
            process = _extract_process(message)
            data = await query_process_params(process)
            return _format_params_report(data)

        # 物料状态
        if any(k in message for k in ["物料状态", "工位物料", "物料查询"]):
            if ws_id:
                data = await check_material_status(ws_id)
                return format_material_report(data)
            return "请指定工位，例如：'WS-SMT-01-02 物料状态'"

        # 领料 / 缺料呼叫
        if any(k in message for k in ["领料", "缺料呼叫", "申请物料", "要料"]):
            material = _extract_material(message)
            qty = _extract_qty_word(message)
            if ws_id and material:
                data = await request_material(ws_id, material, qty)
                return f"领料申请已提交\n- 申请号: {data['req_id']}\n- 物料: {data['material']}\n- 数量: {data['qty']}\n- 状态: {data['status']}"
            return "请提供工位和物料名称，例如：'WS-SMT-01-02 领料 电阻0402 2000个'"

        # 异常上报
        if any(k in message for k in ["异常上报", "异常报告", "上报异常", "工位异常"]):
            ab_type = _extract_ab_type(message)
            desc = _extract_description(message)
            op = _extract_operator(message)
            if ws_id:
                data = await report_abnormal(ws_id, ab_type, desc or "未描述", op or "系统")
                return f"异常已上报\n- 编号: {data['ab_id']}\n- 类型: {data['type']}\n- 描述: {data['description']}\n- 状态: {data['status']}"
            return "请指定工位和异常描述，例如：'WS-SMT-01-01 异常上报 质量异常 首件不合格'"

        # 首件确认
        if "首件" in message:
            result = "合格" if any(k in message for k in ["合格", "通过", "OK", "ok"]) else "不合格"
            op = _extract_operator(message)
            if ws_id:
                data = await first_article_confirm(ws_id, result, op or "系统")
                return f"首件确认已记录\n- 工位: {data['ws_id']}\n- 结果: {data['result']}\n- 操作人: {data['operator']}\n- 时间: {data['time']}"
            return "请指定工位和结果，例如：'WS-SMT-01-01 首件确认 合格 张三'"

        # 自检
        if any(k in message for k in ["自检", "自检记录", "质量自检"]):
            result = "合格" if any(k in message for k in ["合格", "通过", "OK", "ok"]) else "不合格"
            op = _extract_operator(message)
            if ws_id:
                data = await self_inspection(ws_id, ["外观", "尺寸", "功能"], result, op or "系统")
                return f"自检记录已保存\n- 工位: {data['ws_id']}\n- 结果: {data['result']}\n- 操作人: {data['operator']}\n- 时间: {data['time']}"
            return "请指定工位，例如：'WS-SMT-01-01 自检 合格 张三'"

        # 人员签到
        if any(k in message for k in ["签到", "人员签到", "上班", "接班"]):
            op = _extract_operator(message)
            if ws_id and op:
                shift = _extract_shift(message)
                data = await operator_signin(ws_id, op, shift)
                return f"签到成功\n- 工位: {data['ws_id']}\n- 操作人: {data['operator']}\n- 班次: {data['shift'] or '未指定'}\n- 时间: {data['time']}"
            return "请提供工位和操作人，例如：'WS-SMT-01-01 签到 张三 白班'"

        # 设备点检
        if any(k in message for k in ["点检", "设备点检", "点检确认"]):
            op = _extract_operator(message)
            if ws_id:
                data = await equipment_check(ws_id, ["设备运行状态", "安全防护", "环境参数"], "正常", op or "系统")
                return f"设备点检已完成\n- 工位: {data['ws_id']}\n- 结果: {data['result']}\n- 操作人: {data['operator']}\n- 时间: {data['time']}"
            return "请指定工位，例如：'WS-SMT-01-01 设备点检 张三'"

        return None


def _extract_ws_id(message: str) -> Optional[str]:
    import re
    match = re.search(r'(WS-[A-Z0-9]+-\d+-\d+)', message)
    return match.group(0) if match else None


def _extract_wo_id(message: str) -> Optional[str]:
    import re
    match = re.search(r'(WO[-\s]?\d{4}[-\s]?\d+)', message, re.IGNORECASE)
    return match.group(0).replace(" ", "") if match else None


def _extract_operator(message: str) -> Optional[str]:
    import re
    match = re.search(r'([一-龥]{2,4})(?=\s*$|，|,|。|开工|完工|报工|签到|点检|自检|首件)', message)
    return match.group(0) if match else None


def _extract_process(message: str) -> Optional[str]:
    for p in ["锡膏印刷", "贴片", "插件", "SMT贴片", "DIP插件", "组装"]:
        if p in message:
            return p
    return None


def _extract_material(message: str) -> Optional[str]:
    import re
    # Try to find material name in quotes or after 领料/物料
    for kw in ["领料", "物料", "要料"]:
        idx = message.find(kw)
        if idx >= 0:
            rest = message[idx + len(kw):].strip(" ，:：")
            # Remove qty part
            rest = re.sub(r'\d+\s*个|瓶|罐|件', '', rest).strip()
            if rest:
                return rest
    return None


def _extract_qty(message: str, *keywords: str) -> Optional[int]:
    import re
    for kw in keywords:
        match = re.search(rf'{kw}[:\s：]*(\d+)', message)
        if match:
            return int(match.group(1))
    # Fallback: first number in message
    match = re.search(r'(\d{1,5})', message)
    return int(match.group(1)) if match else None


def _extract_qty_word(message: str) -> str:
    import re
    match = re.search(r'(\d+)\s*(个|瓶|罐|件|kg)?', message)
    return match.group(0) if match else ""


def _extract_number(message: str) -> Optional[int]:
    import re
    match = re.search(r'(\d+)', message)
    return int(match.group(1)) if match else None


def _extract_ab_type(message: str) -> str:
    for t in ["质量异常", "设备异常", "物料异常"]:
        if t in message:
            return t
    return "其他异常"


def _extract_description(message: str) -> Optional[str]:
    for kw in ["异常上报", "异常报告", "异常", "报警"]:
        idx = message.find(kw)
        if idx >= 0:
            rest = message[idx + len(kw):].strip(" ，:：")
            if rest:
                return rest
    return None


def _extract_shift(message: str) -> str:
    if "白班" in message:
        return "白班"
    if "夜班" in message or "晚班" in message:
        return "夜班"
    if "中班" in message:
        return "中班"
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
