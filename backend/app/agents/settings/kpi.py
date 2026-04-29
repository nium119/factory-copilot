"""制造 KPI 目标注册表 + 状态判定函数"""

MANUFACTURING_KPIS = {
    # ── 设备领域 ──
    "oee": {
        "name": "OEE 设备综合效率",
        "target": 85.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 75.0, "critical_threshold": 65.0,
        "domain": "equipment",
    },
    "equipment_uptime": {
        "name": "设备开机率",
        "target": 95.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 90.0, "critical_threshold": 85.0,
        "domain": "equipment",
    },
    "mtbf": {
        "name": "平均故障间隔 (MTBF)",
        "target": 200.0, "unit": "小时", "direction": "higher_better",
        "warning_threshold": 120.0, "critical_threshold": 80.0,
        "domain": "equipment",
    },
    "mttr": {
        "name": "平均修复时间 (MTTR)",
        "target": 30.0, "unit": "分钟", "direction": "lower_better",
        "warning_threshold": 60.0, "critical_threshold": 90.0,
        "domain": "equipment",
    },

    # ── 质量领域 ──
    "yield_rate": {
        "name": "一次合格率",
        "target": 98.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 96.0, "critical_threshold": 94.0,
        "domain": "quality",
    },
    "defect_rate": {
        "name": "不良率",
        "target": 2.0, "unit": "%", "direction": "lower_better",
        "warning_threshold": 5.0, "critical_threshold": 8.0,
        "domain": "quality",
    },
    "cpk": {
        "name": "过程能力指数 (Cpk)",
        "target": 1.33, "unit": "", "direction": "higher_better",
        "warning_threshold": 1.0, "critical_threshold": 0.67,
        "domain": "quality",
    },

    # ── 排产领域 ──
    "delivery_rate": {
        "name": "交期达成率",
        "target": 95.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 90.0, "critical_threshold": 85.0,
        "domain": "scheduling",
    },
    "balance_rate": {
        "name": "产线平衡率",
        "target": 85.0, "unit": "%", "direction": "higher_better",
        "warning_threshold": 75.0, "critical_threshold": 65.0,
        "domain": "scheduling",
    },
    "changeover_time": {
        "name": "平均换线时间",
        "target": 30.0, "unit": "分钟", "direction": "lower_better",
        "warning_threshold": 45.0, "critical_threshold": 60.0,
        "domain": "scheduling",
    },

    # ── 库存领域 ──
    "inventory_turnover": {
        "name": "库存周转率",
        "target": 12.0, "unit": "次/月", "direction": "higher_better",
        "warning_threshold": 8.0, "critical_threshold": 5.0,
        "domain": "inventory",
    },
    "shortage_rate": {
        "name": "缺料率",
        "target": 0.5, "unit": "%", "direction": "lower_better",
        "warning_threshold": 2.0, "critical_threshold": 5.0,
        "domain": "inventory",
    },

    # ── 安灯领域 ──
    "andon_response_time": {
        "name": "安灯平均响应时间",
        "target": 5.0, "unit": "分钟", "direction": "lower_better",
        "warning_threshold": 10.0, "critical_threshold": 15.0,
        "domain": "andon",
    },
    "andon_resolve_time": {
        "name": "安灯平均解决时间",
        "target": 30.0, "unit": "分钟", "direction": "lower_better",
        "warning_threshold": 60.0, "critical_threshold": 90.0,
        "domain": "andon",
    },

    # ── 生产领域 ──
    "production_output": {
        "name": "产线日产出",
        "target": 1000.0, "unit": "件/天", "direction": "higher_better",
        "warning_threshold": 850.0, "critical_threshold": 700.0,
        "domain": "production",
    },
}


def get_kpi_status(kpi_key: str, actual_value: float) -> str:
    """根据 KPI 实际值返回状态：on_track / warning / critical"""
    kpi = MANUFACTURING_KPIS.get(kpi_key)
    if not kpi:
        return "unknown"
    direction = kpi["direction"]
    warn = kpi["warning_threshold"]
    crit = kpi["critical_threshold"]
    target = kpi["target"]

    if direction == "higher_better":
        if actual_value >= target:
            return "on_track"
        elif actual_value >= warn:
            return "warning"
        elif actual_value >= crit:
            return "critical"
        else:
            return "critical"
    else:  # lower_better
        if actual_value <= target:
            return "on_track"
        elif actual_value <= warn:
            return "warning"
        elif actual_value <= crit:
            return "critical"
        else:
            return "critical"
