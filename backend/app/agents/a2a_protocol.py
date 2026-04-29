"""A2A (Agent-to-Agent) 消息协议定义 — 对接外部 Agent 的基础设施"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import uuid


class A2AMessageType(str, Enum):
    DELEGATE = "delegate"       # 委托：A 将子任务委托给 B
    QUERY = "query"             # 查询：A 向 B 查询信息
    BROADCAST = "broadcast"     # 广播：A 向所有 Agent 广播消息
    RESPONSE = "response"       # 响应：B 返回结果给 A


@dataclass
class A2AMessage:
    """A2A 消息"""
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    msg_type: A2AMessageType = A2AMessageType.DELEGATE
    from_agent: str = ""           # 发起方 agent name
    to_agent: str = ""             # 目标方 agent name（broadcast 时为空）
    content: str = ""              # 消息内容/委托任务描述
    context: Dict[str, Any] = field(default_factory=dict)  # 附加上下文
    correlation_id: str = ""       # 关联 ID，用于追踪委托链

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "msg_type": self.msg_type.value,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "content": self.content,
            "context": self.context,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "A2AMessage":
        return cls(
            msg_id=data.get("msg_id", str(uuid.uuid4())),
            msg_type=A2AMessageType(data.get("msg_type", "delegate")),
            from_agent=data.get("from_agent", ""),
            to_agent=data.get("to_agent", ""),
            content=data.get("content", ""),
            context=data.get("context", {}),
            correlation_id=data.get("correlation_id", ""),
        )


@dataclass
class A2ADelegation:
    """委托记录 — 追踪一次委托的完整生命周期"""
    delegation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    from_agent: str = ""
    to_agent: str = ""
    task: str = ""
    status: str = "pending"        # pending / running / success / error / timeout
    result: Optional[str] = None
    elapsed_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "delegation_id": self.delegation_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "task": self.task,
            "status": self.status,
            "result": self.result,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }
