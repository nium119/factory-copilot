"""
Pydantic 请求/响应模型定义
用于 FastAPI 接口数据校验和 Swagger 文档展示
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    """消息角色枚举"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    """
    聊天对话请求体

    用于发起单次对话请求，支持指定模型、深度思考、联网搜索等参数。
    """
    content: str = Field(..., description="用户输入内容")
    role: MessageRole = Field(default=MessageRole.USER, description="消息角色")
    session_id: Optional[str] = Field(None, description="会话标识，不传则使用默认会话")
    model_name: Optional[str] = Field(None, description="指定使用的 AI 模型名称")
    agent_name: Optional[str] = Field(None, description="指定 Agent 名称，不传则使用通用助手，传 'auto' 则自动路由")
    use_agent: bool = Field(default=False, description="是否启用 Agent 模式（协作模式）")
    web_search: bool = Field(default=False, description="是否启用联网搜索功能")
    enable_thinking: bool = Field(default=False, description="是否启用深度思考")


class AgentResponse(BaseModel):
    """
    Agent 对话响应

    包含 AI 回复内容、会话标识和状态信息。
    """
    response: str = Field(..., description="AI 回复内容")
    session_id: str = Field(..., description="会话标识")
    status: str = Field(default="success", description="请求状态，success 表示成功")
    metadata: Optional[Dict[str, Any]] = Field(None, description="附加元数据")


class SessionInfo(BaseModel):
    """
    会话信息

    包含会话的基本统计信息和时间戳。
    """
    session_id: str = Field(..., description="会话标识")
    user_id: Optional[str] = Field(None, description="用户标识")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="最后更新时间")
    message_count: int = Field(default=0, description="会话内的消息数量")


class HealthCheckResponse(BaseModel):
    """
    健康检查响应

    返回服务运行状态、版本号和当前时间戳。
    """
    status: str = Field(..., description="服务运行状态，healthy 表示正常")
    version: str = Field(..., description="应用版本号")
    timestamp: datetime = Field(default_factory=datetime.now, description="响应时间戳")


class ErrorResponse(BaseModel):
    """
    错误响应

    接口出错时统一返回的错误信息格式。
    """
    error: str = Field(..., description="错误类型描述")
    detail: Optional[Dict[str, Any]] = Field(None, description="详细错误信息")
    timestamp: datetime = Field(default_factory=datetime.now, description="错误发生时间")


# ============================================
# 会话管理相关模型
# ============================================


class ConversationCreate(BaseModel):
    """
    创建会话请求体

    创建一个新对话会话，标题可选，不传则由系统自动生成。
    """
    title: Optional[str] = Field(None, description="会话标题，不传则自动生成", max_length=255)
    metadata: Optional[Dict[str, Any]] = Field(None, description="扩展元数据")


class ConversationUpdate(BaseModel):
    """
    更新会话请求体

    支持修改会话标题、激活状态和元数据。
    """
    title: Optional[str] = Field(None, description="新标题")
    is_active: Optional[bool] = Field(None, description="是否激活，设为 False 可隐藏会话")
    metadata: Optional[Dict[str, Any]] = Field(None, description="更新后的元数据")


class ConversationResponse(BaseModel):
    """
    会话响应

    返回会话的完整信息，包括 ID、标题、消息数量、创建时间等。
    """
    id: str = Field(..., description="会话唯一标识（UUID）")
    user_id: str = Field(..., description="所属用户标识")
    title: Optional[str] = Field(None, description="会话标题")
    message_count: int = Field(default=0, description="会话内的消息总数")
    is_active: bool = Field(default=True, description="是否激活")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="最后更新时间")
    metadata: Optional[Dict[str, Any]] = Field(None, description="扩展元数据")

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    """
    会话列表响应

    分页返回用户的会话列表，包含总数、当前页和每页数量。
    """
    conversations: List[ConversationResponse] = Field(..., description="会话列表")
    total: int = Field(..., description="符合条件的会话总数")
    page: int = Field(..., description="当前页码，从 1 开始")
    page_size: int = Field(..., description="每页返回的会话数量")


# ============================================
# 消息相关模型
# ============================================


class MessageCreate(BaseModel):
    """
    创建消息请求体

    向指定会话中追加一条新消息。
    """
    conversation_id: str = Field(..., description="所属会话 ID")
    role: MessageRole = Field(..., description="消息角色：user / assistant / system")
    content: str = Field(..., description="消息文本内容")
    metadata: Optional[Dict[str, Any]] = Field(None, description="扩展元数据")


class MessageResponse(BaseModel):
    """
    消息响应

    返回消息的完整信息，包括 ID、所属会话、角色、内容和创建时间。
    """
    id: str = Field(..., description="消息唯一标识（UUID）")
    conversation_id: str = Field(..., description="所属会话 ID")
    role: MessageRole = Field(..., description="消息角色")
    content: str = Field(..., description="消息文本内容")
    created_at: datetime = Field(..., description="创建时间")
    metadata: Optional[Dict[str, Any]] = Field(None, description="扩展元数据")

    class Config:
        from_attributes = True


class MessageListResponse(BaseModel):
    """
    消息列表响应

    返回指定会话的消息列表及总数。
    """
    messages: List[MessageResponse] = Field(..., description="消息列表，按时间升序")
    total: int = Field(..., description="消息总数")


# ============================================
# 记忆管理相关模型
# ============================================


class MemoryRetrieveRequest(BaseModel):
    """
    检索记忆请求体

    基于向量语义相似度检索相关的历史记忆。
    """
    query: str = Field(..., description="用于检索的查询文本")
    conversation_id: Optional[str] = Field(None, description="限定检索范围到指定会话（可选）")
    top_k: int = Field(default=5, description="最多返回的记忆数量")
    similarity_threshold: float = Field(default=0.7, description="最低相似度阈值，低于此值的结果将被过滤")


class MemoryItem(BaseModel):
    """
    记忆项

    单条检索到的记忆内容，包含原始消息文本和相似度评分。
    """
    id: str = Field(..., description="记忆唯一标识")
    content: str = Field(..., description="记忆内容（原始消息文本）")
    role: str = Field(..., description="消息角色")
    conversation_id: str = Field(..., description="所属会话 ID")
    similarity: float = Field(..., description="与查询文本的语义相似度（0~1）")
    created_at: datetime = Field(..., description="记忆创建时间")


class MemoryRetrieveResponse(BaseModel):
    """
    检索记忆响应

    返回匹配的记忆列表及总数。
    """
    memories: List[MemoryItem] = Field(..., description="检索到的记忆列表")
    total: int = Field(..., description="匹配的记忆总数")


class MemoryConfig(BaseModel):
    """
    记忆配置

    控制长期记忆的启用状态、检索数量和自动注入行为。
    """
    enabled: bool = Field(default=True, description="是否启用长期记忆功能")
    top_k: int = Field(default=5, description="每次检索最多返回的记忆数量")
    similarity_threshold: float = Field(default=0.7, description="检索相似度阈值")
    auto_inject: bool = Field(default=True, description="是否自动将检索到的记忆注入到对话上下文中")
