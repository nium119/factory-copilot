"""Guardrails 安全护栏 — 输入过滤 + 输出验证 + 工具调用安全 + 审计日志"""
import json
import re
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from app.agents.settings import GUARDRAILS
from app.core.logger import log

# ─── 输入 / 输出护栏 ───

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


def check_output(response: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """检查 AI 输出是否合规。
    Returns (is_valid, reject_reason, error_code)
    """
    if not response or not response.strip():
        return False, "响应为空", "empty"

    if len(response) > GUARDRAILS["max_output_length"]:
        return False, f"响应过长（{len(response)} 字符）", "too_long"

    return True, None, None


# ─── 工具输出脱敏 ───

def sanitize_tool_output(result: Any) -> Any:
    """递归清理工具输出中的控制字符，防止注入 LLM 上下文"""
    if isinstance(result, str):
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', result)
    elif isinstance(result, dict):
        return {k: sanitize_tool_output(v) for k, v in result.items()}
    elif isinstance(result, list):
        return [sanitize_tool_output(item) for item in result]
    return result


# ─── 审计日志 ───

def _summarize_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏参数摘要：截断值到 100 字符"""
    out = {}
    for k, v in params.items():
        s = str(v)
        out[k] = s[:100] + "..." if len(s) > 100 else s
    return out


class AuditLogger:
    """结构化审计日志，写入 logs/audit.log"""

    _initialized = False

    @classmethod
    def _ensure_dir(cls):
        if cls._initialized:
            return
        import os

        from app.agents.settings import AUDIT_CONFIG
        log_dir = os.path.dirname(AUDIT_CONFIG["log_file"])
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        cls._initialized = True

    @classmethod
    def log(
        cls,
        tool_name: str,
        action_name: str,
        risk: str,
        agent: str,
        params: Dict[str, Any],
        result_preview: str,
        success: bool,
        session_id: str = "unknown",
    ):
        """写入一条结构化的审计记录"""
        from app.agents.settings import AUDIT_CONFIG
        if not AUDIT_CONFIG["enabled"]:
            return

        cls._ensure_dir()

        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "action": action_name,
            "risk": risk,
            "agent": agent,
            "session_id": session_id,
            "success": success,
            "result_preview": str(result_preview)[:200] if result_preview else "(empty)",
            "params_summary": _summarize_params(params) if not AUDIT_CONFIG.get("log_full_args") else params,
        }

        log_path = AUDIT_CONFIG["log_file"]
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            log.error(f"[AuditLogger] 写入审计日志失败: {e}")

        log.info(f"[AUDIT] {tool_name} | {action_name} | {risk} | success={success}")


# ─── 工具调用安全包装器 ───

def _build_params_dict(args: tuple, kwargs: dict) -> Dict[str, Any]:
    """从 (args, kwargs) 构建可审计的参数字典"""
    d = dict(kwargs)
    for i, v in enumerate(args):
        d[f"arg{i}"] = v
    return d


async def safe_tool_call(
    tool_name: str,
    tool_fn: Callable,
    *args,
    session_id: str = "default",
    **kwargs,
) -> Any:
    """工具调用安全包装器

    流程：分类查询 → [审批] → 执行 → [审计] → 脱敏 → 返回

    返回：
        - READ: 直接返回工具结果
        - WRITE_AUDIT: 执行 + 审计日志 + 返回结果
        - WRITE_APPROVE / CRITICAL: 返回 requires_approval dict
    """
    from app.agents.settings import AUDIT_CONFIG, TOOL_SAFETY

    classification = TOOL_SAFETY.get(tool_name)
    if not classification:
        log.warning(f"[Guardrails] 未注册的工具调用: {tool_name}，直接通过")
        return await tool_fn(*args, **kwargs)

    risk = classification["risk"]

    # 1. 审批拦截：WRITE_APPROVE 和 CRITICAL
    if risk in ("WRITE_APPROVE", "CRITICAL"):
        from app.agents.approval import ApprovalManager

        action_key = classification["action_key"]
        params = _build_params_dict(args, kwargs)
        # 对已自带 skip_approval 的函数直接放行
        if kwargs.get("skip_approval"):
            pass  # 走正常执行路径
        else:
            approval = ApprovalManager.create_approval_request(
                action=action_key,
                description=f"{classification['action_name']}: {_summarize_params(params)}",
                details=params,
            )
            if approval:
                log.info(f"[Guardrails] {tool_name} 需审批: {approval['approval_id']}")
                return {
                    "requires_approval": True,
                    "approval_id": approval["approval_id"],
                    "message": f"{classification['action_name']}需要审批确认 (ID: {approval['approval_id']})",
                }

    # 2. 执行工具
    try:
        result = await tool_fn(*args, **kwargs)
        success = True
        result_preview = str(result)[:200] if result else "(empty)"
    except Exception as e:
        success = False
        result_preview = str(e)[:200]
        log.error(f"[Guardrails] 工具 {tool_name} 执行失败: {e}")
        raise

    # 3. 审计日志（WRITE_AUDIT / WRITE_APPROVE / CRITICAL）
    if risk in ("WRITE_AUDIT", "WRITE_APPROVE", "CRITICAL") and AUDIT_CONFIG["enabled"]:
        params = _build_params_dict(args, kwargs)
        AuditLogger.log(
            tool_name=tool_name,
            action_name=classification.get("action_name", tool_name),
            risk=risk,
            agent=classification.get("agent", "unknown"),
            params=params,
            result_preview=result_preview,
            success=success,
            session_id=session_id,
        )

    # 4. 脱敏输出
    result = sanitize_tool_output(result)

    return result
