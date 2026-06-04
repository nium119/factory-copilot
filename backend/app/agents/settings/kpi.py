"""制造 KPI 目标注册表 + 状态判定函数

KPI 定义从 config/kpi.yaml 加载。
"""

from app.core.config_loader import load_yaml

MANUFACTURING_KPIS = load_yaml("kpi")


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
