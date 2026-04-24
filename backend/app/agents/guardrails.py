"""Guardrails 安全护栏 — 输入过滤 + 输出验证"""
import re
from typing import Optional, Tuple
from app.core.logger import log
from app.agents.settings import GUARDRAILS


def check_input(message: str) -> Tuple[bool, Optional[str]]:
    """检查用户输入是否合规"""
    if not message or not message.strip():
        return False, "输入不能为空"

    if len(message) > GUARDRAILS["max_input_length"]:
        return False, f"输入过长（{len(message)} 字符），最大支持 {GUARDRAILS['max_input_length']} 字符"

    if len(message.strip()) < GUARDRAILS["min_input_length"]:
        return False, "输入过短"

    text_lower = message.lower()
    for pattern in GUARDRAILS["sensitive_patterns"]:
        if re.search(pattern, text_lower, re.IGNORECASE):
            log.warning(f"[Guardrails] 输入命中敏感模式: {pattern[:30]}")
            return False, "输入包含不安全的操作指令"

    return True, None


def sanitize_input(message: str) -> str:
    """清理输入：去除首尾空白、控制字符"""
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', message)
    return cleaned.strip()


def check_output(response: str) -> Tuple[bool, Optional[str]]:
    """检查 AI 输出是否合规"""
    if not response or not response.strip():
        return False, "响应为空"

    if len(response) > GUARDRAILS["max_output_length"]:
        return False, "响应过长"

    return True, None
