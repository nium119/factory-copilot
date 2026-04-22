"""
端到端测试：混合记忆摘要压缩
1. 向数据库注入 60 条消息（模拟长对话）
2. 通过 API 发一条新消息，触发摘要压缩
3. 验证 summary 字段是否生成
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


async def setup_long_conversation():
    """在数据库中创建一个有 60 条消息的长对话"""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        message_repo = MessageRepository(session)
        conversation_repo = ConversationRepository(session)

        # 创建会话
        conv = await conversation_repo.create(user_id="test_user", title="长对话测试")
        print(f"[1] 创建会话: {conv.id}")

        # 注入 60 对消息（120 条）
        for i in range(60):
            await message_repo.create(
                conversation_id=conv.id,
                role=MessageRole.USER,
                content=f"这是第 {i+1} 轮用户提问：测试内容 {i+1}"
            )
            await message_repo.create(
                conversation_id=conv.id,
                role=MessageRole.ASSISTANT,
                content=f"这是第 {i+1} 轮 AI 回复：对应回复 {i+1}"
            )

        await conversation_repo.increment_message_count(conv.id)
        await session.commit()

        # 验证消息数量
        all_messages = await message_repo.get_by_conversation(conv.id)
        print(f"[2] 注入完成: {len(all_messages)} 条消息")
        print(f"[3] 摘要字段: {conv.summary}")
        print(f"    MAX_HISTORY_LENGTH 配置: {settings.MAX_HISTORY_LENGTH}")
        print(f"\n[4] 现在通过 API 发送一条新消息，触发摘要压缩...")
        print(f"    会话ID: {conv.id}")
        return conv.id


async def main():
    print("=" * 60)
    print("端到端测试：混合记忆摘要压缩")
    print("=" * 60)
    print()

    conversation_id = await setup_long_conversation()

    print("\n[5] 请在浏览器打开 http://localhost:3000")
    print(f"    找到会话 \"{conversation_id}\" 并发送一条消息")
    print("    或直接通过 curl 测试:")
    print(f"    curl -X POST http://127.0.0.1:3000/api/messages/stream \\")
    print(f"      -H 'Content-Type: application/json' \\")
    print(f"      -d '{{\"conversation_id\":\"{conversation_id}\",\"content\":\"请总结一下之前的对话\"}}'")
    print()
    print("    发完后运行: python tests/check_summary.py {conversation_id}")


if __name__ == "__main__":
    asyncio.run(main())
