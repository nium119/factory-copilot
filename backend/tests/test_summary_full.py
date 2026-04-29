"""
测试混合记忆摘要逻辑（不依赖 LLM）
验证：消息超过阈值时，正确分割旧消息和最近消息
"""
import pytest
import pytest_asyncio
import os

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.repositories.message_repository import MessageRepository
from app.repositories.conversation_repository import ConversationRepository

# 注册所有模型
import app.models  # noqa: F401

TEST_DB = "sqlite+aiosqlite:///./data/test_summary_full.db"


@pytest_asyncio.fixture
async def db_engine():
    """创建测试数据库引擎并建表"""
    engine = create_async_engine(TEST_DB, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
    # 清理测试数据库
    if os.path.exists("./data/test_summary_full.db"):
        os.remove("./data/test_summary_full.db")


@pytest_asyncio.fixture
async def db_session(db_engine):
    """创建测试数据库会话"""
    async_session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest.mark.asyncio
async def test_summary_logic(db_session):
    """测试摘要逻辑：消息超过阈值时正确分割"""
    message_repo = MessageRepository(db_session)
    conversation_repo = ConversationRepository(db_session)

    # 创建测试会话
    conv = await conversation_repo.create(user_id="test", title="摘要测试")
    assert conv.id is not None

    # 注入 60 对消息 (120 条)
    for i in range(60):
        await message_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content=f"用户问题 {i+1}: 这是第 {i+1} 轮测试内容"
        )
        await message_repo.create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content=f"AI 回复 {i+1}: 对应第 {i+1} 轮的回答"
        )

    # 获取全部消息
    all_messages = await message_repo.get_by_conversation(conv.id)
    assert len(all_messages) == 120

    # 模拟 _load_history_messages 的逻辑
    assert len(all_messages) > settings.MAX_HISTORY_LENGTH
    old_messages = all_messages[:-settings.MAX_HISTORY_LENGTH]
    recent_messages = all_messages[-settings.MAX_HISTORY_LENGTH:]

    assert len(old_messages) > 0
    assert len(recent_messages) == settings.MAX_HISTORY_LENGTH
    assert len(old_messages) + len(recent_messages) == 120
