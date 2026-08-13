"""A2A 跨 Agent 通信 — 协议 + HTTP 客户端 + 注册表"""
from app.a2a.client import A2AClient, A2AError
from app.a2a.protocol import (
    AgentCard,
    AgentSkill,
    JSONRPCRequest,
    JSONRPCResponse,
    Task,
    TaskStatus,
    build_user_message,
    send_task_params,
)
from app.a2a.registry import a2a_registry

__all__ = [
    "A2AClient",
    "A2AError",
    "AgentCard",
    "AgentSkill",
    "JSONRPCRequest",
    "JSONRPCResponse",
    "Task",
    "TaskStatus",
    "build_user_message",
    "send_task_params",
    "a2a_registry",
]
