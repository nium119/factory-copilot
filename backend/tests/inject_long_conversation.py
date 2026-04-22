"""
向主数据库注入 60 条消息的长对话，用于测试摘要压缩
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.repositories.message_repository import MessageRepository
from app.repositories.conversation_repository import ConversationRepository


async def main():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        message_repo = MessageRepository(session)
        conversation_repo = ConversationRepository(session)

        # 创建测试会话
        conv = await conversation_repo.create(user_id="test_user", title="长对话测试-摘要压缩")
        print(f"创建会话: {conv.id}")

        # 注入 60 对消息（120 条）
        for i in range(60):
            await message_repo.create(
                conversation_id=conv.id,
                role=MessageRole.USER,
                content=f"用户问题 {i+1}: 这是第 {i+1} 轮测试内容，用于测试摘要压缩功能"
            )
            await message_repo.create(
                conversation_id=conv.id,
                role=MessageRole.ASSISTANT,
                content=f"AI 回复 {i+1}: 对应第 {i+1} 轮的回答内容，包含相关知识和建议"
            )
            if (i + 1) % 20 == 0:
                print(f"  已注入 {(i+1)*2} 条消息...")

        await session.commit()

        all_messages = await message_repo.get_by_conversation(conv.id)
        print(f"总消息数: {len(all_messages)}")
        print(f"会话ID: {conv.id}")
        print(f"\n现在可以通过 API 发一条消息触发摘要压缩:")
        print(f"curl -X POST http://127.0.0.1:8001/api/messages/stream \\")
        print(f"  -H 'Content-Type: application/json' \\")
        print(f"  -d '{{\"conversation_id\":\"{conv.id}\",\"content\":\"你好\",\"model_name\":\"qwen3.6-plus\"}}'")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
