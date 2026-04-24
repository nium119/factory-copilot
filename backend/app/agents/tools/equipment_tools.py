"""设备工具 — 模拟数据 + 预留 MES API 接入"""
from typing import Dict, Any, Optional, List
from app.core.logger import log

MES_API_BASE = "http://localhost:9090"
MES_API_ENABLED = False

MOCK_EQUIPMENT = [
    {"name": "贴片机-01", "type": "SMT", "status": "运行中", "oee": 92.5, "uptime": "120h", "next_maintenance": "2026-04-25", "fault_count": 0},
    {"name": "贴片机-02", "type": "SMT", "status": "运行中", "oee": 88.3, "uptime": "85h", "next_maintenance": "2026-04-23", "fault_count": 1},
    {"name": "回流焊-01", "type": "焊接", "status": "运行中", "oee": 95.1, "uptime": "200h", "next_maintenance": "2026-04-30", "fault_count": 0},
    {"name": "AOI-01", "type": "检测", "status": "维护中", "oee": 0, "uptime": "-", "next_maintenance": "2026-04-22", "fault_count": 2},
    {"name": "波峰焊-01", "type": "焊接", "status": "停机", "oee": 0, "uptime": "-", "next_maintenance": "2026-04-24", "fault_count": 3},
]

MOCK_EQUIPMENT_SUMMARY = {
    "total": 5,
    "running": 3,
    "maintenance": 1,
    "stopped": 1,
    "avg_oee": 55.2,  # 含停机设备
    "active_avg_oee": 92.0,
}


async def query_equipment(name: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询设备状态"""
    log.info(f"[设备工具] 查询设备状态, 设备: {name}")
    if name:
        return [e for e in MOCK_EQUIPMENT if name.lower() in e["name"].lower() or name.lower() in e["type"].lower()]
    return MOCK_EQUIPMENT


async def query_equipment_summary() -> Dict[str, Any]:
    """查询设备概况"""
    return MOCK_EQUIPMENT_SUMMARY


async def diagnose_fault(equipment_name: str = "") -> str:
    """故障诊断建议"""
    log.info(f"[设备工具] 故障诊断, 设备: {equipment_name}")
    lines = ["## 设备故障诊断建议\n"]
    for e in MOCK_EQUIPMENT:
        if e["fault_count"] > 0 or e["status"] in ("维护中", "停机"):
            lines.append(f"**{e['name']}** (状态: {e['status']})")
            if e["status"] == "停机":
                lines.append(f"  - 故障次数: {e['fault_count']} 次")
                lines.append(f"  - 建议: 检查波峰焊喷嘴堵塞情况，清理助焊剂残留，校准链条张力")
            elif e["status"] == "维护中":
                lines.append(f"  - 计划维护中，预计今日完成")
            lines.append("")
    if not lines[1:]:
        lines.append("所有设备运行正常，无故障报告。")
    return "\n".join(lines)


def format_equipment(equipments: List[Dict[str, Any]]) -> str:
    """格式化设备数据为文本"""
    if not equipments:
        return "无设备信息。"
    lines = ["## 设备状态\n"]
    lines.append("| 设备 | 类型 | 状态 | OEE | 下次保养 | 故障次数 |")
    lines.append("|------|------|------|-----|----------|----------|")
    for e in equipments:
        oee_str = f"{e['oee']}%" if e['oee'] > 0 else "-"
        lines.append(f"| {e['name']} | {e['type']} | {e['status']} | {oee_str} | {e['next_maintenance']} | {e['fault_count']} |")
    s = MOCK_EQUIPMENT_SUMMARY
    lines.append(f"\n**概况**: 共 {s['total']} 台设备，运行中 {s['running']} 台，维护中 {s['maintenance']} 台，停机 {s['stopped']} 台")
    return "\n".join(lines)
