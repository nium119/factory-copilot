"""生产准备工具 — 模拟数据 + 预留 MES API 接入
工单投产前的物料齐套检查、设备确认、模具准备、质检标准、SOP、工艺卡配置
跨 Agent 工具共享：复用 inventory/equipment/quality 工具的数据
"""
from typing import Dict, Any, Optional, List
from app.core.logger import log

# 跨 Agent 工具共享（用于齐套检查时查询实时数据）
from app.agents.tools.inventory_tools import query_inventory as _query_inventory
from app.agents.tools.equipment_tools import query_equipment as _query_equipment
from app.agents.tools.quality_tools import query_quality_report as _query_quality_report

MES_API_BASE = "http://localhost:9090"
MES_API_ENABLED = False

# ─── 模拟数据 ───
MOCK_WORK_ORDERS = [
    {"wo_id": "WO-2026-001", "product": "主板A", "process": "SMT贴片", "line": "SMT-01", "plan_qty": 500, "status": "待准备"},
    {"wo_id": "WO-2026-002", "product": "控制板B", "process": "DIP插件", "line": "DIP-01", "plan_qty": 300, "status": "已准备"},
    {"wo_id": "WO-2026-003", "product": "电源模块C", "process": "组装", "line": "组装-01", "plan_qty": 200, "status": "待准备"},
]

MOCK_MATERIAL_BOM = {
    "WO-2026-001": [
        {"material_code": "M-001", "name": "PCB板 120x80", "required": 520, "available": 800, "status": "充足"},
        {"material_code": "M-002", "name": "电阻 0402", "required": 2000, "available": 1500, "status": "不足"},
        {"material_code": "M-003", "name": "锡膏 SAC305", "required": 5, "available": 12, "status": "充足"},
        {"material_code": "M-004", "name": "IC芯片 STM32", "required": 520, "available": 50, "status": "严重不足"},
    ],
    "WO-2026-002": [
        {"material_code": "M-010", "name": "连接器 JST-4P", "required": 320, "available": 500, "status": "充足"},
        {"material_code": "M-011", "name": "电容 100uF", "required": 1000, "available": 1200, "status": "充足"},
    ],
    "WO-2026-003": [
        {"material_code": "M-020", "name": "外壳组件 A型", "required": 200, "available": 180, "status": "不足"},
        {"material_code": "M-021", "name": "散热片 40x40", "required": 200, "available": 300, "status": "充足"},
    ],
}

MOCK_EQUIPMENT_STATUS = {
    "SMT-01": [{"equip": "贴片机 NPM-W2", "status": "运行中", "oee": 87}, {"equip": "锡膏印刷机", "status": "运行中", "oee": 92}],
    "DIP-01": [{"equip": "波峰焊", "status": "运行中", "oee": 78}, {"equip": "选择性涂覆机", "status": "维护中", "oee": 0}],
    "组装-01": [{"equip": "电动螺丝刀组", "status": "运行中", "oee": 95}, {"equip": "测试台", "status": "待确认", "oee": 0}],
}

MOCK_MOLD_STATUS = {
    "WO-2026-001": [{"mold": "SMT钢网 120x80", "status": "在库", "location": "模具库-A03"}],
    "WO-2026-002": [{"mold": "DIP治具 JST-4P", "status": "在线", "location": "DIP-01产线"}],
    "WO-2026-003": [{"mold": "组装治具 A型", "status": "维修中", "location": "模具维修区"}],
}

MOCK_QUALITY_STANDARDS = {
    "主板A": {"standard": "IPC-A-610 Class 2", "inspect_items": 15, "pass_rate_target": 98.5, "last_inspection": "2026-04-22"},
    "控制板B": {"standard": "IPC-A-610 Class 2", "inspect_items": 12, "pass_rate_target": 97.0, "last_inspection": "2026-04-21"},
    "电源模块C": {"standard": "IPC-A-610 Class 3", "inspect_items": 20, "pass_rate_target": 99.0, "last_inspection": "2026-04-20"},
}

MOCK_SOPS = [
    {"sop_id": "SOP-SMT-001", "process": "SMT贴片", "version": "V3.2", "title": "SMT贴片作业指导书", "status": "有效"},
    {"sop_id": "SOP-DIP-001", "process": "DIP插件", "version": "V2.8", "title": "DIP插件作业指导书", "status": "有效"},
    {"sop_id": "SOP-ASM-001", "process": "组装", "version": "V4.0", "title": "产品组装作业指导书", "status": "有效"},
]

MOCK_PROCESS_CARDS = {
    "WO-2026-001": {
        "card_id": "PC-SMT-001",
        "processes": ["锡膏印刷", "贴片", "回流焊", "AOI检测"],
        "parameters": {"炉温": "245°C", "速度": "0.8m/min", "压力": "0.5MPa"},
    },
    "WO-2026-002": {
        "card_id": "PC-DIP-001",
        "processes": ["插件", "波峰焊", "剪脚", "清洗"],
        "parameters": {"炉温": "260°C", "链速": "1.2m/min", "助焊剂": "免洗型"},
    },
    "WO-2026-003": {
        "card_id": "PC-ASM-001",
        "processes": ["组装", "测试", "包装"],
        "parameters": {"扭矩": "2.5N·m", "测试电压": "5V DC", "包装": "防静电袋"},
    },
}


async def check_material_readiness(work_order: Optional[str] = None) -> Dict[str, Any]:
    """物料齐套检查"""
    log.info(f"[生产准备] 物料齐套检查, 工单: {work_order}")
    if MES_API_ENABLED:
        pass
    results = {}
    targets = [work_order] if work_order else list(MOCK_MATERIAL_BOM.keys())
    for wo in targets:
        if wo in MOCK_MATERIAL_BOM:
            items = MOCK_MATERIAL_BOM[wo]
            shortage = [i for i in items if i["status"] != "充足"]
            results[wo] = {
                "total_items": len(items),
                "ready_items": len(items) - len(shortage),
                "shortage_items": [{"name": i["name"], "required": i["required"], "available": i["available"]} for i in shortage],
                "status": "齐套" if not shortage else "缺料",
            }
    return results


async def check_equipment_readiness(work_order: Optional[str] = None) -> Dict[str, Any]:
    """设备状态确认"""
    log.info(f"[生产准备] 设备状态确认, 工单: {work_order}")
    results = {}
    if work_order and work_order in MOCK_WORK_ORDERS:
        line = next((w["line"] for w in MOCK_WORK_ORDERS if w["wo_id"] == work_order), None)
        if line:
            results[line] = MOCK_EQUIPMENT_STATUS.get(line, [])
    else:
        results = MOCK_EQUIPMENT_STATUS
    return results


async def check_mold_readiness(work_order: Optional[str] = None) -> Dict[str, Any]:
    """模具准备检查"""
    log.info(f"[生产准备] 模具准备检查, 工单: {work_order}")
    results = {}
    targets = [work_order] if work_order else list(MOCK_MOLD_STATUS.keys())
    for wo in targets:
        if wo in MOCK_MOLD_STATUS:
            molds = MOCK_MOLD_STATUS[wo]
            not_ready = [m for m in molds if m["status"] not in ("在库", "在线")]
            results[wo] = {"molds": molds, "ready": not not_ready, "issues": [m["mold"] + " - " + m["status"] for m in not_ready]}
    return results


async def query_quality_standard(product: Optional[str] = None) -> Dict[str, Any]:
    """质检标准查询"""
    log.info(f"[生产准备] 质检标准查询, 产品: {product}")
    if product and product in MOCK_QUALITY_STANDARDS:
        return {product: MOCK_QUALITY_STANDARDS[product]}
    return MOCK_QUALITY_STANDARDS


async def query_sop(process: Optional[str] = None) -> List[Dict[str, Any]]:
    """SOP 查询"""
    log.info(f"[生产准备] SOP查询, 工序: {process}")
    if process:
        return [s for s in MOCK_SOPS if process in s["process"] or process in s["title"]]
    return MOCK_SOPS


async def query_process_card(work_order: Optional[str] = None) -> Dict[str, Any]:
    """工艺卡配置查询"""
    log.info(f"[生产准备] 工艺卡查询, 工单: {work_order}")
    if work_order and work_order in MOCK_PROCESS_CARDS:
        return {work_order: MOCK_PROCESS_CARDS[work_order]}
    return MOCK_PROCESS_CARDS


async def check_work_order_readiness(work_order: str) -> Dict[str, Any]:
    """工单全流程齐套检查 — 聚合物料/设备/模具/质检/SOP/工艺卡
    跨 Agent 工具共享：调用 inventory/equipment/quality Agent 的工具获取实时数据
    """
    log.info(f"[生产准备] 工单齐套检查: {work_order}")
    wo_ids = [w["wo_id"] for w in MOCK_WORK_ORDERS]
    if work_order not in wo_ids:
        return {"error": f"工单 {work_order} 不存在"}

    wo = next(w for w in MOCK_WORK_ORDERS if w["wo_id"] == work_order)
    product = wo["product"]
    process = wo["process"]
    line = wo["line"]

    # ─── 跨 Agent 工具调用：从其他 Agent 获取实时数据 ───
    # 调用 inventory Agent 工具：查询产线相关物料
    inv_results = await _query_inventory()  # 获取全量库存
    # 用 BOM 中的物料名称到库存中匹配
    bom = MOCK_MATERIAL_BOM.get(work_order, [])
    cross_inv_matches = []
    for item in bom:
        matches = [i for i in inv_results if item["name"] in i["name"] or item["material_code"] in i["sku"]]
        cross_inv_matches.extend(matches)

    # 调用 equipment Agent 工具：查询产线设备状态
    equip_results = await _query_equipment(line)

    # 调用 quality Agent 工具：查询产品质量数据
    quality_results = await _query_quality_report(product)

    # ─── 本地工具调用：模具/SOP/工艺卡 ───
    material = await check_material_readiness(work_order)
    equipment = await check_equipment_readiness(work_order)
    mold = await check_mold_readiness(work_order)
    quality = await query_quality_standard(product)
    sop = await query_sop(process)
    card = await query_process_card(work_order)

    # ─── 综合判定 ───
    issues = []
    if work_order in material and material[work_order]["status"] == "缺料":
        issues.extend([f"物料不足: {s['name']}" for s in material[work_order]["shortage_items"]])
    if work_order in mold and not mold[work_order]["ready"]:
        issues.extend([f"模具异常: {i}" for i in mold[work_order]["issues"]])
    # 跨 Agent 数据补充的告警
    for inv in cross_inv_matches:
        if inv["status"] in ("预警", "缺料"):
            issues.append(f"库存预警: {inv['name']} (库存 {inv['stock']} / 安全 {inv['safety_stock']})")

    return {
        "work_order": wo,
        "material": material.get(work_order, {}),
        "equipment": equipment,
        "mold": mold.get(work_order, {}),
        "quality": quality,
        "quality_cross": quality_results if quality_results else None,
        "sop": sop,
        "process_card": card.get(work_order, {}),
        "cross_inventory": cross_inv_matches,
        "cross_equipment": equip_results,
        "overall_status": "不通过" if issues else "通过",
        "issues": issues,
    }


def format_readiness_report(data: Dict[str, Any]) -> str:
    """格式化齐套检查报告"""
    lines = ["## 工单投产前齐套检查报告\n"]
    if "error" in data:
        return f"错误: {data['error']}"

    wo = data.get("work_order", {})
    lines.append(f"**工单**: {wo.get('wo_id', 'N/A')} | **产品**: {wo.get('product', 'N/A')} | **工序**: {wo.get('process', 'N/A')} | **产线**: {wo.get('line', 'N/A')}\n")

    # 物料
    mat = data.get("material", {})
    lines.append("### 物料齐套")
    lines.append(f"状态: {mat.get('status', '未知')} | 物料项: {mat.get('total_items', 0)} | 就绪: {mat.get('ready_items', 0)}")
    if mat.get("shortage_items"):
        for s in mat["shortage_items"]:
            lines.append(f"  [!] {s['name']}: 需求 {s['required']}, 可用 {s['available']}")

    # 跨 Agent 库存数据
    cross_inv = data.get("cross_inventory", [])
    if cross_inv:
        lines.append("\n### 关联库存(跨Agent)")
        for inv in cross_inv:
            icon = "[OK]" if inv["status"] == "充足" else "[!]"
            lines.append(f"  {icon} {inv['name']}: {inv['stock']} {inv['unit']} (安全库存 {inv['safety_stock']})")

    # 模具
    mold = data.get("mold", {})
    lines.append("\n### 模具准备")
    if mold.get("molds"):
        for m in mold["molds"]:
            status_icon = "[OK]" if m["status"] in ("在库", "在线") else "[!]"
            lines.append(f"  {status_icon} {m['mold']}: {m['status']} ({m['location']})")

    # 跨 Agent 设备数据
    cross_equip = data.get("cross_equipment", [])
    if cross_equip:
        lines.append("\n### 产线设备(跨Agent)")
        for eq in cross_equip:
            icon = "[OK]" if eq["status"] == "运行中" else "[!]"
            oee_str = f"{eq['oee']}%" if eq['oee'] > 0 else "-"
            lines.append(f"  {icon} {eq['name']}: {eq['status']} (OEE {oee_str})")

    # 质检
    quality = data.get("quality", {})
    if quality:
        lines.append("\n### 质检标准")
        for prod, qs in quality.items():
            lines.append(f"  {prod}: {qs['standard']}, 目标良率 {qs['pass_rate_target']}%")

    # 跨 Agent 质量数据
    quality_cross = data.get("quality_cross")
    if quality_cross and isinstance(quality_cross, list):
        lines.append("\n### 质量报告(跨Agent)")
        for qr in quality_cross:
            lines.append(f"  {qr.get('product', 'N/A')}: 批次良率 {qr.get('pass_rate', 'N/A')}%, 检测 {qr.get('inspected', 0)} 件")

    # SOP
    sop = data.get("sop", [])
    if sop:
        lines.append("\n### SOP")
        for s in sop:
            lines.append(f"  {s['sop_id']} ({s['version']}): {s['title']} [{s['status']}]")

    # 工艺卡
    card = data.get("process_card", {})
    if card:
        lines.append("\n### 工艺卡")
        lines.append(f"  工艺: {' -> '.join(card.get('processes', []))}")
        params = card.get("parameters", {})
        if params:
            lines.append(f"  参数: {', '.join(f'{k}={v}' for k, v in params.items())}")

    # 结论
    status_text = "通过" if data.get("overall_status") == "通过" else "不通过"
    lines.append(f"\n### 综合判定: {status_text}")
    if data.get("issues"):
        lines.append("\n需解决问题:")
        for issue in data["issues"]:
            lines.append(f"  - {issue}")

    return "\n".join(lines)
