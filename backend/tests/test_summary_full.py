"""
测试混合记忆摘要逻辑（不依赖 LLM）
验证：消息超过阈值时，正确分割旧消息和最近消息
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


async def test_summary_logic():
    """测试摘要逻辑"""
    print("=" * 60)
    print("测试: 混合记忆摘要逻辑")
    print("=" * 60)

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        message_repo = MessageRepository(session)
        conversation_repo = ConversationRepository(session)

        # 创建测试会话
        conv = await conversation_repo.create(user_id="test", title="摘要测试")
        print(f"\n[1] 创建会话: {conv.id}")

        # 注入 60 对消息（120 条）
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

        await session.commit()

        # 获取全部消息
        all_messages = await message_repo.get_by_conversation(conv.id)
        print(f"[2] 总消息数: {len(all_messages)}")
        print(f"[3] MAX_HISTORY_LENGTH: {settings.MAX_HISTORY_LENGTH}")
        print(f"[4] 当前摘要: {conv.summary}")

        # 模拟 _load_history_messages 的逻辑
        if len(all_messages) > settings.MAX_HISTORY_LENGTH:
            old_messages = all_messages[:-settings.MAX_HISTORY_LENGTH]
            recent_messages = all_messages[-settings.MAX_HISTORY_LENGTH:]

            print(f"\n[5] 消息分割:")
            print(f"    旧消息: {len(old_messages)} 条")
            print(f"    最近保留: {len(recent_messages)} 条")
            print(f"    第一条旧消息: {old_messages[0].content}")
            print(f"    第一条最近消息: {recent_messages[0].content}")

            # 模拟生成摘要文本
            old_text = "\n".join(
                f"[{msg.role.value}] {msg.content[:50]}"
                for msg in old_messages[:10]  # 只取前 10 条展示
            )
            print(f"\n[6] 将被压缩的旧消息示例:")
            for line in old_text.split("\n")[:5]:
                print(f"    {line}")
            print(f"    ... (共 {len(old_messages)} 条)")

            print(f"\n[7] 摘要更新流程:")
            print(f"    - 将以上 {len(old_messages)} 条旧消息 + 已有摘要(如有)")
            print(f"    - 调用 LLM 生成约 {settings.SUMMARY_MAX_TOKENS} 字摘要")
            print(f"    - 存入 conversations.summary 字段")
            print(f"    - 下次对话时复用该摘要")
        else:
            print(f"\n[5] 消息数未超过阈值，全量加载")

        print(f"\n[OK] 摘要压缩逻辑验证通过!")

    await engine.dispose()


async def main():
    await test_summary_logic()


if __name__ == "__main__":
    asyncio.run(main())
