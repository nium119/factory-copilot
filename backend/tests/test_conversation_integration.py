"""
集成测试
测试会话管理、消息处理和长期记忆功能
"""
import pytest
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.services.conversation_service import ConversationService
from app.services.vector_memory_service import vector_memory_service
from app.core.config import settings


# 测试数据库URL
TEST_DB_URL = "sqlite+aiosqlite:///./test.db"


@pytest.fixture
async def db_session():
    """创建测试数据库会话"""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        yield session


@pytest.fixture
async def conversation_service(db_session):
    """创建会话服务"""
    conversation_repo = ConversationRepository(db_session)
    message_repo = MessageRepository(db_session)
    return ConversationService(conversation_repo, message_repo)


@pytest.mark.asyncio
async def test_create_conversation(conversation_service):
    """测试创建会话"""
    user_id = "test_user"

    # 创建会话
    conversation = await conversation_service.create(
        user_id=user_id,
        title="测试会话"
    )

    assert conversation is not None
    assert conversation.user_id == user_id
    assert conversation.title == "测试会话"
    assert conversation.message_count == 0
    assert conversation.is_active is True


@pytest.mark.asyncio
async def test_get_conversation_list(conversation_service):
    """测试获取会话列表"""
    user_id = "test_user"

    # 创建多个会话
    for i in range(3):
        await conversation_service.create(
            user_id=user_id,
            title=f"测试会话{i}"
        )

    # 获取会话列表
    conversations, total = await conversation_service.get_list(
        user_id=user_id,
        page=1,
        page_size=10
    )

    assert len(conversations) == 3
    assert total == 3


@pytest.mark.asyncio
async def test_update_conversation_title(conversation_service):
    """测试更新会话标题"""
    user_id = "test_user"

    # 创建会话
    conversation = await conversation_service.create(
        user_id=user_id,
        title="原标题"
    )

    # 更新标题
    updated = await conversation_service.update_title(
        conversation_id=str(conversation.id),
        title="新标题"
    )

    assert updated is not None
    assert updated.title == "新标题"


@pytest.mark.asyncio
async def test_delete_conversation(conversation_service):
    """测试删除会话"""
    user_id = "test_user"

    # 创建会话
    conversation = await conversation_service.create(
        user_id=user_id,
        title="待删除会话"
    )

    # 删除会话
    deleted = await conversation_service.delete(str(conversation.id))

    assert deleted is True

    # 验证已删除
    result = await conversation_service.get_by_id(str(conversation.id))
    assert result is None


@pytest.mark.asyncio
async def test_user_data_isolation(conversation_service):
    """测试用户数据隔离"""
    user1 = "user1"
    user2 = "user2"

    # 为user1创建会话
    conv1 = await conversation_service.create(
        user_id=user1,
        title="User1的会话"
    )

    # 为user2创建会话
    conv2 = await conversation_service.create(
        user_id=user2,
        title="User2的会话"
    )

    # user1只能看到自己的会话
    conversations1, total1 = await conversation_service.get_list(
        user_id=user1,
        page=1,
        page_size=10
    )

    assert total1 == 1
    assert conversations1[0].user_id == user1

    # user2只能看到自己的会话
    conversations2, total2 = await conversation_service.get_list(
        user_id=user2,
        page=1,
        page_size=10
    )

    assert total2 == 1
    assert conversations2[0].user_id == user2


@pytest.mark.asyncio
async def test_auto_generate_title(conversation_service, db_session):
    """测试自动生成标题"""
    user_id = "test_user"

    # 创建会话
    conversation = await conversation_service.create(user_id=user_id)

    # 添加消息
    message_repo = MessageRepository(db_session)
    await message_repo.create(
        conversation_id=str(conversation.id),
        role=MessageRole.USER,
        content="这是一条测试消息,用于自动生成标题"
    )

    # 自动生成标题
    title = await conversation_service.auto_generate_title(str(conversation.id))

    assert title is not None
    assert len(title) <= 23  # 20字符 + "..."


@pytest.mark.asyncio
async def test_memory_retrieval():
    """测试长期记忆检索"""
    # 跳过如果Milvus未启用
    if not settings.MILVUS_ENABLED:
        pytest.skip("Milvus未启用")

    # 初始化向量记忆服务
    await vector_memory_service.initialize()

    if not vector_memory_service._initialized:
        pytest.skip("VectorMemoryService未初始化")

    user_id = "test_user"
    conversation_id = "test_conv"
    message_id = "test_msg"

    # 存储向量
    vector_id = await vector_memory_service.store(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        content="这是一条测试消息",
        role="user"
    )

    assert vector_id is not None

    # 检索记忆
    memories = await vector_memory_service.retrieve(
        user_id=user_id,
        query="测试消息",
        top_k=5
    )

    assert len(memories) > 0

    # 清理
    await vector_memory_service.delete_by_conversation(conversation_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
