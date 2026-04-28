"""安灯(Andon)工具 — 模拟数据 + MES CLI 接入
异常呼叫、停线处理、问题上报、响应跟踪
"""
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from app.core.logger import log
from app.agents.tools.mes_cli_runner import cli_or_mock

MES_API_ENABLED = os.getenv("MES_API_ENABLED", "false").lower() == "true"

# ─── 模拟数据 ───
MOCK_ACTIVE_ANDONS = [
    {"andon_id": "AN-2026-042", "type": "设备", "line": "SMT-01", "description": "贴片机吸嘴堵塞，频繁报警", "status": "处理中", "created_at": "2026-04-23 09:15", "responder": "张工(设备)", "level": "线长"},
    {"andon_id": "AN-2026-043", "type": "质量", "line": "DIP-01", "description": "波峰焊连锡不良率超标(>3%)", "status": "待响应", "created_at": "2026-04-23 10:30", "responder": None, "level": "线长"},
    {"andon_id": "AN-2026-044", "type": "物料", "line": "组装-01", "description": "外壳组件A型库存不足，无法继续生产", "status": "待响应", "created_at": "2026-04-23 11:00", "responder": None, "level": "线长"},
]

MOCK_ANDON_HISTORY = [
    {"andon_id": "AN-2026-040", "type": "设备", "line": "SMT-02", "description": "回流焊温度异常", "status": "已关闭", "created_at": "2026-04-22 14:20", "resolved_at": "2026-04-22 15:05", "response_time": "5min", "resolve_time": "45min"},
    {"andon_id": "AN-2026-041", "type": "质量", "line": "组装-02", "description": "成品测试不良率偏高", "status": "已关闭", "created_at": "2026-04-22 16:00", "resolved_at": "2026-04-22 17:30", "response_time": "3min", "resolve_time": "90min"},
]

MOCK_ANDON_STATS = {
    "today_total": 8,
    "today_resolved": 5,
    "avg_response_time": "4.2min",
    "avg_resolve_time": "52min",
    "by_type": {"设备": 3, "质量": 2, "物料": 2, "工艺": 1},
    "by_line": {"SMT-01": 3, "SMT-02": 1, "DIP-01": 2, "组装-01": 1, "组装-02": 1},
}

MOCK_LINE_STOP_RECORDS = [
    {"line": "SMT-01", "reason": "设备故障", "start_time": "2026-04-22 08:30", "end_time": "2026-04-22 09:15", "duration": "45min", "impact": "影响产出约 60 件"},
]


async def create_andon_alert(alert_type: str, description: str, line: Optional[str] = None, severity: Optional[str] = None) -> Dict[str, Any]:
    """创建安灯报警"""
    log.info(f"[安灯] 创建报警: 类型={alert_type}, 产线={line}, 严重度={severity}, 描述={description}")
    if MES_API_ENABLED:
        cmd = ["andon", "create", "--type", alert_type, "--desc", description]
        if line:
            cmd.extend(["--line", line])
        if severity:
            cmd.extend(["--severity", severity])
        return cli_or_mock(cmd, {}, True)
    new_id = f"AN-2026-{len(MOCK_ACTIVE_ANDONS) + 50:03d}"
    alert = {
        "andon_id": new_id,
        "type": alert_type,
        "line": line or "未指定",
        "description": description,
        "status": "待响应",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "responder": None,
        "level": "线长",
    }
    MOCK_ACTIVE_ANDONS.insert(0, alert)
    return alert


async def query_active_andons(line: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询活跃安灯"""
    log.info(f"[安灯] 查询活跃安灯, 产线: {line}")
    cmd = ["andon", "active"]
    if line:
        cmd.extend(["--line", line])
    result = cli_or_mock(cmd, MOCK_ACTIVE_ANDONS, MES_API_ENABLED)
    if isinstance(result, list):
        if line:
            return [a for a in result if line.lower() in a.get("line", "").lower()]
        return result
    if line:
        return [a for a in MOCK_ACTIVE_ANDONS if line.lower() in a["line"].lower()]
    return MOCK_ACTIVE_ANDONS


async def query_andon_history(hours: int = 24) -> List[Dict[str, Any]]:
    """查询安灯历史"""
    log.info(f"[安灯] 查询历史, 最近 {hours} 小时")
    if MES_API_ENABLED:
        return cli_or_mock(["andon", "history", "--hours", str(hours)], MOCK_ANDON_HISTORY[:10], True)
    return MOCK_ANDON_HISTORY[:10]


async def escalate_andon(andon_id: str, level: str = "manager", skip_approval: bool = False) -> Dict[str, Any]:
    """升级安灯处理（需要审批）"""
    log.info(f"[安灯] 升级处理: {andon_id} -> {level}")
    # 创建审批请求
    if not skip_approval:
        from app.agents.approval import ApprovalManager
        approval = ApprovalManager.create_approval_request(
            action="andon_escalate",
            description=f"将安灯 {andon_id} 升级到 {level}",
            details={"andon_id": andon_id, "level": level},
        )
        if approval:
            return {
                "requires_approval": True,
                "approval_id": approval["approval_id"],
                "message": f"该操作需要审批。审批 ID: {approval['approval_id']}",
            }
    # 直接执行（如果不需要审批或已审批）
    for a in MOCK_ACTIVE_ANDONS:
        if a["andon_id"] == andon_id:
            level_map = {"manager": "生产经理", "director": "生产总监", "vp": "生产副总"}
            a["level"] = level_map.get(level, level)
            a["status"] = "已升级"
            return {"andon_id": andon_id, "new_level": a["level"], "status": "已升级"}
    return {"error": f"安灯 {andon_id} 不存在"}


async def get_andon_stats() -> Dict[str, Any]:
    """安灯统计"""
    log.info("[安灯] 查询统计")
    return cli_or_mock(["andon", "stats"], MOCK_ANDON_STATS, MES_API_ENABLED)


async def handle_line_stop(line: str, reason: str, skip_approval: bool = False) -> Dict[str, Any]:
    """停线处理（需要审批）"""
    log.info(f"[安灯] 停线处理: 产线={line}, 原因={reason}")
    # 创建审批请求
    if not skip_approval:
        from app.agents.approval import ApprovalManager
        approval = ApprovalManager.create_approval_request(
            action="andon_stop_line",
            description=f"停线: {line} - {reason}",
            details={"line": line, "reason": reason},
        )
        if approval:
            return {
                "requires_approval": True,
                "approval_id": approval["approval_id"],
                "message": f"停线操作需要审批。审批 ID: {approval['approval_id']}",
            }
    record = {
        "line": line,
        "reason": reason,
        "start_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "end_time": None,
        "status": "停线中",
    }
    MOCK_LINE_STOP_RECORDS.append(record)
    return record


def format_andon_report(andons: List[Dict[str, Any]]) -> str:
    """格式化安灯报告"""
    if not andons:
        return "当前无活跃安灯报警。"
    lines = ["## 安灯报警列表\n"]
    lines.append("| ID | 类型 | 产线 | 描述 | 状态 | 响应人 | 级别 |")
    lines.append("|------|------|------|------|------|------|------|")
    for a in andons:
        lines.append(f"| {a['andon_id']} | {a['type']} | {a['line']} | {a['description']} | {a['status']} | {a['responder'] or '未分配'} | {a['level']} |")
    return "\n".join(lines)


def format_stats_report(stats: Dict[str, Any]) -> str:
    """格式化安灯统计报告"""
    lines = ["## 安灯统计\n"]
    lines.append(f"**今日总数**: {stats['today_total']} | **已解决**: {stats['today_resolved']}")
    lines.append(f"**平均响应时间**: {stats['avg_response_time']} | **平均解决时间**: {stats['avg_resolve_time']}")
    lines.append("\n**按类型分布**:")
    for t, c in stats.get("by_type", {}).items():
        lines.append(f"  {t}: {c}")
    lines.append("\n**按产线分布**:")
    for l, c in stats.get("by_line", {}).items():
        lines.append(f"  {l}: {c}")
    return "\n".join(lines)
