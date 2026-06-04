"""轻量实体提取器 — 从用户消息中提取关键参数
用于将用户意图中的实体（产线、产品、工单号等）传递给工具函数
"""
import re
from typing import Dict, Optional

# 产线名模式
LINE_PATTERNS = [
    r'(SMT-\d+)',
    r'(DIP-\d+)',
    r'(组装-\d+)',
    r'(产线[一二三四\d]+)',
]

# 工单号模式
WO_PATTERNS = [
    r'(WO[-\s]?\d{4}[-\s]?\d+)',
    r'(工单[-\s]?\d+)',
    r'(生产工单[-\s]?\d+)',
]

# 产品名模式（引号内或"产品X"模式）
PRODUCT_PATTERNS = [
    r'["“”]([^"“”]{1,20})["“”]',
    r'产品([A-Z]?[一-龥\w]{1,10})',
]

# 安灯类型映射
ANDON_TYPE_MAP = {
    "物料": "物料", "设备": "设备", "质量": "质量", "工艺": "工艺",
    "缺料": "物料", "故障": "设备", "不良": "质量", "异常": "设备",
}

# 紧急度映射
URGENCY_MAP = {
    "紧急": "urgent", "急": "urgent", "高": "high", "紧急优先": "urgent",
    "高优": "high", "高优先级": "high",
    "正常": "normal", "普通": "normal", "低": "low",
}


def _match_patterns(text: str, patterns: list) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return None


async def extract_entities(message: str, domain: str = "analysis_monitor") -> Dict[str, str]:
    """
    从用户消息中提取实体参数

    Args:
        message: 用户消息
        domain: 所属域 (production_execution/production_management/quality_equipment/analysis_monitor)

    Returns:
        实体字典，如 {"line": "SMT-01", "product": "主板A"}
    """
    entities: Dict[str, str] = {}

    # 通用提取：产线
    line = _match_patterns(message, LINE_PATTERNS)
    if line:
        entities["line"] = line

    # 通用提取：工单号
    wo = _match_patterns(message, WO_PATTERNS)
    if wo:
        entities["work_order"] = wo

    # 通用提取：产品
    product = _match_patterns(message, PRODUCT_PATTERNS)
    if product:
        entities["product"] = product

    # 域特定提取
    if domain == "production_management":
        urgency = _extract_urgency(message)
        if urgency:
            entities["urgency"] = urgency

    elif domain == "production_execution":
        alert_type = _extract_andon_type(message)
        if alert_type:
            entities["alert_type"] = alert_type
        process = _extract_process(message)
        if process:
            entities["process"] = process

    return entities


def _extract_urgency(message: str) -> Optional[str]:
    for keyword, value in URGENCY_MAP.items():
        if keyword in message:
            return value
    return "normal"


def _extract_andon_type(message: str) -> Optional[str]:
    for keyword, atype in ANDON_TYPE_MAP.items():
        if keyword in message:
            return atype
    return None


def _extract_process(message: str) -> Optional[str]:
    for p in ["SMT贴片", "DIP插件", "组装", "SMT", "DIP", "贴片", "插件", "波峰焊", "回流焊", "测试", "包装"]:
        if p in message:
            return p
    return None
