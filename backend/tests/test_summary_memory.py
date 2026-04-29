"""
测试混合记忆策略 - 摘要压缩功能
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

TEST_DB = "sqlite+aiosqlite:///./data/test_summary_memory.db"


@pytest_asyncio.fixture
async def db_engine():
    """创建测试数据库引擎并建表"""
    engine = create_async_engine(TEST_DB, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
    if os.path.exists("./data/test_summary_memory.db"):
        os.remove("./data/test_summary_memory.db")


@pytest_asyncio.fixture
async def db_session(db_engine):
    """创建测试数据库会话"""
    async_session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest.mark.asyncio
async def test_summary_compression_logic(db_session):
    """测试摘要压缩逻辑：消息超过阈值时正确分割"""
    message_repo = MessageRepository(db_session)
    conversation_repo = ConversationRepository(db_session)

    # 创建测试会话
    conv = await conversation_repo.create(user_id="test_user", title="测试摘要")
    assert conv.id is not None

    # 添加 60 对消息 (120 条)，超过 MAX_HISTORY_LENGTH=50
    for i in range(60):
        await message_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content=f"用户消息 {i+1}: 这是一个测试内容"
        )
        await message_repo.create(
            conversation_id=conv.id,
            role=MessageRole.ASSISTANT,
            content=f"AI 回复 {i+1}: 这是对测试内容的回复"
        )

    # 获取全部消息，验证数量
    all_messages = await message_repo.get_by_conversation(conv.id)
    assert len(all_messages) == 120

    # 验证会话摘要字段
    conv_loaded = await conversation_repo.get_by_id(conv.id)
    assert conv_loaded is not None

    # 模拟 _load_history_messages 的分割逻辑
    old_messages = all_messages[:-settings.MAX_HISTORY_LENGTH]
    recent_messages = all_messages[-settings.MAX_HISTORY_LENGTH:]

    assert len(old_messages) > 0
    assert len(recent_messages) == settings.MAX_HISTORY_LENGTH
    assert len(old_messages) + len(recent_messages) == 120

    # 验证第一条旧消息和第一条最近消息的内容正确
    assert "用户消息 1" in old_messages[0].content
    # 120 条消息，保留 50 条 → old=70 条(35对), recent 从第 36 个 USER 开始
    assert "用户消息 36" in recent_messages[0].content


@pytest.mark.asyncio
async def test_no_summary_when_under_threshold(db_session):
    """测试消息数未超过阈值时不触发摘要"""
    message_repo = MessageRepository(db_session)
    conversation_repo = ConversationRepository(db_session)

    conv = await conversation_repo.create(user_id="test_user", title="测试不压缩")
    assert conv.id is not None

    # 只添加 10 条消息 (远低于 MAX_HISTORY_LENGTH)
    for i in range(10):
        await message_repo.create(
            conversation_id=conv.id,
            role=MessageRole.USER,
            content=f"消息 {i+1}"
        )

    all_messages = await message_repo.get_by_conversation(conv.id)
    assert len(all_messages) == 10

    # 消息数未超过阈值，应全量加载
    assert len(all_messages) <= settings.MAX_HISTORY_LENGTH
