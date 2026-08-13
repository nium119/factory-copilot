"""A2A 协议定义 — 对齐 A2A 协议 v0.3.0 标准

实现 FC 需要的核心子集：
- Agent Card 发现：GET /.well-known/agent-card.json
- Task 生命周期：submitted / working / input-required / completed / failed / canceled
- 核心操作：tasks/send、tasks/get、tasks/cancel、tasks/sendSubscribe（SSE 流式）
- 流式事件：TaskStatusUpdateEvent / TaskArtifactUpdateEvent

传输为 JSON-RPC 2.0 over HTTP/SSE。
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ─────────────────── Agent Card ───────────────────


class AgentCapabilities(BaseModel):
    """Agent 能力声明（协议特性协商）"""
    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False
    extensions: List[str] = Field(default_factory=list)


class AgentSkill(BaseModel):
    """Agent Card 中的能力项"""
    id: str
    name: str
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    examples: List[str] = Field(default_factory=list)
    inputModes: List[str] = Field(default_factory=list)
    outputModes: List[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    """A2A v0.3.0 Agent Card（能力清单）"""
    name: str
    description: str = ""
    url: str = ""
    version: str = "1.0.0"
    protocolVersion: str = "0.3.0"
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    defaultInputModes: List[str] = Field(default_factory=lambda: ["text"])
    defaultOutputModes: List[str] = Field(default_factory=lambda: ["text"])
    skills: List[AgentSkill] = Field(default_factory=list)
    securitySchemes: Dict[str, Any] = Field(default_factory=dict)
    security: Optional[List[Dict[str, Any]]] = None
    preferredTransport: str = "JSONRPC"
    # 端点映射（A2A 扩展字段，客户端据此发现端点路径；缺失时回退固定路径）
    endpoints: Dict[str, str] = Field(default_factory=dict)


# ─────────────────── Message / Part / Artifact ───────────────────


class Part(BaseModel):
    """A2A Part（kind 判别：text / file / data）"""
    kind: str = "text"
    text: str = ""
    file: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """A2A Message（role + parts）"""
    role: str = "user"
    parts: List[Part] = Field(default_factory=list)
    messageId: Optional[str] = None
    taskId: Optional[str] = None
    contextId: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Artifact(BaseModel):
    """A2A Artifact（产物，由 Part 组成）"""
    name: str = ""
    description: str = ""
    parts: List[Part] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ─────────────────── Task ───────────────────


class TaskStatus(str, Enum):
    """A2A Task 生命周期状态"""
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskState(BaseModel):
    """A2A TaskState（状态 + 可选关联消息 + 时间戳）"""
    state: TaskStatus = TaskStatus.SUBMITTED
    message: Optional[Message] = None
    timestamp: str = ""


class Task(BaseModel):
    """A2A Task 对象（v0.3.0：contextId + TaskState + Artifact）"""
    id: str
    contextId: str = ""
    status: TaskState = Field(default_factory=TaskState)
    artifacts: List[Artifact] = Field(default_factory=list)
    history: List[TaskState] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def result_text(self) -> str:
        """提取文本结果（首个 text Part 的文本），供调用方直接展示"""
        for art in self.artifacts:
            for p in art.parts:
                if p.kind == "text" and p.text:
                    return p.text
        return ""


# ─────────────────── 事件（SSE 流） ───────────────────


class TaskStatusUpdateEvent(BaseModel):
    """A2A 任务状态更新事件（SSE 流）"""
    kind: str = "status-update"
    taskId: str = ""
    contextId: str = ""
    status: TaskState = Field(default_factory=TaskState)
    final: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskArtifactUpdateEvent(BaseModel):
    """A2A 任务产物更新事件（SSE 流）"""
    kind: str = "artifact-update"
    taskId: str = ""
    contextId: str = ""
    artifact: Optional[Artifact] = None
    append: bool = False
    lastChunk: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


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


# ─────────────────── 参数构造 ───────────────────


def build_user_message(text: str) -> Dict[str, Any]:
    """构造 A2A 标准 user 消息（{role, parts:[{kind:"text", text}]}）"""
    return {"role": "user", "parts": [{"kind": "text", "text": text}]}


def send_task_params(message: str, context_id: str = "") -> Dict[str, Any]:
    """tasks/send 参数（v0.3.0 用 contextId）"""
    return {"message": build_user_message(message), "contextId": context_id}
