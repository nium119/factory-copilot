"""工位终端工具 — 模拟数据 + MES CLI 接入
工位操作指导、生产报工、物料管理、异常上报、工位状态、质量自检
"""
import os
from datetime import datetime
from typing import Any, Dict, List

from app.agents.tools.mes_cli_runner import cli_or_mock
from app.core.logger import log

MES_API_ENABLED = os.getenv("MES_API_ENABLED", "false").lower() == "true"

# ─── 模拟数据 ───
MOCK_WORKSTATIONS = [
    {"ws_id": "WS-SMT-01-01", "line": "SMT-01", "name": "SMT-01 印刷工位", "status": "运行中", "operator": "张三"},
    {"ws_id": "WS-SMT-01-02", "line": "SMT-01", "name": "SMT-01 贴片工位", "status": "运行中", "operator": "李四"},
    {"ws_id": "WS-DIP-01-01", "line": "DIP-01", "name": "DIP-01 插件工位", "status": "运行中", "operator": "王五"},
    {"ws_id": "WS-ASM-01-01", "line": "组装-01", "name": "组装-01 装配工位", "status": "待机", "operator": None},
]

MOCK_CURRENT_WORK_ORDERS = {
    "WS-SMT-01-01": {"wo_id": "WO-2026-001", "product": "主板A", "process": "锡膏印刷", "plan_qty": 500, "completed": 380, "status": "生产中"},
    "WS-SMT-01-02": {"wo_id": "WO-2026-001", "product": "主板A", "process": "贴片", "plan_qty": 500, "completed": 375, "status": "生产中"},
    "WS-DIP-01-01": {"wo_id": "WO-2026-002", "product": "控制板B", "process": "插件", "plan_qty": 300, "completed": 180, "status": "生产中"},
}

MOCK_SOPS = {
    "锡膏印刷": {"sop_id": "SOP-SMT-PRINT-001", "title": "锡膏印刷作业指导书", "version": "V2.1", "steps": ["1.确认钢网型号", "2.安装钢网并锁紧", "3.添加锡膏(回温4h)", "4.设置印刷参数(压力0.5MPa,速度80mm/s)", "5.首件确认", "6.批量生产"]},
    "贴片": {"sop_id": "SOP-SMT-MOUNT-001", "title": "SMT贴片作业指导书", "version": "V3.2", "steps": ["1.确认程序已加载", "2.检查供料器站位", "3.首件贴装确认", "4.开启自动运行", "5.定时巡检(每2h)"]},
    "插件": {"sop_id": "SOP-DIP-INSERT-001", "title": "DIP插件作业指导书", "version": "V2.8", "steps": ["1.核对BOM和工艺卡", "2.按极性方向插件", "3.每盘物料扫码确认", "4.自检焊点质量"]},
}

MOCK_PROCESS_PARAMS = {
    "锡膏印刷": {"压力": "0.5MPa", "印刷速度": "80mm/s", "脱模速度": "2.0mm/s", "清洗频率": "每5片"},
    "贴片": {"贴装速度": "0.8s/元件", "贴装压力": "3.5N", "识别相机": "高像素", "吸嘴型号": "CN065"},
    "插件": "参考工艺卡参数",
}

MOCK_MATERIAL_AT_WS = {
    "WS-SMT-01-01": [
        {"material": "锡膏 SAC305", "batch": "B20260420", "qty": "5罐", "status": "充足"},
        {"material": "钢网清洗剂", "batch": "C20260415", "qty": "2瓶", "status": "低库存"},
    ],
    "WS-SMT-01-02": [
        {"material": "电阻 0402", "batch": "R20260418", "qty": "1500个", "status": "不足"},
        {"material": "IC芯片 STM32", "batch": "I20260420", "qty": "50个", "status": "严重不足"},
    ],
}

MOCK_REPORTS = []  # 报工记录
MOCK_INSPECTIONS = []  # 自检记录


async def get_workstation_info(ws_id: str = None) -> List[Dict[str, Any]]:
    """工位基本信息查询"""
    log.info(f"[工位终端] 查询工位信息: {ws_id}")
    cmd = ["ws", "info"]
    if ws_id:
        cmd.extend(["--station", ws_id])
    result = cli_or_mock(cmd, MOCK_WORKSTATIONS, MES_API_ENABLED)
    if isinstance(result, list):
        if ws_id:
            return [w for w in result if ws_id.lower() in w.get("ws_id", "").lower() or ws_id.lower() in w.get("name", "").lower()]
        return result
    return MOCK_WORKSTATIONS


async def get_current_work_order(ws_id: str) -> Dict[str, Any]:
    """获取工位当前工单"""
    log.info(f"[工位终端] 查询当前工单: {ws_id}")
    result = cli_or_mock(["ws", "wo-current", "--station", ws_id], {}, MES_API_ENABLED)
    if isinstance(result, dict) and result:
        return result
    return MOCK_CURRENT_WORK_ORDERS.get(ws_id, {"error": "当前无工单"})


async def start_work_order(ws_id: str, wo_id: str, operator: str, skip_approval: bool = False) -> Dict[str, Any]:
    """工单开工确认（需要审批）"""
    log.info(f"[工位终端] 工单开工: {wo_id} @ {ws_id}, 操作人: {operator}")
    if not skip_approval:
        from app.agents.approval import ApprovalManager
        approval = ApprovalManager.create_approval_request(
            action="wo_start",
            description=f"工单开工: {wo_id} @ {ws_id}",
            details={"ws_id": ws_id, "wo_id": wo_id, "operator": operator},
        )
        if approval:
            return {
                "requires_approval": True,
                "approval_id": approval["approval_id"],
                "message": f"工单开工需审批确认 (ID: {approval['approval_id']})",
            }
    if MES_API_ENABLED:
        return cli_or_mock(["ws", "wo-start", "--station", ws_id, "--wo", wo_id], {}, True)
    record = {"ws_id": ws_id, "wo_id": wo_id, "action": "开工", "operator": operator, "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
    MOCK_REPORTS.append(record)
    MOCK_CURRENT_WORK_ORDERS[ws_id] = {"wo_id": wo_id, "product": "待确认", "process": "待确认", "plan_qty": 0, "completed": 0, "status": "生产中"}
    for w in MOCK_WORKSTATIONS:
        if w["ws_id"] == ws_id:
            w["operator"] = operator
            w["status"] = "运行中"
    return record


async def complete_work_order(ws_id: str, good_qty: int, bad_qty: int, operator: str, skip_approval: bool = False) -> Dict[str, Any]:
    """工单完工报工（需要审批）"""
    log.info(f"[工位终端] 完工报工: {ws_id}, 良品={good_qty}, 不良品={bad_qty}")
    if not skip_approval:
        from app.agents.approval import ApprovalManager
        approval = ApprovalManager.create_approval_request(
            action="wo_complete",
            description=f"工单完工: {ws_id} 良品{good_qty} 不良品{bad_qty}",
            details={"ws_id": ws_id, "good_qty": good_qty, "bad_qty": bad_qty, "operator": operator},
        )
        if approval:
            return {
                "requires_approval": True,
                "approval_id": approval["approval_id"],
                "message": f"完工报工需审批确认 (ID: {approval['approval_id']})",
            }
    if MES_API_ENABLED:
        return cli_or_mock(["ws", "wo-complete", "--station", ws_id, "--qty", str(good_qty), "--defects", str(bad_qty)], {}, True)
    record = {
        "ws_id": ws_id, "action": "完工报工", "operator": operator,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "good_qty": good_qty, "bad_qty": bad_qty, "total": good_qty + bad_qty,
        "yield_rate": f"{good_qty / (good_qty + bad_qty) * 100:.1f}%" if (good_qty + bad_qty) > 0 else "0%",
    }
    MOCK_REPORTS.append(record)
    if ws_id in MOCK_CURRENT_WORK_ORDERS:
        MOCK_CURRENT_WORK_ORDERS[ws_id]["completed"] = good_qty
    return record


async def report_production(ws_id: str, qty: int, operator: str) -> Dict[str, Any]:
    """产量上报（阶段报工）"""
    log.info(f"[工位终端] 产量上报: {ws_id}, 数量={qty}")
    if MES_API_ENABLED:
        return cli_or_mock(["ws", "report-prod", "--station", ws_id, "--qty", str(qty), "--operator", operator], {}, True)
    record = {"ws_id": ws_id, "action": "阶段报工", "qty": qty, "operator": operator, "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
    MOCK_REPORTS.append(record)
    if ws_id in MOCK_CURRENT_WORK_ORDERS:
        MOCK_CURRENT_WORK_ORDERS[ws_id]["completed"] = qty
    return record


async def query_sop(ws_id: str = None, process: str = None) -> Dict[str, Any]:
    """SOP / 作业指导书查询"""
    log.info(f"[工位终端] SOP查询: 工位={ws_id}, 工序={process}")
    cmd = ["ws", "sop"]
    if process:
        cmd.extend(["--desc", process])
    if MES_API_ENABLED:
        result = cli_or_mock(cmd, {}, True)
        if result and isinstance(result, dict):
            return result
    if process and process in MOCK_SOPS:
        return {process: MOCK_SOPS[process]}
    if ws_id and ws_id in MOCK_CURRENT_WORK_ORDERS:
        p = MOCK_CURRENT_WORK_ORDERS[ws_id].get("process", "")
        if p in MOCK_SOPS:
            return {p: MOCK_SOPS[p]}
    return MOCK_SOPS


async def query_process_params(process: str = None) -> Dict[str, Any]:
    """工艺参数查询"""
    log.info(f"[工位终端] 工艺参数查询: 工序={process}")
    cmd = ["ws", "params"]
    if process:
        cmd.extend(["--desc", process])
    if MES_API_ENABLED:
        result = cli_or_mock(cmd, {}, True)
        if result and isinstance(result, dict):
            return result
    if process and process in MOCK_PROCESS_PARAMS:
        return {process: MOCK_PROCESS_PARAMS[process]}
    return MOCK_PROCESS_PARAMS


async def check_material_status(ws_id: str) -> Dict[str, Any]:
    """工位物料状态查询"""
    log.info(f"[工位终端] 物料状态查询: {ws_id}")
    if MES_API_ENABLED:
        return cli_or_mock(["ws", "material", "--station", ws_id], {}, True)
    materials = MOCK_MATERIAL_AT_WS.get(ws_id, [])
    shortage = [m for m in materials if m["status"] in ("不足", "严重不足")]
    return {"ws_id": ws_id, "materials": materials, "shortage_count": len(shortage), "shortage_items": shortage}


async def request_material(ws_id: str, material: str, qty: str = "") -> Dict[str, Any]:
    """领料申请 / 缺料呼叫"""
    log.info(f"[工位终端] 领料申请: {ws_id}, 物料={material}, 数量={qty}")
    if MES_API_ENABLED:
        return cli_or_mock(["ws", "request-mat", "--station", ws_id, "--desc", material, "--qty", qty or "1"], {}, True)
    req_id = f"MR-{len(MOCK_REPORTS) + 100:03d}"
    return {"req_id": req_id, "ws_id": ws_id, "material": material, "qty": qty, "status": "已提交", "time": datetime.now().strftime("%Y-%m-%d %H:%M")}


async def report_abnormal(ws_id: str, ab_type: str, description: str, operator: str) -> Dict[str, Any]:
    """异常上报（质量/设备/物料）"""
    log.info(f"[工位终端] 异常上报: {ws_id}, 类型={ab_type}, 描述={description}")
    if MES_API_ENABLED:
        return cli_or_mock(["ws", "report", "--station", ws_id, "--type", ab_type, "--desc", description], {}, True)
    ab_id = f"AB-{len(MOCK_REPORTS) + 200:03d}"
    return {"ab_id": ab_id, "ws_id": ws_id, "type": ab_type, "description": description, "operator": operator, "status": "已上报", "time": datetime.now().strftime("%Y-%m-%d %H:%M")}


async def first_article_confirm(ws_id: str, result: str, operator: str, notes: str = "") -> Dict[str, Any]:
    """首件确认"""
    log.info(f"[工位终端] 首件确认: {ws_id}, 结果={result}")
    if MES_API_ENABLED:
        return cli_or_mock(["ws", "fa-confirm", "--station", ws_id, "--desc", result, "--operator", operator], {}, True)
    record = {
        "ws_id": ws_id, "action": "首件确认", "result": result,
        "operator": operator, "notes": notes,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    MOCK_INSPECTIONS.append(record)
    return record


async def self_inspection(ws_id: str, check_items: List[str], result: str, operator: str, defects: str = "") -> Dict[str, Any]:
    """自检记录"""
    log.info(f"[工位终端] 自检记录: {ws_id}, 检查项={check_items}, 结果={result}")
    if MES_API_ENABLED:
        desc = f"检查项: {','.join(check_items)}, 结果: {result}" + (f", 缺陷: {defects}" if defects else "")
        return cli_or_mock(["ws", "self-inspect", "--station", ws_id, "--desc", desc, "--operator", operator], {}, True)
    record = {
        "ws_id": ws_id, "action": "自检", "check_items": check_items,
        "result": result, "operator": operator, "defects": defects,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    MOCK_INSPECTIONS.append(record)
    return record


async def operator_signin(ws_id: str, operator: str, shift: str = "") -> Dict[str, Any]:
    """工位人员签到"""
    log.info(f"[工位终端] 人员签到: {ws_id}, 操作人={operator}, 班次={shift}")
    if MES_API_ENABLED:
        cmd = ["ws", "signin", "--station", ws_id, "--operator", operator]
        if shift:
            cmd.extend(["--shift", shift])
        result = cli_or_mock(cmd, {}, True)
        if isinstance(result, dict) and result:
            return result
    for w in MOCK_WORKSTATIONS:
        if w["ws_id"] == ws_id:
            w["operator"] = operator
            if w["status"] == "待机":
                w["status"] = "运行中"
    return {"ws_id": ws_id, "operator": operator, "shift": shift, "time": datetime.now().strftime("%Y-%m-%d %H:%M"), "status": "签到成功"}


async def equipment_check(ws_id: str, check_items: List[str], result: str, operator: str) -> Dict[str, Any]:
    """设备点检确认"""
    log.info(f"[工位终端] 设备点检: {ws_id}, 检查项={check_items}")
    if MES_API_ENABLED:
        desc = f"检查项: {','.join(check_items)}, 结果: {result}"
        return cli_or_mock(["ws", "equip-check", "--station", ws_id, "--desc", desc, "--operator", operator], {}, True)
    return {"ws_id": ws_id, "action": "设备点检", "check_items": check_items, "result": result, "operator": operator, "time": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ─── 格式化工具 ───

def format_workstation_report(workstations: List[Dict]) -> str:
    lines = ["## 工位列表\n"]
    lines.append("| 工位ID | 产线 | 名称 | 状态 | 操作人 |")
    lines.append("|------|------|------|------|------|")
    for w in workstations:
        lines.append(f"| {w['ws_id']} | {w['line']} | {w['name']} | {w['status']} | {w['operator'] or '未分配'} |")
    return "\n".join(lines)


def format_work_order_report(wo: Dict) -> str:
    if "error" in wo:
        return f"工位当前无工单: {wo['error']}"
    progress = f"{wo['completed']}/{wo['plan_qty']}" if wo['plan_qty'] > 0 else "N/A"
    pct = f"{wo['completed'] / wo['plan_qty'] * 100:.1f}%" if wo['plan_qty'] > 0 else "0%"
    return f"## 当前工单\n- **工单号**: {wo['wo_id']}\n- **产品**: {wo['product']}\n- **工序**: {wo['process']}\n- **进度**: {progress} ({pct})\n- **状态**: {wo['status']}"


def format_sop_report(sops: Dict) -> str:
    lines = ["## 作业指导书(SOP)\n"]
    for process, sop in sops.items():
        lines.append(f"### {process} — {sop['title']} ({sop['version']})")
        for step in sop["steps"]:
            lines.append(f"  {step}")
        lines.append("")
    return "\n".join(lines)


def format_material_report(data: Dict) -> str:
    lines = [f"## 工位物料状态 ({data['ws_id']})\n"]
    if data["shortage_count"] > 0:
        lines.append(f"**[!] 缺料告警**: {data['shortage_count']} 项物料不足\n")
    for m in data["materials"]:
        icon = "[OK]" if m["status"] == "充足" else ("[!]" if m["status"] == "低库存" else "[X]")
        lines.append(f"{icon} {m['material']} ({m['batch']}): {m['qty']} -- {m['status']}")
    return "\n".join(lines)
