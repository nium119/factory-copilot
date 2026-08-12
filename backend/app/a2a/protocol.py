"""A2A 协议定义 — 对齐 2026 工业界 A2A 标准（Agent Card + Task 生命周期 + JSON-RPC 2.0）

仅实现 FC 需要的核心子集：
- Agent Card 发现：GET /.well-known/agent-card.json
- Task 生命周期：submitted / working / input-required / completed / failed / canceled
- 核心操作：tasks/send、tasks/get、tasks/cancel、tasks/sendSubscribe（SSE 流式）
传输为 JSON-RPC 2.0 over HTTP/SSE。
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ─────────────────── Agent Card ───────────────────

class AgentSkill(BaseModel):
    """Agent Card 中的能力项"""
    id: str
    name: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    inputModes: List[str] = Field(default_factory=list)
    outputModes: List[str] = Field(default_factory=list)
    examples: List[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    """对齐 A2A 标准 Agent Card（能力清单）"""
    name: str
    description: str = ""
    url: str = ""
    version: str = "1.0.0"
    skills: List[AgentSkill] = Field(default_factory=list)
    endpoints: Dict[str, str] = Field(
        default_factory=dict,
        description="任务端点映射，如 {\"tasks/send\": \"/tasks/send\"}",
    )


# ─────────────────── Task ───────────────────

class TaskStatus(str, Enum):
    """A2A Task 生命周期状态"""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class Task(BaseModel):
    """A2A Task 对象"""
    id: str
    sessionId: str = ""
    status: TaskStatus = TaskStatus.SUBMITTED
    message: Dict[str, Any] = Field(default_factory=dict)  # {"role": "user", "parts": [...]}
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)  # 产出物
    metadata: Dict[str, Any] = Field(default_factory=dict)
    createdAt: str = ""
    updatedAt: str = ""

    @property
    def result_text(self) -> str:
        """提取首个文本 artifact 作为结果（供调用方直接展示）"""
        for art in self.artifacts:
            if isinstance(art, dict):
                if art.get("type") == "text":
                    return str(art.get("data", ""))
                if "data" in art:
                    return str(art["data"])
        return ""


# ─────────────────── JSON-RPC 2.0 ───────────────────

class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Dict[str, Any] = Field(default_factory=dict)
    id: Optional[Any] = None


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None


# ─────────────────── 参数模型 ───────────────────

def build_user_message(text: str) -> Dict[str, Any]:
    """构造 A2A 标准 user 消息（{role, parts:[{type, text}]}）"""
    return {"role": "user", "parts": [{"type": "text", "text": text}]}


def send_task_params(message: str, session_id: str = "") -> Dict[str, Any]:
    """tasks/send 参数"""
    return {"message": build_user_message(message), "sessionId": session_id}
