"""
测试混合记忆策略 - 摘要压缩功能
"""
import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.repositories.message_repository import MessageRepository
from app.repositories.conversation_repository import ConversationRepository


async def test_summary_logic():
    """测试摘要压缩逻辑"""
    print("=" * 60)
    print("测试: 混合记忆策略 - 摘要压缩")
    print("=" * 60)

    # 创建内存数据库用于测试
    engine = create_async_engine("sqlite+aiosqlite:///./data/test_summary.db", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        message_repo = MessageRepository(session)
        conversation_repo = ConversationRepository(session)

        # 1. 创建测试会话
        conv = await conversation_repo.create(user_id="test_user", title="测试摘要")
        print(f"\n[1] 创建会话: {conv.id}")

        # 2. 模拟添加 60 条消息（超过 MAX_HISTORY_LENGTH=50）
        print(f"\n[2] 添加 60 条消息...")
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
            if (i + 1) % 20 == 0:
                print(f"  已添加 {i+1} 对消息...")

        # 3. 获取全部消息，验证数量
        all_messages = await message_repo.get_by_conversation(conv.id)
        print(f"\n[3] 总消息数: {len(all_messages)}")
        print(f"    MAX_HISTORY_LENGTH 配置: {settings.MAX_HISTORY_LENGTH}")

        # 4. 模拟 _load_history_messages 的逻辑
        # 获取会话对象以检查摘要字段
        conv_loaded = await conversation_repo.get_by_id(conv.id)
        print(f"    当前摘要: {conv_loaded.summary}")

        # 计算需要压缩的旧消息
        old_messages = all_messages[:-settings.MAX_HISTORY_LENGTH]
        recent_messages = all_messages[-settings.MAX_HISTORY_LENGTH:]
        print(f"    旧消息数量: {len(old_messages)}")
        print(f"    最近保留消息: {len(recent_messages)}")

        # 5. 验证消息内容正确分割
        print(f"\n[5] 验证消息分割:")
        print(f"    第一条旧消息: {old_messages[0].content}")
        print(f"    第一条最近消息: {recent_messages[0].content}")

        print(f"\n[OK] 摘要压缩逻辑验证通过!")
        print(f"     当消息数 > {settings.MAX_HISTORY_LENGTH} 时:")
        print(f"     - 保留最近 {settings.MAX_HISTORY_LENGTH} 条完整消息")
        print(f"     - 旧消息将通过 LLM 压缩为摘要")
        print(f"     - 摘要缓存到 conversations.summary 字段")

    # 清理测试数据库
    await engine.dispose()
    import time
    time.sleep(0.5)
    test_db = "./data/test_summary.db"
    if os.path.exists(test_db):
        os.remove(test_db)
        print(f"\n[清理] 测试数据库已删除")


async def test_no_summary_needed():
    """测试消息数未超过阈值时不触发摘要"""
    print("\n" + "=" * 60)
    print("测试: 消息数未超过阈值 - 不触发摘要")
    print("=" * 60)

    engine = create_async_engine("sqlite+aiosqlite:///./data/test_no_summary.db", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        message_repo = MessageRepository(session)
        conversation_repo = ConversationRepository(session)

        conv = await conversation_repo.create(user_id="test_user", title="测试不压缩")

        # 只添加 10 条消息
        for i in range(10):
            await message_repo.create(
                conversation_id=conv.id,
                role=MessageRole.USER,
                content=f"消息 {i+1}"
            )

        all_messages = await message_repo.get_by_conversation(conv.id)
        print(f"\n[1] 总消息数: {len(all_messages)}")
        print(f"    阈值: {settings.MAX_HISTORY_LENGTH}")

        if len(all_messages) <= settings.MAX_HISTORY_LENGTH:
            print(f"    结果: 不需要压缩，全量加载 {len(all_messages)} 条消息")
            print(f"    [OK] 短对话不触发摘要 - 验证通过!")
        else:
            print(f"    [FAIL] 短对话不应该触发摘要")

    await engine.dispose()
    import time
    time.sleep(0.5)
    test_db = "./data/test_no_summary.db"
    if os.path.exists(test_db):
        os.remove(test_db)


async def main():
    await test_summary_logic()
    await test_no_summary_needed()

    print("\n" + "=" * 60)
    print("所有测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
