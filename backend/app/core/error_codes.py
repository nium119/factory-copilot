"""结构化错误码 — SSE 事件和 API 响应的统一错误编码。

错误码格式: 领域_具体原因（如 TOOL_NEO4J_UNAVAILABLE）
每个错误码映射到中文用户消息和 HTTP 级别的严重程度。
"""

from enum import Enum
from typing import Optional


class ErrorSeverity(str, Enum):
    INFO = "info"        # 非阻塞通知
    WARN = "warn"        # 降级但可用
    ERROR = "error"      # 请求失败
    FATAL = "fatal"      # 系统级故障


class ErrorDomain(str, Enum):
    INPUT = "INPUT"            # 用户输入校验
    ROUTE = "ROUTE"            # 意图路由 / Agent 选择
    TOOL = "TOOL"              # 工具执行
    NEO4J = "NEO4J"            # Neo4j / 数据库
    LLM = "LLM"                # LLM 调用
    OUTPUT = "OUTPUT"          # 输出校验 / 安全护栏
    SYSTEM = "SYSTEM"          # 基础设施 / 配置


class ErrorCode(str, Enum):
    # ── INPUT ──
    INPUT_EMPTY = "INPUT_EMPTY"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    INPUT_SENSITIVE = "INPUT_SENSITIVE"

    # ── ROUTE ──
    ROUTE_NO_MATCH = "ROUTE_NO_MATCH"
    ROUTE_AMBIGUOUS = "ROUTE_AMBIGUOUS"
    ROUTE_AGENT_UNAVAILABLE = "ROUTE_AGENT_UNAVAILABLE"

    # ── TOOL ──
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_PARAM_MISSING = "TOOL_PARAM_MISSING"
    TOOL_PARAM_INVALID = "TOOL_PARAM_INVALID"
    TOOL_EXEC_FAILED = "TOOL_EXEC_FAILED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_REQUIRES_CONFIRM = "TOOL_REQUIRES_CONFIRM"
    TOOL_CONFIRM_DENIED = "TOOL_CONFIRM_DENIED"
    TOOL_CONFIRM_TIMEOUT = "TOOL_CONFIRM_TIMEOUT"

    # ── NEO4J ──
    NEO4J_UNAVAILABLE = "NEO4J_UNAVAILABLE"
    NEO4J_QUERY_FAILED = "NEO4J_QUERY_FAILED"
    NEO4J_CYPHER_INVALID = "NEO4J_CYPHER_INVALID"
    NEO4J_TIMEOUT = "NEO4J_TIMEOUT"

    # ── LLM ──
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_RESPONSE_INVALID = "LLM_RESPONSE_INVALID"
    LLM_TIMEOUT = "LLM_TIMEOUT"

    # ── OUTPUT ──
    OUTPUT_EMPTY = "OUTPUT_EMPTY"
    OUTPUT_TOO_LONG = "OUTPUT_TOO_LONG"
    OUTPUT_GUARDRAIL_REJECTED = "OUTPUT_GUARDRAIL_REJECTED"

    # ── SYSTEM ──
    SYSTEM_INTERNAL = "SYSTEM_INTERNAL"
    SYSTEM_OVERLOADED = "SYSTEM_OVERLOADED"
    SYSTEM_CONFIG_ERROR = "SYSTEM_CONFIG_ERROR"


# ── 元数据映射 ──

ERROR_META: dict[ErrorCode, dict] = {
    ErrorCode.INPUT_EMPTY:              {"severity": ErrorSeverity.WARN,  "message": "输入不能为空", "http_status": 400},
    ErrorCode.INPUT_TOO_LONG:           {"severity": ErrorSeverity.WARN,  "message": "输入内容过长", "http_status": 400},
    ErrorCode.INPUT_SENSITIVE:          {"severity": ErrorSeverity.WARN,  "message": "输入包含敏感内容", "http_status": 400},

    ErrorCode.ROUTE_NO_MATCH:           {"severity": ErrorSeverity.INFO,  "message": "未找到匹配的处理流程", "http_status": 200},
    ErrorCode.ROUTE_AMBIGUOUS:          {"severity": ErrorSeverity.WARN,  "message": "匹配到多个处理流程", "http_status": 200},
    ErrorCode.ROUTE_AGENT_UNAVAILABLE:  {"severity": ErrorSeverity.ERROR, "message": "对应 Agent 不可用", "http_status": 503},

    ErrorCode.TOOL_NOT_FOUND:           {"severity": ErrorSeverity.ERROR, "message": "工具未实现", "http_status": 501},
    ErrorCode.TOOL_PARAM_MISSING:       {"severity": ErrorSeverity.WARN,  "message": "缺少必要参数", "http_status": 400},
    ErrorCode.TOOL_PARAM_INVALID:       {"severity": ErrorSeverity.WARN,  "message": "参数格式不合法", "http_status": 400},
    ErrorCode.TOOL_EXEC_FAILED:         {"severity": ErrorSeverity.ERROR, "message": "工具执行失败", "http_status": 500},
    ErrorCode.TOOL_TIMEOUT:             {"severity": ErrorSeverity.ERROR, "message": "工具执行超时", "http_status": 504},
    ErrorCode.TOOL_REQUIRES_CONFIRM:    {"severity": ErrorSeverity.INFO,  "message": "操作需要人工确认", "http_status": 200},
    ErrorCode.TOOL_CONFIRM_DENIED:      {"severity": ErrorSeverity.INFO,  "message": "操作已被取消", "http_status": 200},
    ErrorCode.TOOL_CONFIRM_TIMEOUT:     {"severity": ErrorSeverity.WARN,  "message": "确认超时，操作已取消", "http_status": 408},

    ErrorCode.NEO4J_UNAVAILABLE:        {"severity": ErrorSeverity.ERROR, "message": "数据库连接不可用", "http_status": 503},
    ErrorCode.NEO4J_QUERY_FAILED:       {"severity": ErrorSeverity.ERROR, "message": "数据查询失败", "http_status": 500},
    ErrorCode.NEO4J_CYPHER_INVALID:     {"severity": ErrorSeverity.ERROR, "message": "查询语法错误", "http_status": 500},
    ErrorCode.NEO4J_TIMEOUT:            {"severity": ErrorSeverity.ERROR, "message": "数据库查询超时", "http_status": 504},

    ErrorCode.LLM_UNAVAILABLE:          {"severity": ErrorSeverity.ERROR, "message": "AI 模型不可用", "http_status": 503},
    ErrorCode.LLM_RATE_LIMITED:         {"severity": ErrorSeverity.ERROR, "message": "AI 请求频率过高，请稍候", "http_status": 429},
    ErrorCode.LLM_RESPONSE_INVALID:     {"severity": ErrorSeverity.ERROR, "message": "AI 响应格式异常", "http_status": 500},
    ErrorCode.LLM_TIMEOUT:              {"severity": ErrorSeverity.ERROR, "message": "AI 响应超时", "http_status": 504},

    ErrorCode.OUTPUT_EMPTY:             {"severity": ErrorSeverity.WARN,  "message": "AI 响应为空", "http_status": 200},
    ErrorCode.OUTPUT_TOO_LONG:          {"severity": ErrorSeverity.WARN,  "message": "AI 响应过长", "http_status": 200},
    ErrorCode.OUTPUT_GUARDRAIL_REJECTED: {"severity": ErrorSeverity.WARN, "message": "响应未通过安全检查", "http_status": 200},

    ErrorCode.SYSTEM_INTERNAL:          {"severity": ErrorSeverity.FATAL, "message": "系统内部错误", "http_status": 500},
    ErrorCode.SYSTEM_OVERLOADED:        {"severity": ErrorSeverity.ERROR, "message": "系统繁忙，请稍候", "http_status": 503},
    ErrorCode.SYSTEM_CONFIG_ERROR:      {"severity": ErrorSeverity.FATAL, "message": "系统配置异常", "http_status": 500},
}


def get_error_meta(code: ErrorCode) -> dict:
    return ERROR_META.get(code, {
        "severity": ErrorSeverity.ERROR,
        "message": "未知错误",
        "http_status": 500,
    })


def sse_error(code: ErrorCode, detail: str = "", **extra) -> dict:
    """构造 SSE 事件用的错误载荷。

    返回可直接 JSON 序列化的字典。
    """
    meta = get_error_meta(code)
    payload = {
        "code": code.value,
        "severity": meta["severity"].value,
        "message": meta["message"],
        "detail": detail or meta["message"],
    }
    payload.update(extra)
    return payload


def classify_exception(e: Exception) -> ErrorCode:
    """将 Python 异常映射到最接近的 ErrorCode。"""
    cls = type(e).__name__
    msg = str(e).lower()

    if "timeout" in cls.lower() or "timedout" in msg:
        return ErrorCode.TOOL_TIMEOUT
    if "connection" in msg or "unavailable" in msg or "refused" in msg:
        return ErrorCode.NEO4J_UNAVAILABLE
    if "rate" in msg and "limit" in msg:
        return ErrorCode.LLM_RATE_LIMITED
    if "neo4j" in msg or "cypher" in msg:
        if "syntax" in msg:
            return ErrorCode.NEO4J_CYPHER_INVALID
        return ErrorCode.NEO4J_QUERY_FAILED
    return ErrorCode.TOOL_EXEC_FAILED
